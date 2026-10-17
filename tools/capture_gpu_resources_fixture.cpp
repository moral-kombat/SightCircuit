// Original fake Vulkan backend: no loader, driver, camera or device access.
#define VK_NO_PROTOTYPES
#include "capture_gpu_resources.hpp"
#include <functional>
#include <iostream>
#include <map>
#include <sys/mman.h>

namespace gpu = framejacker::gpu;
using framejacker::transport::Error;
static void require_at(bool condition, int line) {
    if (!condition) throw std::runtime_error("fixture_assertion_failed_line_" + std::to_string(line));
}
#define require(condition) require_at((condition), __LINE__)
static int injected_failures = 0;
template <typename F> static void rejects(const char *reason, F action) {
    try { action(); } catch (const std::exception &error) {
        require(std::string(error.what()).find(reason) == 0); return;
    }
    throw Error("fixture_expected_rejection");
}
template <typename T> static uintptr_t id(T value) { return reinterpret_cast<uintptr_t>(value); }

struct Fake {
    static Fake *active;
    struct Object { std::string kind; uintptr_t parent = 0, bound_to = 0; bool mapped = false; };
    std::map<uintptr_t, Object> objects;
    std::map<uintptr_t, framejacker::transport::Fd> imported_fds;
    std::vector<std::string> calls;
    std::vector<uint8_t> pixels = std::vector<uint8_t>(4096);
    uintptr_t next = 10;
    int fallible_calls = 0, fail_at = -1;
    uint32_t physical_count = 1, extension_count = 4, queue_count = 2, memory_count = 3;
    uint32_t loader_version = VK_MAKE_API_VERSION(0, 1, 3, 296), device_version = VK_MAKE_API_VERSION(0, 1, 4, 362);
    uint32_t req_bits = 7, selected_type = 99;
    uint64_t req_size = 4096, alignment = 4096;
    bool cpu_first = false, graphics_second = false, compute_only = false, ycbcr = true, r8 = true;
    bool null_queue = false, missing_fd_proc = false, bad_heap = false;
    Fake() { require(!active); active = this; }
    ~Fake() { active = nullptr; }
    static Fake &s() { require(active); return *active; }
    VkResult result(const char *name) {
        calls.emplace_back(name);
        return ++fallible_calls == fail_at ? VK_ERROR_OUT_OF_HOST_MEMORY : VK_SUCCESS;
    }
    template <typename T> VkResult create(const char *name, uintptr_t parent, T *out) {
        auto result_value = result(name); *out = reinterpret_cast<T>(next++);
        if (result_value == VK_SUCCESS) objects.emplace(id(*out), Object{name, parent});
        return result_value; // Failed output handles are intentionally non-null/undefined.
    }
    template <typename T> void destroy(T handle, const char *kind) {
        auto found = objects.find(id(handle)); require(found != objects.end() && found->second.kind == kind);
        for (const auto &entry : objects) require(entry.second.parent != id(handle) && entry.second.bound_to != id(handle));
        require(!found->second.mapped); calls.emplace_back(std::string("destroy_") + kind); objects.erase(found);
    }
    static VKAPI_ATTR VkResult VKAPI_CALL version(uint32_t *out) { *out = s().loader_version; return s().result("version"); }
    static VKAPI_ATTR VkResult VKAPI_CALL instance(const VkInstanceCreateInfo *info, const VkAllocationCallbacks *, VkInstance *out) {
        require(info->pApplicationInfo->apiVersion == VK_API_VERSION_1_3 && !info->enabledLayerCount && !info->enabledExtensionCount);
        return s().create("instance", 0, out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_instance(VkInstance value, const VkAllocationCallbacks *) { s().destroy(value, "instance"); }
    static VKAPI_ATTR VkResult VKAPI_CALL devices(VkInstance, uint32_t *count, VkPhysicalDevice *out) {
        auto &f = s();
        if (out) for (uint32_t i = 0; i < std::min(*count, f.physical_count); ++i) out[i] = reinterpret_cast<VkPhysicalDevice>(1000 + i);
        *count = f.physical_count; return f.result("devices");
    }
    static VKAPI_ATTR void VKAPI_CALL properties(VkPhysicalDevice physical, VkPhysicalDeviceProperties *out) {
        *out = {}; out->apiVersion = s().device_version;
        out->deviceType = s().cpu_first && id(physical) == 1000 ? VK_PHYSICAL_DEVICE_TYPE_CPU : VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL extensions(VkPhysicalDevice, const char *, uint32_t *count, VkExtensionProperties *out) {
        const char *names[] = {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
                              "VK_EXT_image_drm_format_modifier", "VK_EXT_queue_family_foreign"};
        if (out) for (uint32_t i = 0; i < std::min(*count, 4u); ++i) std::strcpy(out[i].extensionName, names[i]);
        *count = s().extension_count; return s().result("extensions");
    }
    static VKAPI_ATTR void VKAPI_CALL features(VkPhysicalDevice, VkPhysicalDeviceFeatures2 *out) {
        auto *value = static_cast<VkPhysicalDeviceSamplerYcbcrConversionFeatures *>(out->pNext);
        require(value->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES);
        value->samplerYcbcrConversion = s().ycbcr;
    }
    static VKAPI_ATTR void VKAPI_CALL memory(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties *out) {
        *out = {}; out->memoryTypeCount = s().memory_count; out->memoryHeapCount = 1;
        out->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
        out->memoryTypes[1].propertyFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
        out->memoryTypes[2].propertyFlags = out->memoryTypes[1].propertyFlags | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        out->memoryTypes[31].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
        if (s().bad_heap) out->memoryTypes[0].heapIndex = 1;
    }
    static VKAPI_ATTR void VKAPI_CALL families(VkPhysicalDevice, uint32_t *count, VkQueueFamilyProperties *out) {
        if (out) for (uint32_t i = 0; i < std::min(*count, 2u); ++i) {
            out[i] = {}; out[i].queueCount = 1;
            out[i].queueFlags = s().compute_only ? VK_QUEUE_COMPUTE_BIT :
                ((i == (s().graphics_second ? 1u : 0u)) ? VK_QUEUE_GRAPHICS_BIT : VK_QUEUE_SPARSE_BINDING_BIT);
        }
        *count = s().queue_count;
    }
    static VKAPI_ATTR void VKAPI_CALL formats(VkPhysicalDevice, VkFormat format, VkFormatProperties2 *out) {
        if (format == framejacker::capture_import::camera_format) {
            auto *list = static_cast<VkDrmFormatModifierPropertiesListEXT *>(out->pNext);
            if (list->pDrmFormatModifierProperties) list->pDrmFormatModifierProperties[0] = {
                framejacker::capture_import::camera_modifier, 2, VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT};
            list->drmFormatModifierCount = 1;
            return;
        }
        require(format == VK_FORMAT_R8_UNORM);
        out->formatProperties.optimalTilingFeatures = s().r8 ? VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_BLIT_DST_BIT |
            VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT : 0;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL image_properties(VkPhysicalDevice, const VkPhysicalDeviceImageFormatInfo2 *info,
                                                          VkImageFormatProperties2 *out) {
        const bool imported = info->format == framejacker::capture_import::camera_format;
        require(imported || (info->format == VK_FORMAT_R8_UNORM && info->usage == (VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT)));
        if (imported) {
            auto *external = static_cast<VkExternalImageFormatProperties *>(out->pNext);
            external->externalMemoryProperties.externalMemoryFeatures = VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT;
            external->externalMemoryProperties.compatibleHandleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        }
        out->imageFormatProperties.maxExtent = {16384, 16384, 1};
        out->imageFormatProperties.maxMipLevels = out->imageFormatProperties.maxArrayLayers = 1;
        out->imageFormatProperties.sampleCounts = VK_SAMPLE_COUNT_1_BIT;
        out->imageFormatProperties.maxResourceSize = gpu::allocation_budget;
        return s().result("image_properties");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL device(VkPhysicalDevice, const VkDeviceCreateInfo *info,
                                                const VkAllocationCallbacks *, VkDevice *out) {
        require(info->queueCreateInfoCount == 1 && info->enabledExtensionCount == 4 &&
                info->pQueueCreateInfos[0].queueFamilyIndex == (s().graphics_second ? 1u : 0u) &&
                info->pQueueCreateInfos[0].pQueuePriorities[0] == 0);
        auto *conversion = static_cast<const VkPhysicalDeviceSamplerYcbcrConversionFeatures *>(info->pNext);
        require(conversion && conversion->samplerYcbcrConversion);
        uintptr_t parent = 0;
        for (auto &entry : s().objects) if (entry.second.kind == "instance") parent = entry.first;
        return s().create("device", parent, out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_device(VkDevice value, const VkAllocationCallbacks *) { s().destroy(value, "device"); }
    static VKAPI_ATTR void VKAPI_CALL queue(VkDevice, uint32_t, uint32_t, VkQueue *out) { *out = s().null_queue ? nullptr : reinterpret_cast<VkQueue>(3000); }
    static VKAPI_ATTR VkResult VKAPI_CALL fd_properties(VkDevice, VkExternalMemoryHandleTypeFlagBits, int fd, VkMemoryFdPropertiesKHR *out) {
        require(fcntl(fd, F_GETFD) & FD_CLOEXEC); out->memoryTypeBits = 7; return VK_SUCCESS;
    }
    static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL proc(VkDevice, const char *name) {
        require(!std::strcmp(name, "vkGetMemoryFdPropertiesKHR"));
        return s().missing_fd_proc ? nullptr : reinterpret_cast<PFN_vkVoidFunction>(fd_properties);
    }
    static VKAPI_ATTR VkResult VKAPI_CALL pool(VkDevice device_value, const VkCommandPoolCreateInfo *info,
                                              const VkAllocationCallbacks *, VkCommandPool *out) {
        require(info->flags == VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT && info->queueFamilyIndex == (s().graphics_second ? 1u : 0u));
        return s().create("pool", id(device_value), out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_pool(VkDevice, VkCommandPool value, const VkAllocationCallbacks *) { s().destroy(value, "pool"); }
    static VKAPI_ATTR VkResult VKAPI_CALL buffer(VkDevice device_value, const VkBufferCreateInfo *info,
                                                const VkAllocationCallbacks *, VkBuffer *out) {
        require(info->size && info->usage == VK_BUFFER_USAGE_TRANSFER_DST_BIT && !info->flags);
        return s().create("buffer", id(device_value), out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_buffer(VkDevice, VkBuffer value, const VkAllocationCallbacks *) {
        for (auto &entry : s().objects) if (entry.second.bound_to == id(value)) entry.second.bound_to = 0;
        s().destroy(value, "buffer");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL image(VkDevice device_value, const VkImageCreateInfo *info,
                                               const VkAllocationCallbacks *, VkImage *out) {
        require((info->format == VK_FORMAT_R8_UNORM || info->format == framejacker::capture_import::camera_format) && info->imageType == VK_IMAGE_TYPE_2D &&
                info->arrayLayers == 1 && info->mipLevels == 1 && info->extent.depth == 1 &&
                (info->tiling == VK_IMAGE_TILING_OPTIMAL || info->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT) && !info->flags);
        return s().create("image", id(device_value), out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_image(VkDevice, VkImage value, const VkAllocationCallbacks *) {
        for (auto &entry : s().objects) if (entry.second.bound_to == id(value)) entry.second.bound_to = 0;
        s().destroy(value, "image");
    }
    static void requirements(VkMemoryRequirements2 *out) {
        require(out->sType == VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2 && out->pNext);
        auto *dedicated = static_cast<VkMemoryDedicatedRequirements *>(out->pNext);
        require(dedicated->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS);
        dedicated->requiresDedicatedAllocation = VK_TRUE;
        out->memoryRequirements = {s().req_size, s().alignment, s().req_bits};
    }
    static VKAPI_ATTR void VKAPI_CALL buffer_req(VkDevice, const VkBufferMemoryRequirementsInfo2 *info, VkMemoryRequirements2 *out) {
        require(s().objects.count(id(info->buffer))); requirements(out);
    }
    static VKAPI_ATTR void VKAPI_CALL image_req(VkDevice, const VkImageMemoryRequirementsInfo2 *info, VkMemoryRequirements2 *out) {
        require(s().objects.count(id(info->image))); requirements(out);
    }
    static VKAPI_ATTR VkResult VKAPI_CALL allocate(VkDevice device_value, const VkMemoryAllocateInfo *info,
                                                  const VkAllocationCallbacks *, VkDeviceMemory *out) {
        auto *dedicated = static_cast<const VkMemoryDedicatedAllocateInfo *>(info->pNext);
        int imported_fd = -1;
        if (dedicated->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR) {
            auto *import = static_cast<const VkImportMemoryFdInfoKHR *>(info->pNext);
            imported_fd = import->fd; dedicated = static_cast<const VkMemoryDedicatedAllocateInfo *>(import->pNext);
        }
        require(dedicated && dedicated->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO &&
                bool(dedicated->image) != bool(dedicated->buffer) && info->allocationSize == s().req_size);
        s().selected_type = info->memoryTypeIndex;
        auto result = s().create("memory", id(device_value), out);
        if (result == VK_SUCCESS && imported_fd >= 0)
            s().imported_fds.emplace(id(*out), framejacker::transport::Fd(imported_fd));
        return result;
    }
    static VKAPI_ATTR void VKAPI_CALL free_memory(VkDevice, VkDeviceMemory value, const VkAllocationCallbacks *) {
        require(s().objects.at(id(value)).bound_to == 0); s().imported_fds.erase(id(value)); s().destroy(value, "memory");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL bind_buffer(VkDevice, VkBuffer buffer_value, VkDeviceMemory memory_value, VkDeviceSize offset) {
        require(!offset); auto result_value = s().result("bind_buffer");
        if (result_value == VK_SUCCESS) s().objects.at(id(memory_value)).bound_to = id(buffer_value);
        return result_value;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL bind_image(VkDevice, VkImage image_value, VkDeviceMemory memory_value, VkDeviceSize offset) {
        require(!offset); auto result_value = s().result("bind_image");
        if (result_value == VK_SUCCESS) s().objects.at(id(memory_value)).bound_to = id(image_value);
        return result_value;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL map(VkDevice, VkDeviceMemory memory_value, VkDeviceSize offset, VkDeviceSize size,
                                             VkMemoryMapFlags flags, void **out) {
        require(!offset && size == VK_WHOLE_SIZE && !flags);
        auto result_value = s().result("map"); *out = s().pixels.data();
        if (result_value == VK_SUCCESS) s().objects.at(id(memory_value)).mapped = true;
        return result_value;
    }
    static VKAPI_ATTR void VKAPI_CALL unmap(VkDevice, VkDeviceMemory value) {
        auto &object = s().objects.at(id(value)); require(object.mapped); object.mapped = false; s().calls.emplace_back("unmap");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL invalidate(VkDevice, uint32_t count, const VkMappedMemoryRange *ranges) {
        require(count == 1 && ranges->size == VK_WHOLE_SIZE && !ranges->offset && s().objects.at(id(ranges->memory)).mapped);
        return s().result("invalidate");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL commands(VkDevice, const VkCommandBufferAllocateInfo *info, VkCommandBuffer *out) {
        require(info->commandBufferCount == 1 && info->level == VK_COMMAND_BUFFER_LEVEL_PRIMARY);
        return s().create("command", id(info->commandPool), out);
    }
    static VKAPI_ATTR void VKAPI_CALL free_commands(VkDevice, VkCommandPool, uint32_t count, const VkCommandBuffer *out) {
        require(count == 1); s().destroy(out[0], "command");
    }
    static VKAPI_ATTR VkResult VKAPI_CALL fence(VkDevice device_value, const VkFenceCreateInfo *info,
                                               const VkAllocationCallbacks *, VkFence *out) {
        require(!info->flags); return s().create("fence", id(device_value), out);
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_fence(VkDevice, VkFence value, const VkAllocationCallbacks *) { s().destroy(value, "fence"); }
    gpu::Api api() const {
        gpu::Api api;
        api.imports = {formats, image_properties, nullptr, image, destroy_image, image_req, allocate, free_memory, bind_image};
        api.instance_version = version; api.create_instance = instance; api.destroy_instance = destroy_instance;
        api.devices = devices; api.properties = properties; api.extensions = extensions; api.features = features;
        api.memory = memory; api.families = families; api.create_device = device; api.destroy_device = destroy_device;
        api.queue = queue; api.device_proc = proc; api.create_pool = pool; api.destroy_pool = destroy_pool;
        api.create_buffer = buffer; api.destroy_buffer = destroy_buffer; api.buffer_requirements = buffer_req; api.bind_buffer = bind_buffer;
        api.map = map; api.unmap = unmap; api.invalidate = invalidate; api.allocate_commands = commands; api.free_commands = free_commands;
        api.create_fence = fence; api.destroy_fence = destroy_fence;
        return api;
    }
};
Fake *Fake::active = nullptr;

static void lifecycle() {
    Fake fake;
    { auto device = gpu::Device::open(fake.api());
      auto host = device.host_buffer(1024); require(fake.selected_type == 2 && host.mapped()); host.invalidate();
      auto image = device.image(64, 64); auto commands = device.commands();
      require(image.image() && commands.command() && commands.fence() && device.allocated_bytes() == 8192);
      require(host.close() && image.close() && commands.close() && device.allocated_bytes() == 0);
      require(device.close()); }
    require(fake.objects.empty());
    const auto count = fake.calls.size(); require(count >= 3);
    require(fake.calls[count - 3] == "destroy_pool" && fake.calls[count - 2] == "destroy_device" && fake.calls.back() == "destroy_instance");
}
static int complete_path(int fail_at = -1) {
    Fake fake; fake.fail_at = fail_at;
    bool rejected = false;
    try {
        auto device = gpu::Device::open(fake.api());
        auto host = device.host_buffer(1024);
        auto image = device.image(64, 64); auto commands = device.commands(); host.invalidate();
    } catch (const std::exception &error) {
        if (fail_at <= 0 || fake.fallible_calls != fail_at ||
            std::string(error.what()).find(": VkResult -1") == std::string::npos) throw;
        rejected = true;
    }
    require(fail_at < 0 || rejected);
    require(fake.objects.empty());
    return fake.fallible_calls;
}
static void failure_matrix() {
    int total = complete_path(); require(total >= 15);
    for (int repetition = 0; repetition < 3; ++repetition)
        for (int i = 1; i <= total; ++i) { complete_path(i); ++injected_failures; }
}
static void selection() {
    { Fake fake; auto api = fake.api(); api.map = nullptr;
      rejects("gpu_dispatch_incomplete", [&] { gpu::Device::open(api); }); require(fake.calls.empty()); }
    { Fake fake; fake.physical_count = 2; fake.cpu_first = true; fake.graphics_second = true;
      { auto device = gpu::Device::open(fake.api()); require(device.family() == 1); }
      require(fake.objects.empty()); }
    const std::vector<std::pair<const char *, std::function<void(Fake &)>>> cases = {
        {"gpu_multiple_compatible_devices_require_selection", [](auto &f) { f.physical_count = 2; }},
        {"gpu_no_compatible_device", [](auto &f) { f.physical_count = 0; }},
        {"gpu_vulkan_1_3_required", [](auto &f) { f.loader_version = VK_API_VERSION_1_2; }},
        {"gpu_no_compatible_device", [](auto &f) { f.device_version = VK_API_VERSION_1_2; }},
        {"gpu_no_compatible_device", [](auto &f) { f.compute_only = true; }},
        {"gpu_no_compatible_device", [](auto &f) { f.extension_count = 3; }},
        {"gpu_no_compatible_device", [](auto &f) { f.ycbcr = false; }},
        {"gpu_no_compatible_device", [](auto &f) { f.r8 = false; }},
        {"gpu_enumeration_bounds", [](auto &f) { f.physical_count = 33; }},
        {"gpu_enumeration_bounds", [](auto &f) { f.extension_count = 4097; }},
        {"gpu_enumeration_bounds", [](auto &f) { f.queue_count = 257; }},
        {"gpu_enumeration_bounds", [](auto &f) { f.memory_count = 33; }},
        {"gpu_memory_heap_bounds", [](auto &f) { f.bad_heap = true; }},
        {"gpu_null_queue", [](auto &f) { f.null_queue = true; }},
        {"gpu_fd_import_dispatch_unavailable", [](auto &f) { f.missing_fd_proc = true; }},
    };
    for (const auto &item : cases) { Fake fake; item.second(fake); rejects(item.first, [&] { gpu::Device::open(fake.api()); }); require(fake.objects.empty()); }
}
static void bounds_and_budget() {
    Fake fake;
    auto device = gpu::Device::open(fake.api());
    rejects("gpu_host_buffer_size_bounds", [&] { device.host_buffer(0); });
    rejects("gpu_host_buffer_size_bounds", [&] { device.host_buffer((64ULL << 20) + 1); });
    rejects("gpu_image_dimensions_bounds", [&] { device.image(2465, 1); });
    for (uint64_t invalid : {uint64_t{0}, gpu::allocation_budget + 1}) {
        fake.req_size = invalid;
        rejects("gpu_allocation_bounds_or_budget", [&] { device.image(64, 64); });
    }
    fake.req_size = 4096; fake.alignment = 3;
    rejects("gpu_allocation_bounds_or_budget", [&] { device.image(64, 64); });
    fake.alignment = 4096; fake.req_bits = 0;
    rejects("gpu_memory_type_unavailable", [&] { device.image(64, 64); });
    fake.req_bits = 7; fake.req_size = gpu::allocation_budget / 2;
    auto first = device.image(64, 64); auto second = device.image(64, 64);
    rejects("gpu_allocation_bounds_or_budget", [&] { device.image(64, 64); });
    require(device.allocated_bytes() == gpu::allocation_budget);
    first.close(); second.close(); require(device.allocated_bytes() == 0);
    device.close(); require(fake.objects.empty());
}
static void moves_and_parent_lifetime() {
    Fake fake; gpu::Resource child;
    { auto device = gpu::Device::open(fake.api()); child = device.host_buffer(1024);
      rejects("gpu_resources_still_owned", [&] { device.close(); });
      auto moved = std::move(child); require(!child.buffer() && moved.buffer()); child = std::move(moved); }
    require(!fake.objects.empty()); require(child.close() && child.close()); require(fake.objects.empty());
}
static void incomplete_work() {
    for (bool subsequently_idle : {false, true}) {
        Fake fake; auto device = gpu::Device::open(fake.api());
        auto host = device.host_buffer(1024); auto image = device.image(64, 64); auto commands = device.commands();
        device.completion().submitted(); size_t before = fake.calls.size();
        rejects("gpu_work_or_cleanup_unconfirmed", [&] { device.image(64, 64); });
        rejects("gpu_work_or_cleanup_unconfirmed", [&] { host.invalidate(); });
        require(!host.close() && !image.close() && !commands.close() && device.completion().cleanup_deferred);
        if (subsequently_idle) device.completion().completed();
        rejects("gpu_work_or_cleanup_unconfirmed", [&] { device.commands(); });
        require(!device.close() && fake.calls.size() == before && !fake.objects.empty());
        fake.objects.clear(); // Simulated process teardown, not graceful Vulkan destruction.
    }
}
static void memory_fallback() {
    Fake fake; fake.memory_count = 32;
    auto device = gpu::Device::open(fake.api());
    fake.req_bits = 2;
    { auto host = device.host_buffer(1024); require(fake.selected_type == 1); host.invalidate(); }
    fake.req_bits = uint32_t{1} << 31;
    { auto image = device.image(64, 64); require(fake.selected_type == 31); }
    device.close(); require(fake.objects.empty());
}
static void imported_parent_lifetime() {
    Fake fake;
    framejacker::transport::Fd source(memfd_create("framejacker-resource-import", MFD_CLOEXEC));
    require(source.get() >= 0 && ftruncate(source.get(), 1 << 24) == 0);
    MrhandBufInfo info{};
    info.magic = MRHAND_MAGIC; info.generation = info.id = info.hasImage = info.hasModifier = 1;
    info.size = 1 << 24; info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    info.format = framejacker::capture_import::camera_format; info.width = info.height = 2464; info.arrayLayers = 1;
    info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT; info.usage = framejacker::capture_import::import_usage;
    info.modifier = framejacker::capture_import::camera_modifier; info.planeCount = 2;
    info.planeOffset[1] = 1024; info.planeRowPitch[0] = info.planeRowPitch[1] = 256;
    gpu::ImportedImage first, second;
    { auto device = gpu::Device::open(fake.api()); first = device.import_image(info, source.get());
      rejects("gpu_resources_still_owned", [&] { device.close(); }); }
    { auto device = gpu::Device::open(fake.api()); second = device.import_image(info, source.get()); }
    require(fake.imported_fds.size() == 2);
    first = std::move(second); require(!second.image() && fake.imported_fds.size() == 1);
    require(first.close() && fake.objects.empty() && fake.imported_fds.empty() && fcntl(source.get(), F_GETFD) >= 0);
}
int main(int argc, char **argv) {
    const std::vector<std::pair<const char *, void (*)()>> tests = {
        {"lifecycle", lifecycle}, {"failures", failure_matrix}, {"selection", selection},
        {"bounds", bounds_and_budget}, {"moves", moves_and_parent_lifetime},
        {"pending", incomplete_work}, {"memory", memory_fallback},
        {"imports", imported_parent_lifetime},
    };
    const bool all = argc == 2 && !std::strcmp(argv[1], "--self-test");
    const bool one = argc == 3 && !std::strcmp(argv[1], "--case");
    if (!all && !one) {
        std::cout << "{\"report_kind\":\"capture_gpu_resources_fixture\",\"status\":\"not_run\"}\n";
        return argc == 1 ? 3 : 64;
    }
    try {
        size_t ran = 0;
        for (const auto &test : tests) {
            if (!all && std::strcmp(test.first, argv[2])) continue;
            test.second(); ++ran;
        }
        if (!ran) throw Error("unknown_fixture_case");
        std::cout << "{\"report_kind\":\"capture_gpu_resources_fixture\",\"schema_version\":1,\"status\":\"passed\","
                     "\"tests_run\":" << ran << ",\"injected_failures\":" << injected_failures <<
                     ",\"runtime_initialized\":false,\"capture_started\":false,"
                     "\"scope\":\"Fake Vulkan dispatch only; no real GPU device, commands or camera\"}\n";
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
