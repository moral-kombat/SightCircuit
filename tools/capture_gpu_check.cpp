// Query physical-device capabilities only. Use the Python supervisor for a deadline.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <algorithm>
#include <cstring>
#include <dlfcn.h>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <signal.h>
#include <unistd.h>
#include <vector>

namespace {
using Fields = std::vector<std::pair<std::string, std::string>>;
constexpr uint64_t modifier = 0x0500000000000001ULL;
constexpr VkFormat camera_format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM;
const std::vector<std::string> required_extensions = {
    "VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
    "VK_EXT_image_drm_format_modifier", "VK_EXT_queue_family_foreign"};

std::string quote(const std::string &value) {
    std::string out = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32 || c >= 127) {
            const char *hex = "0123456789abcdef";
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += static_cast<char>(c);
    }
    return out + '"';
}
std::string boolean(bool value) { return value ? "true" : "false"; }
std::string object(const Fields &fields) {
    std::string out = "{";
    for (const auto &field : fields) {
        if (out.size() > 1) out += ',';
        out += quote(field.first) + ':' + field.second;
    }
    return out + '}';
}
std::string array(const std::vector<std::string> &values) {
    std::string out = "[";
    for (const auto &value : values) { if (out.size() > 1) out += ','; out += value; }
    return out + ']';
}
std::string version(uint32_t value) {
    return object({{"raw", std::to_string(value)}, {"major", std::to_string(VK_VERSION_MAJOR(value))},
                   {"minor", std::to_string(VK_VERSION_MINOR(value))},
                   {"patch", std::to_string(VK_VERSION_PATCH(value))}});
}
void check(VkResult result, const char *operation) {
    if (result != VK_SUCCESS)
        throw std::runtime_error(std::string(operation) + ": VkResult " + std::to_string(result));
}
void bound(uint32_t count, uint32_t maximum, const char *name) {
    if (count > maximum) throw std::runtime_error(std::string(name) + " count exceeds limit");
}
template <size_t N> std::string bounded_string(const char (&value)[N]) {
    return std::string(value, strnlen(value, N));
}

struct Runtime {
    void *library = nullptr;
    VkInstance instance = VK_NULL_HANDLE;
    PFN_vkGetInstanceProcAddr get = nullptr;
    PFN_vkDestroyInstance destroy = nullptr;
    bool &destroyed;
    explicit Runtime(bool &value) : destroyed(value) {}
    ~Runtime() {
        if (instance && destroy) { destroy(instance, nullptr); destroyed = true; }
        if (library) dlclose(library);
    }
    template <typename T> T function(const char *name) {
        auto result = reinterpret_cast<T>(get(instance, name));
        if (!result) throw std::runtime_error(std::string("missing Vulkan entry point: ") + name);
        return result;
    }
};

std::string query_device(Runtime &runtime, VkPhysicalDevice device) {
    auto properties_fn = runtime.function<PFN_vkGetPhysicalDeviceProperties>("vkGetPhysicalDeviceProperties");
    VkPhysicalDeviceProperties properties{};
    properties_fn(device, &properties);
    Fields fields{{"name", quote(bounded_string(properties.deviceName))},
                  {"device_type", std::to_string(properties.deviceType)},
                  {"vendor_id", std::to_string(properties.vendorID)},
                  {"device_id", std::to_string(properties.deviceID)},
                  {"api_version", version(properties.apiVersion)},
                  {"driver_version_raw", std::to_string(properties.driverVersion)}};
    // Version 1.1 supplies the core *2 queries. The reviewed importer requests 1.3.
    if (properties.apiVersion < VK_API_VERSION_1_1) {
        fields.emplace_back("status", quote("api_below_1_1"));
        fields.emplace_back("capability_checks_passed", "false");
        return object(fields);
    }
    auto extensions_fn = runtime.function<PFN_vkEnumerateDeviceExtensionProperties>("vkEnumerateDeviceExtensionProperties");
    uint32_t count = 0;
    check(extensions_fn(device, nullptr, &count, nullptr), "extension count");
    bound(count, 4096, "extension");
    std::vector<VkExtensionProperties> extensions(count);
    if (count) check(extensions_fn(device, nullptr, &count, extensions.data()), "extensions");
    bound(count, static_cast<uint32_t>(extensions.size()), "returned extension");
    std::vector<std::string> present, missing;
    for (const auto &required : required_extensions) {
        bool found = std::any_of(extensions.begin(), extensions.begin() + count,
            [&](const auto &value) { return bounded_string(value.extensionName) == required; });
        (found ? present : missing).push_back(quote(required));
    }
    fields.emplace_back("required_extensions_present", array(present));
    fields.emplace_back("required_extensions_missing", array(missing));

    auto features_fn = runtime.function<PFN_vkGetPhysicalDeviceFeatures2>("vkGetPhysicalDeviceFeatures2");
    VkPhysicalDeviceSamplerYcbcrConversionFeatures ycbcr{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES};
    VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &ycbcr};
    features_fn(device, &features);
    fields.emplace_back("sampler_ycbcr_conversion", boolean(ycbcr.samplerYcbcrConversion));

    auto memory_fn = runtime.function<PFN_vkGetPhysicalDeviceMemoryProperties>("vkGetPhysicalDeviceMemoryProperties");
    VkPhysicalDeviceMemoryProperties memory{};
    memory_fn(device, &memory);
    bound(memory.memoryTypeCount, VK_MAX_MEMORY_TYPES, "memory type");
    bound(memory.memoryHeapCount, VK_MAX_MEMORY_HEAPS, "memory heap");
    bool host_memory = false;
    std::vector<std::string> memory_reports;
    for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
        const auto &value = memory.memoryTypes[i];
        if (value.heapIndex >= memory.memoryHeapCount) throw std::runtime_error("invalid memory heap index");
        constexpr auto required = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
        host_memory |= (value.propertyFlags & required) == required;
        memory_reports.push_back(object({{"index", std::to_string(i)},
            {"heap_index", std::to_string(value.heapIndex)}, {"flags", std::to_string(value.propertyFlags)}}));
    }
    fields.emplace_back("memory_types", array(memory_reports));
    fields.emplace_back("host_visible_cached_memory", boolean(host_memory));

    auto queues_fn = runtime.function<PFN_vkGetPhysicalDeviceQueueFamilyProperties>("vkGetPhysicalDeviceQueueFamilyProperties");
    count = 0; queues_fn(device, &count, nullptr); bound(count, 256, "queue family");
    std::vector<VkQueueFamilyProperties> queues(count);
    if (count) queues_fn(device, &count, queues.data());
    bound(count, static_cast<uint32_t>(queues.size()), "returned queue family");
    bool suitable_queue = false;
    std::vector<std::string> queue_reports;
    for (uint32_t i = 0; i < count; ++i) {
        bool suitable = queues[i].queueCount && (queues[i].queueFlags & VK_QUEUE_GRAPHICS_BIT);
        suitable_queue |= suitable; // Linear blits require a graphics-capable family.
        queue_reports.push_back(object({{"index", std::to_string(i)},
            {"count", std::to_string(queues[i].queueCount)},
            {"flags", std::to_string(queues[i].queueFlags)}, {"supports_graphics", boolean(suitable)}}));
    }
    fields.emplace_back("queue_families", array(queue_reports));

    auto formats_fn = runtime.function<PFN_vkGetPhysicalDeviceFormatProperties2>("vkGetPhysicalDeviceFormatProperties2");
    VkFormatProperties2 r8{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2};
    formats_fn(device, VK_FORMAT_R8_UNORM, &r8);
    constexpr VkFormatFeatureFlags r8_required = VK_FORMAT_FEATURE_BLIT_SRC_BIT |
        VK_FORMAT_FEATURE_BLIT_DST_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT |
        VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
    bool r8_ok = (r8.formatProperties.optimalTilingFeatures & r8_required) == r8_required;
    fields.emplace_back("r8_optimal_features", std::to_string(r8.formatProperties.optimalTilingFeatures));
    fields.emplace_back("r8_linear_blit_features", boolean(r8_ok));

    auto image_fn = runtime.function<PFN_vkGetPhysicalDeviceImageFormatProperties2>("vkGetPhysicalDeviceImageFormatProperties2");
    VkPhysicalDeviceImageFormatInfo2 r8_info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
    r8_info.format = VK_FORMAT_R8_UNORM; r8_info.type = VK_IMAGE_TYPE_2D;
    r8_info.tiling = VK_IMAGE_TILING_OPTIMAL;
    r8_info.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    VkImageFormatProperties2 r8_limits{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
    VkResult r8_result = image_fn(device, &r8_info, &r8_limits);
    bool r8_extent = false;
    if (r8_result == VK_SUCCESS) {
        const auto &limits = r8_limits.imageFormatProperties;
        r8_extent = limits.maxExtent.width >= 2464 && limits.maxExtent.height >= 2464 &&
            limits.maxExtent.depth >= 1 && limits.maxArrayLayers >= 1 && limits.maxMipLevels >= 1 &&
            (limits.sampleCounts & VK_SAMPLE_COUNT_1_BIT);
    } else if (r8_result != VK_ERROR_FORMAT_NOT_SUPPORTED) check(r8_result, "R8 image format");
    fields.emplace_back("r8_image", object({{"vk_result", std::to_string(r8_result)},
        {"max_width", std::to_string(r8_limits.imageFormatProperties.maxExtent.width)},
        {"max_height", std::to_string(r8_limits.imageFormatProperties.maxExtent.height)},
        {"reviewed_extent_and_usage_supported", boolean(r8_extent)}}));

    bool target_found = false, external_ok = false;
    uint32_t memory_planes = 0;
    std::vector<std::string> modifier_reports;
    if (std::find(present.begin(), present.end(), quote("VK_EXT_image_drm_format_modifier")) != present.end()) {
        VkDrmFormatModifierPropertiesListEXT list{VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT};
        VkFormatProperties2 format{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, &list};
        formats_fn(device, camera_format, &format);
        bound(list.drmFormatModifierCount, 4096, "DRM modifier");
        std::vector<VkDrmFormatModifierPropertiesEXT> modifiers(list.drmFormatModifierCount);
        if (!modifiers.empty()) {
            list.pDrmFormatModifierProperties = modifiers.data();
            formats_fn(device, camera_format, &format);
            bound(list.drmFormatModifierCount, static_cast<uint32_t>(modifiers.size()), "returned DRM modifier");
        }
        for (uint32_t i = 0; i < list.drmFormatModifierCount; ++i) {
            const auto &value = modifiers[i];
            if (value.drmFormatModifier != modifier) continue;
            target_found = true; memory_planes = value.drmFormatModifierPlaneCount;
            modifier_reports.push_back(object({{"modifier", quote("0x0500000000000001")},
                {"memory_plane_count", std::to_string(memory_planes)},
                {"tiling_features", std::to_string(value.drmFormatModifierTilingFeatures)}}));
        }
    }
    fields.emplace_back("nv12_target_modifiers", array(modifier_reports));
    if (target_found && missing.empty()) {
        VkPhysicalDeviceExternalImageFormatInfo external{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};
        external.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkPhysicalDeviceImageDrmFormatModifierInfoEXT drm{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT, &external};
        drm.drmFormatModifier = modifier; drm.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkPhysicalDeviceImageFormatInfo2 info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2, &drm};
        info.format = camera_format; info.type = VK_IMAGE_TYPE_2D;
        info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
        info.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
        VkExternalImageFormatProperties ext{VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
        VkImageFormatProperties2 output{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2, &ext};
        VkResult result = image_fn(device, &info, &output);
        Fields image_fields{{"vk_result", std::to_string(result)}};
        if (result == VK_SUCCESS) {
            const auto &value = ext.externalMemoryProperties;
            const auto &limits = output.imageFormatProperties;
            bool importable = value.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT;
            bool compatible = value.compatibleHandleTypes & VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
            external_ok = importable && compatible && limits.maxExtent.width >= 2464 &&
                limits.maxExtent.height >= 2464 && limits.maxExtent.depth >= 1 &&
                limits.maxArrayLayers >= 2 && limits.maxMipLevels >= 1 &&
                (limits.sampleCounts & VK_SAMPLE_COUNT_1_BIT);
            image_fields.insert(image_fields.end(), {
                {"importable", boolean(importable)}, {"dma_buf_compatible", boolean(compatible)},
                {"dedicated_only", boolean(value.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT)},
                {"external_memory_features", std::to_string(value.externalMemoryFeatures)},
                {"compatible_handle_types", std::to_string(value.compatibleHandleTypes)},
                {"max_width", std::to_string(limits.maxExtent.width)},
                {"max_height", std::to_string(limits.maxExtent.height)},
                {"max_array_layers", std::to_string(limits.maxArrayLayers)},
                {"reviewed_extent_and_usage_supported", boolean(external_ok)}});
        } else if (result != VK_ERROR_FORMAT_NOT_SUPPORTED) check(result, "external image format");
        fields.emplace_back("nv12_external_image", object(image_fields));
    } else fields.emplace_back("nv12_external_image", object({{"status", quote("not_queried_missing_prerequisites")}}));
    fields.emplace_back("capability_checks_passed", boolean(properties.apiVersion >= VK_API_VERSION_1_3 &&
        missing.empty() && ycbcr.samplerYcbcrConversion && suitable_queue && host_memory && r8_ok && r8_extent &&
        target_found && memory_planes > 0 && memory_planes <= 4 && external_ok));
    fields.emplace_back("status", quote("queried"));
    return object(fields);
}
} // namespace

int main(int argc, char **argv) {
    if (argc == 2 && (std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h")) {
        std::cout << "Use python3 scripts/capture_gpu_check.py --query-device for a bounded Vulkan query.\n";
        return 0;
    }
    bool requested = argc == 2 && std::string(argv[1]) == "--query-device";
    bool created = false, destroyed = false;
    std::string status = argc == 1 ? "not_run" : "invalid_arguments", error;
    int code = argc == 1 ? 3 : 64;
    std::vector<std::string> devices;
    uint32_t loader_version = 0;
    if (requested) {
        code = 1; status = "query_failed";
        try {
            pid_t parent = getppid();
            if (parent == 1 || prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent)
                throw std::runtime_error("worker parent unavailable");
            rlimit core{0, 0};
            if (setrlimit(RLIMIT_CORE, &core)) throw std::runtime_error("cannot disable core dumps");
            Runtime runtime(destroyed);
            runtime.library = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
            if (!runtime.library) throw std::runtime_error("Vulkan loader unavailable");
            runtime.get = reinterpret_cast<PFN_vkGetInstanceProcAddr>(dlsym(runtime.library, "vkGetInstanceProcAddr"));
            if (!runtime.get) throw std::runtime_error("missing vkGetInstanceProcAddr");
            auto enumerate_version = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(runtime.get(nullptr, "vkEnumerateInstanceVersion"));
            loader_version = VK_API_VERSION_1_0;
            if (enumerate_version) check(enumerate_version(&loader_version), "instance version");
            if (loader_version < VK_API_VERSION_1_1) throw std::runtime_error("Vulkan 1.1 queries unavailable");
            // Resolve destruction before creation so every successful instance has an owner.
            runtime.destroy = reinterpret_cast<PFN_vkDestroyInstance>(dlsym(runtime.library, "vkDestroyInstance"));
            if (!runtime.destroy) throw std::runtime_error("missing vkDestroyInstance");
            auto create = runtime.function<PFN_vkCreateInstance>("vkCreateInstance");
            VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
            app.pApplicationName = "framejacker-gpu-check"; app.apiVersion = VK_API_VERSION_1_1;
            VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; info.pApplicationInfo = &app;
            check(create(&info, nullptr, &runtime.instance), "create instance"); created = true;
            auto enumerate = runtime.function<PFN_vkEnumeratePhysicalDevices>("vkEnumeratePhysicalDevices");
            uint32_t count = 0; check(enumerate(runtime.instance, &count, nullptr), "physical device count");
            bound(count, 32, "physical device");
            if (!count) throw std::runtime_error("no Vulkan physical devices");
            std::vector<VkPhysicalDevice> physical(count);
            check(enumerate(runtime.instance, &count, physical.data()), "physical devices");
            bound(count, static_cast<uint32_t>(physical.size()), "returned physical device");
            if (!count) throw std::runtime_error("no Vulkan physical devices");
            for (uint32_t i = 0; i < count; ++i) devices.push_back(query_device(runtime, physical[i]));
            code = 0; status = "queried";
        } catch (const std::exception &exception) { error = exception.what(); }
    }
    std::cout << object({{"report_kind", quote("framejacker_capture_gpu_query")}, {"schema_version", "1"},
        {"status", quote(status)}, {"exit_code", std::to_string(code)}, {"error", quote(error)},
        {"loader_api_version", version(loader_version)}, {"devices", array(devices)},
        {"vulkan_instance_created", boolean(created)}, {"vulkan_instance_destroyed", boolean(destroyed)},
        {"cleanup_ok", boolean(!created || destroyed)}, {"camera_opened", "false"},
        {"logical_device_created", "false"}, {"external_memory_imported", "false"},
        {"layer_installation_performed", "false"}, {"deployment_ready", "false"}}) << '\n';
    return code;
}
