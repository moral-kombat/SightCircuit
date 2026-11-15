// The gpu_convert pixel path: a camera DMA-BUF is imported into Vulkan as a
// multi-planar image with the frame's own DRM format modifier and plane layout
// (VK_EXT_image_drm_format_modifier, VK_EXT_external_memory_dma_buf), read by
// the compute shader in camera_convert.comp (the fixed-point formula of
// camera_ycbcr.hpp, so the GPU delivers the same bytes as the CPU path), and
// the BGR3 rows are read back from a host-visible buffer. The import is a
// stage of its own (import_modifier and import_linear_copy below) so it can be
// swapped without touching the conversion.
//
// How the image is created and read is an import strategy, chosen per frame
// layout by asking the driver (vkGetPhysicalDeviceImageFormatProperties2) in
// this order and taking the first it accepts:
//   plane_views_uint   MUTABLE_FORMAT, per-plane R8_UINT/R8G8_UINT (R16) views;
//   plane_views_unorm  MUTABLE_FORMAT, per-plane R8_UNORM/R8G8_UNORM (R16) views;
//   ycbcr_sampler      no MUTABLE_FORMAT: the whole image through an immutable
//                      sampler with a VkSamplerYcbcrConversion that converts
//                      nothing (model RGB_IDENTITY, nearest chroma filter).
// A driver may refuse a compressed modifier for a mutable image (Mesa's Turnip
// does for UBWC NV12: reinterpreting a plane is not possible under its
// compression), which leaves the third. Each strategy has its own variant of
// the shader; all three feed the same integer formula.
//
// When the device lacks the modifier extension (the cloud runner's lavapipe)
// and the buffer is linear, the import falls back to importing the DMA-BUF as a
// Vulkan buffer and copying its planes into an optimally tiled image: the
// layout then comes from the frame's plane table, never from the driver. Tiled
// or compressed buffers (UBWC) need the modifier path; nothing here can read
// them otherwise.
//
// query_gpu_convert() opens the same device and reports what the driver
// answers for these formats, modifiers and strategies; it imports nothing and
// reads no buffer.
//
// The Vulkan loader is opened with dlopen at run time, never linked, so a build
// without a loader or a device still runs every other mode. The path writes
// nothing anywhere but the caller's buffer. C++17.
#include <cerrno>
#include <cstring>
#include <string>
#include <vector>

#include <dlfcn.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#include "camera_convert_spirv.hpp"
#include "camera_export_protocol.hpp"
#include "camera_pixels.hpp"

namespace sightcircuit_camera {
namespace {

constexpr uint64_t kFenceTimeoutNs = 5000000000ull;  // the watchdog around convert is the outer bound

const char* const kRequiredExtensions[] = {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf"};
const char* const kModifierExtension = "VK_EXT_image_drm_format_modifier";
const char* const kForeignQueueExtension = "VK_EXT_queue_family_foreign";
const char* const kFormatListExtension = "VK_KHR_image_format_list";

// The room the --query-gpu object may take in the helper's one-line record (8 KiB with its envelope).
constexpr std::size_t kMaxQueryBytes = 7168;

// DRM_FORMAT_MOD_QCOM_COMPRESSED (UBWC): the modifier the Frame's camera service delivers (E97).
constexpr uint64_t kDrmModifierQcomCompressed = 0x0500000000000001ull;

#define SIGHTCIRCUIT_VK_FUNCTIONS(X)            \
    X(vkCreateInstance)                         \
    X(vkDestroyInstance)                        \
    X(vkEnumeratePhysicalDevices)               \
    X(vkGetPhysicalDeviceProperties)            \
    X(vkEnumerateDeviceExtensionProperties)     \
    X(vkGetPhysicalDeviceQueueFamilyProperties) \
    X(vkGetPhysicalDeviceMemoryProperties)      \
    X(vkGetPhysicalDeviceFeatures2)             \
    X(vkGetPhysicalDeviceFormatProperties2)     \
    X(vkGetPhysicalDeviceImageFormatProperties2) \
    X(vkCreateDevice)                           \
    X(vkDestroyDevice)                          \
    X(vkGetDeviceQueue)                         \
    X(vkDeviceWaitIdle)                         \
    X(vkCreateImage)                            \
    X(vkDestroyImage)                           \
    X(vkGetImageMemoryRequirements2)            \
    X(vkCreateBuffer)                           \
    X(vkDestroyBuffer)                          \
    X(vkGetBufferMemoryRequirements2)           \
    X(vkAllocateMemory)                         \
    X(vkFreeMemory)                             \
    X(vkBindImageMemory)                        \
    X(vkBindBufferMemory)                       \
    X(vkMapMemory)                              \
    X(vkUnmapMemory)                            \
    X(vkInvalidateMappedMemoryRanges)           \
    X(vkCreateImageView)                        \
    X(vkDestroyImageView)                       \
    X(vkCreateSampler)                          \
    X(vkDestroySampler)                         \
    X(vkCreateSamplerYcbcrConversion)           \
    X(vkDestroySamplerYcbcrConversion)          \
    X(vkCreateShaderModule)                     \
    X(vkDestroyShaderModule)                    \
    X(vkCreateDescriptorSetLayout)              \
    X(vkDestroyDescriptorSetLayout)             \
    X(vkCreatePipelineLayout)                   \
    X(vkDestroyPipelineLayout)                  \
    X(vkCreateComputePipelines)                 \
    X(vkDestroyPipeline)                        \
    X(vkCreateDescriptorPool)                   \
    X(vkDestroyDescriptorPool)                  \
    X(vkResetDescriptorPool)                    \
    X(vkAllocateDescriptorSets)                 \
    X(vkUpdateDescriptorSets)                   \
    X(vkCreateCommandPool)                      \
    X(vkDestroyCommandPool)                     \
    X(vkResetCommandPool)                       \
    X(vkAllocateCommandBuffers)                 \
    X(vkBeginCommandBuffer)                     \
    X(vkEndCommandBuffer)                       \
    X(vkCmdPipelineBarrier)                     \
    X(vkCmdCopyBufferToImage)                   \
    X(vkCmdBindPipeline)                        \
    X(vkCmdBindDescriptorSets)                  \
    X(vkCmdPushConstants)                       \
    X(vkCmdDispatch)                            \
    X(vkQueueSubmit)                            \
    X(vkCreateFence)                            \
    X(vkDestroyFence)                           \
    X(vkResetFences)                            \
    X(vkWaitForFences)

struct VulkanApi {
    void* library = nullptr;
    PFN_vkGetInstanceProcAddr get_instance_proc_addr = nullptr;
#define SIGHTCIRCUIT_VK_DECLARE(name) PFN_##name name = nullptr;
    SIGHTCIRCUIT_VK_FUNCTIONS(SIGHTCIRCUIT_VK_DECLARE)
#undef SIGHTCIRCUIT_VK_DECLARE
    PFN_vkGetMemoryFdPropertiesKHR vkGetMemoryFdPropertiesKHR = nullptr;
};

std::string vk_result(VkResult result) { return "VkResult=" + std::to_string(static_cast<int>(result)); }
std::string vk_error(const char* call, VkResult result) { return std::string(call) + ":" + vk_result(result); }

struct PushConstants {
    uint32_t width, height, stride_words, sample_shift, sample_max;
    int32_t cr_r, cb_g, cr_g, cb_b;
};
static_assert(sizeof(PushConstants) == 36, "push constant layout drifted from camera_convert.comp");

// ---- import strategies ---------------------------------------------------------------

enum class ImportStrategy { plane_views_uint = 0, plane_views_unorm = 1, ycbcr_sampler = 2 };
constexpr int kStrategyCount = 3;
// The order they are tried in.
const ImportStrategy kStrategies[kStrategyCount] = {ImportStrategy::plane_views_uint, ImportStrategy::plane_views_unorm,
                                                    ImportStrategy::ycbcr_sampler};
const char* strategy_name(ImportStrategy strategy) {
    switch (strategy) {
        case ImportStrategy::plane_views_uint: return "plane_views_uint";
        case ImportStrategy::plane_views_unorm: return "plane_views_unorm";
        case ImportStrategy::ycbcr_sampler: return "ycbcr_sampler";
    }
    return "plane_views_uint";
}
bool strategy_uses_plane_views(ImportStrategy strategy) { return strategy != ImportStrategy::ycbcr_sampler; }

// Which per-plane view formats an image-format query or an image names in its format list.
enum class ViewFormats { none = 0, uint_views = 1, unorm_views = 2 };
ViewFormats strategy_views(ImportStrategy strategy) {
    switch (strategy) {
        case ImportStrategy::plane_views_uint: return ViewFormats::uint_views;
        case ImportStrategy::plane_views_unorm: return ViewFormats::unorm_views;
        case ImportStrategy::ycbcr_sampler: return ViewFormats::none;
    }
    return ViewFormats::none;
}

struct PlaneFormat {
    VkFormat image_format;
    VkFormat luma_uint, chroma_uint;    // the views of planes 0 and 1 under plane_views_uint
    VkFormat luma_unorm, chroma_unorm;  // and under plane_views_unorm
    uint32_t luma_texel_bytes;
    uint32_t chroma_texel_bytes;
};
PlaneFormat plane_format(SourceFormat format) {
    if (format == SourceFormat::p010) {
        return {VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16, VK_FORMAT_R16_UINT, VK_FORMAT_R16G16_UINT,
                VK_FORMAT_R16_UNORM, VK_FORMAT_R16G16_UNORM, 2, 4};
    }
    return {VK_FORMAT_G8_B8R8_2PLANE_420_UNORM, VK_FORMAT_R8_UINT, VK_FORMAT_R8G8_UINT, VK_FORMAT_R8_UNORM,
            VK_FORMAT_R8G8_UNORM, 1, 2};
}

const char* chroma_location_name(VkChromaLocation location) {
    return location == VK_CHROMA_LOCATION_MIDPOINT ? "midpoint" : "cosited_even";
}

// The compute pipeline of one strategy (and, for ycbcr_sampler, one image
// format and chroma location: its sampler is immutable in the set layout).
struct Program {
    bool built = false;
    VkChromaLocation chroma_location = VK_CHROMA_LOCATION_MIDPOINT;
    VkSamplerYcbcrConversion conversion = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;  // ycbcr_sampler only
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
};

// What the driver says about one format with one tiling (a modifier, or optimal).
struct TilingFacts {
    bool listed = false;
    uint32_t plane_count = 0;
    VkFormatFeatureFlags features = 0;
};

struct ImageQueryResult {
    VkResult result = VK_ERROR_FORMAT_NOT_SUPPORTED;
    VkExtent3D max_extent{};
    VkExternalMemoryFeatureFlags external_features = 0;
    VkExternalMemoryHandleTypeFlags compatible_handle_types = 0;
};

// The strategy for one frame layout, decided once and kept while the layout stays.
struct Decision {
    bool valid = false;
    SourceFormat format = SourceFormat::nv12;
    uint64_t modifier = 0;
    uint32_t plane_count = 0, width = 0, height = 0;
    bool chosen = false;
    ImportStrategy strategy = ImportStrategy::plane_views_uint;
    VkChromaLocation chroma_location = VK_CHROMA_LOCATION_MIDPOINT;
    std::string results;  // "plane_views_uint=VkResult=-11,...,ycbcr_sampler=ok"
    std::string error;    // why no strategy was chosen
};

// Everything created for one frame, destroyed after its readback.
struct FrameResources {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE;
    VkBuffer source_buffer = VK_NULL_HANDLE;  // linear-copy fallback only
    VkDeviceMemory source_memory = VK_NULL_HANDLE;
    VkImageView luma_view = VK_NULL_HANDLE;    // plane 0, or the whole image under ycbcr_sampler
    VkImageView chroma_view = VK_NULL_HANDLE;  // plane 1; unused under ycbcr_sampler
    bool copy_from_buffer = false;
};

std::string json_bool(bool value) { return value ? "true" : "false"; }

class GpuConvertPath : public PixelPath {
public:
    explicit GpuConvertPath(const char* only_strategy) : only_strategy_(only_strategy ? only_strategy : "") {}
    ~GpuConvertPath() override { close(); }

    const char* name() const override { return sightcircuit_camera_export::kPixelPathGpuConvert; }
    const char* derivation() const override { return sightcircuit_camera_export::kDerivationGpuConverted; }

    bool open(std::string& error, std::string& diagnostics_json) override {
        if (!only_strategy_.empty()) {
            bool known = false;
            for (ImportStrategy strategy : kStrategies) known = known || only_strategy_ == strategy_name(strategy);
            if (!known) { error = "import_strategy_unknown:" + only_strategy_; return false; }
        }
        if (!load_loader(error)) return false;
        if (!create_instance(error)) return false;
        if (!pick_device(error)) return false;
        if (!create_device(error)) return false;
        if (!create_shared_objects(error)) return false;
        // The first strategy's pipeline is built now, so a device that cannot compile the shader fails here.
        Program* program = nullptr;
        if (!ensure_program(ImportStrategy::plane_views_uint, SourceFormat::nv12, VK_CHROMA_LOCATION_MIDPOINT, program, error)) {
            return false;
        }
        diagnostics_json = diagnostics();
        return true;
    }

    void plan(const SourceFrame& frame, std::string& diagnostics_json) override {
        if (device_ == VK_NULL_HANDLE) return;
        std::string ignored;
        decide(frame, ignored);  // a layout no strategy accepts is reported by convert(); the facts say why
        diagnostics_json = diagnostics();
    }

    bool convert(const SourceFrame& frame, BgrImage& out, std::string& error) override {
        if (device_ == VK_NULL_HANDLE) { error = "gpu_path_not_open"; return false; }
        const uint64_t payload = static_cast<uint64_t>(bgr_stride_bytes(frame.width)) * frame.height;
        if (payload > sightcircuit_camera_export::kMaxPayloadBytes) { error = "payload_exceeds_protocol_bound"; return false; }
        if (frame.plane_count < 2) { error = "plane_count_below_two"; return false; }
        if (!decide(frame, error)) return false;
        Program* program = nullptr;
        if (!ensure_program(decision_.strategy, frame.format, decision_.chroma_location, program, error)) return false;
        FrameResources resources;
        bool ok = import_frame(frame, resources, error) && ensure_output(payload, error) &&
                  dispatch_and_read(frame, resources, *program, out, payload, error);
        destroy(resources);
        return ok;
    }

    std::string notes_json() const override {
        return std::string("{\"import\":\"") + (modifier_extension_ ? "drm_format_modifier" : "linear_copy_fallback") +
               "\",\"import_strategy\":\"" + strategy_fact() + "\",\"frames_imported\":" + std::to_string(frames_) + "}";
    }

    // What the driver answers, for --query-gpu: NV12 and P010 with each listed
    // modifier (two at most), the UBWC modifier whether listed or not, and
    // optimal tiling; every combination of MUTABLE_FORMAT, format list and
    // usage; whether each strategy's pipeline builds; and whether an image of
    // the Frame's delivered layout (E97) can be created. Imports nothing.
    std::string query_json() {
        const std::string head = "{\"device\":" + diagnostics() + ",\"modifier_extension\":" + json_bool(modifier_extension_) +
                                 ",\"programs\":" + query_programs() +
                                 ",\"columns\":[\"mutable_format\",\"view_formats\",\"transfer_src\",\"vk_result\",\"max_width\","
                                 "\"max_height\",\"external_memory_features\",\"compatible_handle_types\"],\"formats\":[";
        const std::string tail = "],\"trial_image\":" + query_trial_image() + "}";
        // The helper's records are single lines of at most 8 KiB: a driver with many modifiers gets fewer
        // of them reported (modifiers_omitted counts the rest), never a record that does not fit.
        std::string out;
        for (int other_modifiers = 2; other_modifiers >= 0; --other_modifiers) {
            out = head + query_format(SourceFormat::nv12, static_cast<std::size_t>(other_modifiers)) + "," +
                  query_format(SourceFormat::p010, static_cast<std::size_t>(other_modifiers)) + tail;
            if (out.size() <= kMaxQueryBytes) break;
        }
        return out;
    }

private:
    // ---- set-up ------------------------------------------------------------------

    bool load_loader(std::string& error) {
        api_.library = ::dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
        if (!api_.library) { error = "vulkan_loader_unavailable"; return false; }
        api_.get_instance_proc_addr =
            reinterpret_cast<PFN_vkGetInstanceProcAddr>(::dlsym(api_.library, "vkGetInstanceProcAddr"));
        if (!api_.get_instance_proc_addr) { error = "vulkan_loader_without_vkGetInstanceProcAddr"; return false; }
        api_.vkCreateInstance = reinterpret_cast<PFN_vkCreateInstance>(api_.get_instance_proc_addr(nullptr, "vkCreateInstance"));
        if (!api_.vkCreateInstance) { error = "vulkan_loader_without_vkCreateInstance"; return false; }
        return true;
    }

    bool create_instance(std::string& error) {
        VkApplicationInfo application{};
        application.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
        application.pApplicationName = "sightcircuit-camera-helper";
        application.apiVersion = VK_API_VERSION_1_1;
        VkInstanceCreateInfo info{};
        info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
        info.pApplicationInfo = &application;
        const VkResult result = api_.vkCreateInstance(&info, nullptr, &instance_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateInstance", result); return false; }
        bool complete = true;
#define SIGHTCIRCUIT_VK_LOAD(name)                                                                    \
        api_.name = reinterpret_cast<PFN_##name>(api_.get_instance_proc_addr(instance_, #name));     \
        if (!api_.name) complete = false;
        SIGHTCIRCUIT_VK_FUNCTIONS(SIGHTCIRCUIT_VK_LOAD)
#undef SIGHTCIRCUIT_VK_LOAD
        if (!complete) { error = "vulkan_1_1_entry_point_missing"; return false; }
        return true;
    }

    static bool has_extension(const std::vector<VkExtensionProperties>& extensions, const char* name) {
        for (const VkExtensionProperties& extension : extensions) {
            if (std::strcmp(extension.extensionName, name) == 0) return true;
        }
        return false;
    }

    bool pick_device(std::string& error) {
        uint32_t count = 0;
        VkResult result = api_.vkEnumeratePhysicalDevices(instance_, &count, nullptr);
        if (result != VK_SUCCESS) { error = vk_error("vkEnumeratePhysicalDevices", result); return false; }
        if (!count) { error = "no_vulkan_physical_device"; return false; }
        std::vector<VkPhysicalDevice> devices(count);
        result = api_.vkEnumeratePhysicalDevices(instance_, &count, devices.data());
        if (result != VK_SUCCESS && result != VK_INCOMPLETE) { error = vk_error("vkEnumeratePhysicalDevices", result); return false; }
        // The first device with dma-buf import; one with the modifier extension wins over one without.
        int best_score = -1;
        for (VkPhysicalDevice device : devices) {
            uint32_t extension_count = 0;
            if (api_.vkEnumerateDeviceExtensionProperties(device, nullptr, &extension_count, nullptr) != VK_SUCCESS) continue;
            std::vector<VkExtensionProperties> extensions(extension_count);
            if (api_.vkEnumerateDeviceExtensionProperties(device, nullptr, &extension_count, extensions.data()) != VK_SUCCESS) continue;
            bool required = true;
            for (const char* name : kRequiredExtensions) required = required && has_extension(extensions, name);
            if (!required) continue;
            uint32_t family_count = 0;
            api_.vkGetPhysicalDeviceQueueFamilyProperties(device, &family_count, nullptr);
            std::vector<VkQueueFamilyProperties> families(family_count);
            api_.vkGetPhysicalDeviceQueueFamilyProperties(device, &family_count, families.data());
            uint32_t family = UINT32_MAX;
            for (uint32_t index = 0; index < family_count; ++index) {
                if ((families[index].queueFlags & VK_QUEUE_COMPUTE_BIT) && families[index].queueCount) { family = index; break; }
            }
            if (family == UINT32_MAX) continue;
            const int score = has_extension(extensions, kModifierExtension) ? 1 : 0;
            if (score > best_score) {
                best_score = score;
                physical_ = device;
                queue_family_ = family;
                modifier_extension_ = score == 1;
                foreign_queue_extension_ = has_extension(extensions, kForeignQueueExtension);
                format_list_extension_ = has_extension(extensions, kFormatListExtension);
            }
        }
        if (best_score < 0) { error = "no_vulkan_device_with_dma_buf_import"; return false; }
        api_.vkGetPhysicalDeviceProperties(physical_, &properties_);
        api_.vkGetPhysicalDeviceMemoryProperties(physical_, &memory_properties_);
        return true;
    }

    bool create_device(std::string& error) {
        std::vector<const char*> extensions(kRequiredExtensions, kRequiredExtensions + 2);
        if (modifier_extension_) extensions.push_back(kModifierExtension);
        if (foreign_queue_extension_) extensions.push_back(kForeignQueueExtension);
        // The format list is core from 1.2; on a 1.1 device the extension carries it.
        if (format_list_extension_ && properties_.apiVersion < VK_API_VERSION_1_2) extensions.push_back(kFormatListExtension);
        format_list_available_ = properties_.apiVersion >= VK_API_VERSION_1_2 || format_list_extension_;
        VkPhysicalDeviceSamplerYcbcrConversionFeatures ycbcr{};
        ycbcr.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES;
        VkPhysicalDeviceFeatures2 features{};
        features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
        features.pNext = &ycbcr;
        api_.vkGetPhysicalDeviceFeatures2(physical_, &features);
        ycbcr_feature_ = ycbcr.samplerYcbcrConversion == VK_TRUE;
        // Only the multi-planar image feature is asked for; every other feature stays off.
        VkPhysicalDeviceSamplerYcbcrConversionFeatures enabled_ycbcr{};
        enabled_ycbcr.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES;
        enabled_ycbcr.samplerYcbcrConversion = ycbcr.samplerYcbcrConversion;
        VkPhysicalDeviceFeatures2 enabled{};
        enabled.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
        enabled.pNext = &enabled_ycbcr;
        const float priority = 1.0f;
        VkDeviceQueueCreateInfo queue{};
        queue.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
        queue.queueFamilyIndex = queue_family_;
        queue.queueCount = 1;
        queue.pQueuePriorities = &priority;
        VkDeviceCreateInfo info{};
        info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
        info.pNext = &enabled;
        info.queueCreateInfoCount = 1;
        info.pQueueCreateInfos = &queue;
        info.enabledExtensionCount = static_cast<uint32_t>(extensions.size());
        info.ppEnabledExtensionNames = extensions.data();
        const VkResult result = api_.vkCreateDevice(physical_, &info, nullptr, &device_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateDevice", result); return false; }
        api_.vkGetDeviceQueue(device_, queue_family_, 0, &queue_);
        api_.vkGetMemoryFdPropertiesKHR = reinterpret_cast<PFN_vkGetMemoryFdPropertiesKHR>(
            api_.get_instance_proc_addr(instance_, "vkGetMemoryFdPropertiesKHR"));
        if (!api_.vkGetMemoryFdPropertiesKHR) { error = "vkGetMemoryFdPropertiesKHR_missing"; return false; }
        // A comma-joined string, not an array: the facts travel as scalars under the ready packet's build.
        std::string extension_list = "\"";
        for (std::size_t i = 0; i < extensions.size(); ++i) extension_list += (i ? "," : "") + std::string(extensions[i]);
        extension_list += "\"";
        // Everything but the closing brace: diagnostics() appends the import strategy's facts.
        device_facts_ = std::string("{\"path\":\"gpu_convert\",\"device_name\":\"") + escape(properties_.deviceName) +
                        "\",\"device_type\":" + std::to_string(static_cast<int>(properties_.deviceType)) +
                        ",\"api_version\":\"" + std::to_string(VK_VERSION_MAJOR(properties_.apiVersion)) + "." +
                        std::to_string(VK_VERSION_MINOR(properties_.apiVersion)) + "." +
                        std::to_string(VK_VERSION_PATCH(properties_.apiVersion)) + "\",\"driver_version\":" +
                        std::to_string(properties_.driverVersion) + ",\"queue_family\":" + std::to_string(queue_family_) +
                        ",\"import\":\"" + (modifier_extension_ ? "drm_format_modifier" : "linear_copy_fallback") +
                        "\",\"image_format_list\":" + json_bool(format_list_available_) +
                        ",\"sampler_ycbcr_conversion_feature\":" + json_bool(ycbcr_feature_) +
                        ",\"device_extensions\":" + extension_list + ",\"shader_source_sha256\":\"" +
                        kCameraConvertSourceSha256 + "\",\"chroma_siting\":\"nearest\",\"fixed_point_bits\":16";
        return true;
    }

    // "undecided" before the first frame layout was seen, "none" when the driver accepted no strategy.
    std::string strategy_fact() const {
        if (!decision_.valid) return "undecided";
        return decision_.chosen ? strategy_name(decision_.strategy) : "none";
    }

    std::string diagnostics() const {
        const bool ycbcr = decision_.valid && decision_.chosen && decision_.strategy == ImportStrategy::ycbcr_sampler;
        return device_facts_ + ",\"import_strategy\":\"" + strategy_fact() + "\",\"import_strategy_results\":\"" +
               (decision_.valid ? decision_.results : std::string()) + "\",\"chroma_location\":\"" +
               (ycbcr ? chroma_location_name(decision_.chroma_location) : "not_applicable") + "\"}";
    }

    static std::string escape(const char* text) {
        std::string out;
        for (const char* p = text; *p; ++p) {
            const unsigned char c = static_cast<unsigned char>(*p);
            if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
            else if (c < 0x20 || c >= 0x7f) out += '?';
            else out += static_cast<char>(c);
        }
        return out;
    }

    // The objects every strategy shares: the plane-view sampler, the descriptor
    // pool, the command buffer and the fence.
    bool create_shared_objects(std::string& error) {
        VkSamplerCreateInfo sampler_info{};
        sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
        sampler_info.magFilter = VK_FILTER_NEAREST;
        sampler_info.minFilter = VK_FILTER_NEAREST;
        sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
        sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.unnormalizedCoordinates = VK_TRUE;
        VkResult result = api_.vkCreateSampler(device_, &sampler_info, nullptr, &sampler_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateSampler", result); return false; }
        // A multi-planar image under a sampler YCbCr conversion may take more than one descriptor (one a plane).
        VkDescriptorPoolSize sizes[2]{};
        sizes[0].type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        sizes[0].descriptorCount = 4;
        sizes[1].type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        sizes[1].descriptorCount = 1;
        VkDescriptorPoolCreateInfo pool_info{};
        pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
        pool_info.maxSets = 1;
        pool_info.poolSizeCount = 2;
        pool_info.pPoolSizes = sizes;
        result = api_.vkCreateDescriptorPool(device_, &pool_info, nullptr, &descriptor_pool_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateDescriptorPool", result); return false; }
        VkCommandPoolCreateInfo command_pool_info{};
        command_pool_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
        command_pool_info.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
        command_pool_info.queueFamilyIndex = queue_family_;
        result = api_.vkCreateCommandPool(device_, &command_pool_info, nullptr, &command_pool_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateCommandPool", result); return false; }
        VkCommandBufferAllocateInfo command_info{};
        command_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
        command_info.commandPool = command_pool_;
        command_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        command_info.commandBufferCount = 1;
        result = api_.vkAllocateCommandBuffers(device_, &command_info, &command_buffer_);
        if (result != VK_SUCCESS) { error = vk_error("vkAllocateCommandBuffers", result); return false; }
        VkFenceCreateInfo fence_info{};
        fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
        result = api_.vkCreateFence(device_, &fence_info, nullptr, &fence_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateFence", result); return false; }
        return true;
    }

    // ---- the strategies' pipelines -------------------------------------------------

    Program& program_slot(ImportStrategy strategy, SourceFormat format) {
        // The plane-view pipelines do not depend on the image format; the sampler conversion does.
        const int format_index = strategy == ImportStrategy::ycbcr_sampler && format == SourceFormat::p010 ? 1 : 0;
        return programs_[static_cast<int>(strategy)][format_index];
    }

    bool ensure_program(ImportStrategy strategy, SourceFormat format, VkChromaLocation chroma_location, Program*& out,
                        std::string& error) {
        Program& program = program_slot(strategy, format);
        out = &program;
        if (program.built && (strategy != ImportStrategy::ycbcr_sampler || program.chroma_location == chroma_location)) return true;
        destroy_program(program);
        program.chroma_location = chroma_location;
        if (!build_program(strategy, format, program, error)) {
            destroy_program(program);
            error = std::string(strategy_name(strategy)) + ":" + error;
            return false;
        }
        program.built = true;
        return true;
    }

    bool build_program(ImportStrategy strategy, SourceFormat format, Program& program, std::string& error) {
        const bool ycbcr = strategy == ImportStrategy::ycbcr_sampler;
        VkResult result = VK_SUCCESS;
        if (ycbcr) {
            if (!ycbcr_feature_) { error = "feature_missing:sampler_ycbcr_conversion"; return false; }
            // Nothing is converted: the model leaves the stored values as they are
            // (G is Y, B is Cb, R is Cr) and the shader applies the formula itself,
            // so the bytes do not depend on the driver's arithmetic.
            VkSamplerYcbcrConversionCreateInfo conversion_info{};
            conversion_info.sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_CREATE_INFO;
            conversion_info.format = plane_format(format).image_format;
            conversion_info.ycbcrModel = VK_SAMPLER_YCBCR_MODEL_CONVERSION_RGB_IDENTITY;
            conversion_info.ycbcrRange = VK_SAMPLER_YCBCR_RANGE_ITU_FULL;
            conversion_info.components = {VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY,
                                          VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY};
            conversion_info.xChromaOffset = program.chroma_location;
            conversion_info.yChromaOffset = program.chroma_location;
            conversion_info.chromaFilter = VK_FILTER_NEAREST;
            conversion_info.forceExplicitReconstruction = VK_FALSE;
            result = api_.vkCreateSamplerYcbcrConversion(device_, &conversion_info, nullptr, &program.conversion);
            if (result != VK_SUCCESS) { error = vk_error("vkCreateSamplerYcbcrConversion", result); return false; }
            VkSamplerYcbcrConversionInfo conversion{};
            conversion.sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_INFO;
            conversion.conversion = program.conversion;
            VkSamplerCreateInfo sampler_info{};
            sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
            sampler_info.pNext = &conversion;
            sampler_info.magFilter = VK_FILTER_NEAREST;
            sampler_info.minFilter = VK_FILTER_NEAREST;
            sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
            sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
            sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
            sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
            sampler_info.unnormalizedCoordinates = VK_FALSE;
            result = api_.vkCreateSampler(device_, &sampler_info, nullptr, &program.sampler);
            if (result != VK_SUCCESS) { error = vk_error("vkCreateSampler(ycbcr)", result); return false; }
        }
        // Bindings 0 and 1 are the planes (or binding 0 the whole image, its sampler immutable); 2 is the output.
        VkDescriptorSetLayoutBinding bindings[3]{};
        uint32_t binding_count = 0;
        for (uint32_t i = 0; i < 3; ++i) {
            if (ycbcr && i == 1) continue;
            VkDescriptorSetLayoutBinding& binding = bindings[binding_count++];
            binding.binding = i;
            binding.descriptorCount = 1;
            binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
            binding.descriptorType = i < 2 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            if (ycbcr && i == 0) binding.pImmutableSamplers = &program.sampler;
        }
        VkDescriptorSetLayoutCreateInfo layout_info{};
        layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
        layout_info.bindingCount = binding_count;
        layout_info.pBindings = bindings;
        result = api_.vkCreateDescriptorSetLayout(device_, &layout_info, nullptr, &program.set_layout);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateDescriptorSetLayout", result); return false; }
        VkPushConstantRange range{};
        range.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        range.size = sizeof(PushConstants);
        VkPipelineLayoutCreateInfo pipeline_layout_info{};
        pipeline_layout_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
        pipeline_layout_info.setLayoutCount = 1;
        pipeline_layout_info.pSetLayouts = &program.set_layout;
        pipeline_layout_info.pushConstantRangeCount = 1;
        pipeline_layout_info.pPushConstantRanges = &range;
        result = api_.vkCreatePipelineLayout(device_, &pipeline_layout_info, nullptr, &program.pipeline_layout);
        if (result != VK_SUCCESS) { error = vk_error("vkCreatePipelineLayout", result); return false; }
        VkShaderModuleCreateInfo shader_info{};
        shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
        switch (strategy) {
            case ImportStrategy::plane_views_uint:
                shader_info.codeSize = kCameraConvertSpirvWords * sizeof(uint32_t);
                shader_info.pCode = kCameraConvertSpirv;
                break;
            case ImportStrategy::plane_views_unorm:
                shader_info.codeSize = kCameraConvertUnormSpirvWords * sizeof(uint32_t);
                shader_info.pCode = kCameraConvertUnormSpirv;
                break;
            case ImportStrategy::ycbcr_sampler:
                shader_info.codeSize = kCameraConvertYcbcrSpirvWords * sizeof(uint32_t);
                shader_info.pCode = kCameraConvertYcbcrSpirv;
                break;
        }
        VkShaderModule shader = VK_NULL_HANDLE;
        result = api_.vkCreateShaderModule(device_, &shader_info, nullptr, &shader);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateShaderModule", result); return false; }
        VkComputePipelineCreateInfo pipeline_info{};
        pipeline_info.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
        pipeline_info.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        pipeline_info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        pipeline_info.stage.module = shader;
        pipeline_info.stage.pName = "main";
        pipeline_info.layout = program.pipeline_layout;
        result = api_.vkCreateComputePipelines(device_, VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &program.pipeline);
        api_.vkDestroyShaderModule(device_, shader, nullptr);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateComputePipelines", result); return false; }
        return true;
    }

    void destroy_program(Program& program) {
        if (device_ != VK_NULL_HANDLE) {
            if (program.pipeline) api_.vkDestroyPipeline(device_, program.pipeline, nullptr);
            if (program.pipeline_layout) api_.vkDestroyPipelineLayout(device_, program.pipeline_layout, nullptr);
            if (program.set_layout) api_.vkDestroyDescriptorSetLayout(device_, program.set_layout, nullptr);
            if (program.sampler) api_.vkDestroySampler(device_, program.sampler, nullptr);
            if (program.conversion) api_.vkDestroySamplerYcbcrConversion(device_, program.conversion, nullptr);
        }
        program = Program();
    }

    // ---- what the driver accepts ---------------------------------------------------

    // The driver's modifier list for a format (empty without the modifier extension) and its plain properties.
    void format_properties(VkFormat format, VkFormatProperties& plain, std::vector<VkDrmFormatModifierPropertiesEXT>& known) const {
        VkDrmFormatModifierPropertiesListEXT modifiers{};
        modifiers.sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT;
        VkFormatProperties2 properties{};
        properties.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2;
        if (modifier_extension_) properties.pNext = &modifiers;
        api_.vkGetPhysicalDeviceFormatProperties2(physical_, format, &properties);
        known.assign(modifiers.drmFormatModifierCount, VkDrmFormatModifierPropertiesEXT{});
        if (modifier_extension_ && !known.empty()) {
            modifiers.pDrmFormatModifierProperties = known.data();
            api_.vkGetPhysicalDeviceFormatProperties2(physical_, format, &properties);
            known.resize(modifiers.drmFormatModifierCount);
        }
        plain = properties.formatProperties;
    }

    TilingFacts modifier_facts(const std::vector<VkDrmFormatModifierPropertiesEXT>& known, uint64_t modifier) const {
        TilingFacts facts;
        for (const VkDrmFormatModifierPropertiesEXT& entry : known) {
            if (entry.drmFormatModifier != modifier) continue;
            facts.listed = true;
            facts.plane_count = entry.drmFormatModifierPlaneCount;
            facts.features = entry.drmFormatModifierTilingFeatures;
        }
        return facts;
    }

    // One vkGetPhysicalDeviceImageFormatProperties2 call: a 2D image of the
    // format with a modifier tiling (and DMA-BUF external memory) or optimal
    // tiling, with or without MUTABLE_FORMAT and a list of per-plane view formats.
    ImageQueryResult query_image(const PlaneFormat& formats, bool modifier_tiling, uint64_t modifier, bool mutable_format,
                                 ViewFormats views, VkImageUsageFlags usage) const {
        const VkFormat uint_views[2] = {formats.luma_uint, formats.chroma_uint};
        const VkFormat unorm_views[2] = {formats.luma_unorm, formats.chroma_unorm};
        VkImageFormatListCreateInfo format_list{};
        format_list.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO;
        format_list.viewFormatCount = 2;
        format_list.pViewFormats = views == ViewFormats::unorm_views ? unorm_views : uint_views;
        const bool with_list = views != ViewFormats::none && format_list_available_;
        VkPhysicalDeviceImageDrmFormatModifierInfoEXT modifier_query{};
        modifier_query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT;
        if (with_list) modifier_query.pNext = &format_list;
        modifier_query.drmFormatModifier = modifier;
        modifier_query.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkPhysicalDeviceExternalImageFormatInfo external_query{};
        external_query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO;
        external_query.pNext = &modifier_query;
        external_query.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkPhysicalDeviceImageFormatInfo2 query{};
        query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2;
        if (modifier_tiling) query.pNext = &external_query;
        else if (with_list) query.pNext = &format_list;
        query.format = formats.image_format;
        query.type = VK_IMAGE_TYPE_2D;
        query.tiling = modifier_tiling ? VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT : VK_IMAGE_TILING_OPTIMAL;
        query.usage = usage;
        query.flags = mutable_format ? VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT : 0;
        VkExternalImageFormatProperties external_properties{};
        external_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES;
        VkImageFormatProperties2 image_properties{};
        image_properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
        if (modifier_tiling) image_properties.pNext = &external_properties;
        ImageQueryResult out;
        out.result = api_.vkGetPhysicalDeviceImageFormatProperties2(physical_, &query, &image_properties);
        if (out.result == VK_SUCCESS) {
            out.max_extent = image_properties.imageFormatProperties.maxExtent;
            out.external_features = external_properties.externalMemoryProperties.externalMemoryFeatures;
            out.compatible_handle_types = external_properties.externalMemoryProperties.compatibleHandleTypes;
        }
        return out;
    }

    ImageQueryResult query_strategy(ImportStrategy strategy, const PlaneFormat& formats, bool modifier_tiling,
                                    uint64_t modifier) const {
        // The fallback's image is also the destination of the plane copies.
        const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | (modifier_tiling ? 0 : VK_IMAGE_USAGE_TRANSFER_DST_BIT);
        return query_image(formats, modifier_tiling, modifier, strategy_uses_plane_views(strategy), strategy_views(strategy), usage);
    }

    static bool chroma_location_for(VkFormatFeatureFlags features, VkChromaLocation& location) {
        // Whichever the format offers, midpoint first; the shader's chroma coordinate suits both.
        if (features & VK_FORMAT_FEATURE_MIDPOINT_CHROMA_SAMPLES_BIT) { location = VK_CHROMA_LOCATION_MIDPOINT; return true; }
        if (features & VK_FORMAT_FEATURE_COSITED_CHROMA_SAMPLES_BIT) { location = VK_CHROMA_LOCATION_COSITED_EVEN; return true; }
        return false;
    }

    // Whether a strategy can import a frame of this layout: "ok" or the reason it cannot.
    std::string try_strategy(ImportStrategy strategy, const PlaneFormat& formats, bool modifier_tiling, uint64_t modifier,
                             VkFormatFeatureFlags features, uint32_t width, uint32_t height, VkChromaLocation& chroma_location) const {
        if (strategy == ImportStrategy::ycbcr_sampler) {
            if (!ycbcr_feature_) return "feature_missing:sampler_ycbcr_conversion";
            if (!chroma_location_for(features, chroma_location)) return "feature_missing:chroma_samples";
        }
        const ImageQueryResult answer = query_strategy(strategy, formats, modifier_tiling, modifier);
        if (answer.result != VK_SUCCESS) return vk_result(answer.result);
        if (modifier_tiling && !(answer.external_features & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)) return "dma_buf_not_importable";
        if (width > answer.max_extent.width || height > answer.max_extent.height) return "frame_exceeds_device_image_extent";
        return "ok";
    }

    // Chooses the strategy for this frame layout: the first, in order, that the
    // driver's image-format query accepts and whose features allow it. Every
    // strategy is asked, so the facts name each answer. Kept while the layout stays.
    bool decide(const SourceFrame& frame, std::string& error) {
        if (decision_.valid && decision_.format == frame.format && decision_.modifier == frame.drm_modifier &&
            decision_.plane_count == frame.plane_count && decision_.width == frame.width && decision_.height == frame.height) {
            if (!decision_.chosen) error = decision_.error;
            return decision_.chosen;
        }
        decision_ = Decision();
        decision_.valid = true;
        decision_.format = frame.format;
        decision_.modifier = frame.drm_modifier;
        decision_.plane_count = frame.plane_count;
        decision_.width = frame.width;
        decision_.height = frame.height;
        const PlaneFormat formats = plane_format(frame.format);
        VkFormatProperties plain{};
        std::vector<VkDrmFormatModifierPropertiesEXT> known;
        format_properties(formats.image_format, plain, known);
        VkFormatFeatureFlags features = plain.optimalTilingFeatures;
        if (modifier_extension_) {
            // The driver must know this modifier for this format with the frame's plane count.
            const TilingFacts facts = modifier_facts(known, frame.drm_modifier);
            if (!facts.listed) {
                decision_.error = "modifier_unknown_to_driver:" + hex_u64(frame.drm_modifier, 16);
            } else if (facts.plane_count != frame.plane_count) {
                decision_.error = "modifier_plane_count_disagrees_with_driver:" + std::to_string(frame.plane_count) + "!=" +
                                  std::to_string(facts.plane_count);
            } else if (!(facts.features & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT)) {
                decision_.error = "modifier_not_sampleable:" + hex_u64(frame.drm_modifier, 16);
            }
            features = facts.features;
        } else if (!modifier_is_linear(frame.drm_modifier)) {
            decision_.error = "modifier_import_unsupported_by_device:" + hex_u64(frame.drm_modifier, 16);
        }
        if (!decision_.error.empty()) { error = decision_.error; return false; }
        for (ImportStrategy strategy : kStrategies) {
            if (!only_strategy_.empty() && only_strategy_ != strategy_name(strategy)) continue;
            VkChromaLocation chroma_location = VK_CHROMA_LOCATION_MIDPOINT;
            const std::string answer = try_strategy(strategy, formats, modifier_extension_, frame.drm_modifier, features,
                                                    frame.width, frame.height, chroma_location);
            decision_.results += (decision_.results.empty() ? "" : ",") + std::string(strategy_name(strategy)) + "=" + answer;
            if (answer == "ok" && !decision_.chosen) {
                decision_.chosen = true;
                decision_.strategy = strategy;
                decision_.chroma_location = chroma_location;
            }
        }
        if (!decision_.chosen) {
            decision_.error = "no_import_strategy:" + decision_.results;
            error = decision_.error;
        }
        return decision_.chosen;
    }

    // ---- memory ------------------------------------------------------------------

    bool find_memory_type(uint32_t type_bits, VkMemoryPropertyFlags required, uint32_t& index) const {
        for (uint32_t i = 0; i < memory_properties_.memoryTypeCount; ++i) {
            if ((type_bits & (1u << i)) && (memory_properties_.memoryTypes[i].propertyFlags & required) == required) {
                index = i;
                return true;
            }
        }
        return false;
    }

    static bool descriptor_size(int fd, uint64_t& size, std::string& error) {
        struct stat status{};
        if (::fstat(fd, &status) != 0) { error = "fstat_failed:errno=" + std::to_string(errno); return false; }
        if (status.st_size <= 0) { error = "buffer_size_unknown"; return false; }
        size = static_cast<uint64_t>(status.st_size);
        return true;
    }

    // Imports the descriptor (a duplicate; the lease keeps the original) as the
    // dedicated memory of image or buffer. On success Vulkan owns the duplicate.
    bool import_memory(int fd, uint64_t fd_size, const VkMemoryRequirements& requirements, VkImage image,
                       VkBuffer buffer, VkDeviceMemory& memory, std::string& error) {
        if (fd_size < requirements.size) {
            error = "buffer_smaller_than_driver_layout:" + std::to_string(fd_size) + "<" + std::to_string(requirements.size);
            return false;
        }
        const int duplicate = ::dup(fd);
        if (duplicate < 0) { error = "dup_failed:errno=" + std::to_string(errno); return false; }
        VkMemoryFdPropertiesKHR fd_properties{};
        fd_properties.sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR;
        VkResult result = api_.vkGetMemoryFdPropertiesKHR(device_, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, duplicate,
                                                          &fd_properties);
        if (result != VK_SUCCESS) { ::close(duplicate); error = vk_error("vkGetMemoryFdPropertiesKHR", result); return false; }
        uint32_t type_index = 0;
        if (!find_memory_type(fd_properties.memoryTypeBits & requirements.memoryTypeBits, 0, type_index)) {
            ::close(duplicate);
            error = "no_memory_type_for_dma_buf_import";
            return false;
        }
        VkMemoryDedicatedAllocateInfo dedicated{};
        dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
        dedicated.image = image;
        dedicated.buffer = buffer;
        VkImportMemoryFdInfoKHR import{};
        import.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR;
        import.pNext = &dedicated;
        import.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        import.fd = duplicate;
        VkMemoryAllocateInfo allocate{};
        allocate.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
        allocate.pNext = &import;
        allocate.allocationSize = fd_size;
        allocate.memoryTypeIndex = type_index;
        result = api_.vkAllocateMemory(device_, &allocate, nullptr, &memory);
        if (result != VK_SUCCESS) { ::close(duplicate); error = vk_error("vkAllocateMemory(import)", result); return false; }
        return true;
    }

    // ---- the import stage ----------------------------------------------------------

    bool import_frame(const SourceFrame& frame, FrameResources& resources, std::string& error) {
        if (modifier_extension_) return import_modifier(frame, resources, error);
        return import_linear_copy(frame, resources, error);
    }

    // The image of a strategy: MUTABLE_FORMAT with the per-plane view formats
    // listed for the plane-view strategies, neither for ycbcr_sampler. The
    // format list, when used, is linked in front of chain.
    struct ImageDescription {
        VkFormat view_formats[2];
        VkImageFormatListCreateInfo format_list{};
        VkImageCreateInfo info{};
    };
    void describe_image(ImportStrategy strategy, const PlaneFormat& formats, uint32_t width, uint32_t height,
                        const void* chain, ImageDescription& description) const {
        const bool unorm = strategy == ImportStrategy::plane_views_unorm;
        description.view_formats[0] = unorm ? formats.luma_unorm : formats.luma_uint;
        description.view_formats[1] = unorm ? formats.chroma_unorm : formats.chroma_uint;
        description.format_list.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO;
        description.format_list.pNext = chain;
        description.format_list.viewFormatCount = 2;
        description.format_list.pViewFormats = description.view_formats;
        const bool planes = strategy_uses_plane_views(strategy);
        VkImageCreateInfo& info = description.info;
        info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
        info.pNext = planes && format_list_available_ ? &description.format_list : chain;
        info.flags = planes ? VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT : 0;
        info.imageType = VK_IMAGE_TYPE_2D;
        info.format = formats.image_format;
        info.extent = {width, height, 1};
        info.mipLevels = 1;
        info.arrayLayers = 1;
        info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
        info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    }

    // An image over a DMA-BUF: the modifier and the plane table describe the layout.
    VkResult create_modifier_image(ImportStrategy strategy, const PlaneFormat& formats, uint64_t modifier, uint32_t width,
                                   uint32_t height, uint32_t plane_count, const PlaneMeta* planes, VkImage& image) const {
        VkSubresourceLayout layouts[kMaxPlanes]{};
        for (uint32_t plane = 0; plane < plane_count && plane < kMaxPlanes; ++plane) {
            layouts[plane].offset = planes[plane].offset;
            layouts[plane].size = planes[plane].size;
            layouts[plane].rowPitch = planes[plane].stride;
        }
        VkImageDrmFormatModifierExplicitCreateInfoEXT explicit_layout{};
        explicit_layout.sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT;
        explicit_layout.drmFormatModifier = modifier;
        explicit_layout.drmFormatModifierPlaneCount = plane_count;
        explicit_layout.pPlaneLayouts = layouts;
        VkExternalMemoryImageCreateInfo external{};
        external.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
        external.pNext = &explicit_layout;
        external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        ImageDescription description;
        describe_image(strategy, formats, width, height, &external, description);
        description.info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
        return api_.vkCreateImage(device_, &description.info, nullptr, &image);
    }

    // The device path: the DMA-BUF becomes the image's memory, with the frame's
    // modifier and plane table describing the layout. The queue-family
    // ownership acquire from the foreign producer carries the contents in.
    // decide() has checked the modifier and the strategy against the driver.
    bool import_modifier(const SourceFrame& frame, FrameResources& resources, std::string& error) {
        const PlaneFormat formats = plane_format(frame.format);
        VkResult result = create_modifier_image(decision_.strategy, formats, frame.drm_modifier, frame.width, frame.height,
                                                frame.plane_count, frame.planes, resources.image);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateImage(modifier)", result); return false; }

        VkMemoryRequirements requirements{};
        image_requirements(resources.image, requirements);
        uint64_t fd_size = 0;
        if (!descriptor_size(frame.fd, fd_size, error)) return false;
        if (!import_memory(frame.fd, fd_size, requirements, resources.image, VK_NULL_HANDLE, resources.image_memory, error)) return false;
        result = api_.vkBindImageMemory(device_, resources.image, resources.image_memory, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkBindImageMemory", result); return false; }
        resources.copy_from_buffer = false;
        return create_views(frame, formats, resources, error);
    }

    // The fallback for devices without the modifier extension: the DMA-BUF is
    // imported as a buffer and each plane is copied into an optimally tiled
    // image with the frame's own offsets and strides.
    bool import_linear_copy(const SourceFrame& frame, FrameResources& resources, std::string& error) {
        const PlaneFormat formats = plane_format(frame.format);
        const uint32_t row_bytes = frame.width * formats.luma_texel_bytes;
        if (frame.planes[0].stride < row_bytes || frame.planes[1].stride < row_bytes) { error = "plane_stride_too_small"; return false; }
        if (frame.planes[0].stride % formats.luma_texel_bytes || frame.planes[1].stride % formats.chroma_texel_bytes) {
            error = "plane_stride_not_texel_aligned";
            return false;
        }
        // The same plane checks as the CPU path, before any copy: each plane holds its
        // rows at its stride, and each plane lies within the descriptor.
        const uint64_t luma_needed = static_cast<uint64_t>(frame.planes[0].stride) * (frame.height - 1) + row_bytes;
        const uint64_t chroma_needed = static_cast<uint64_t>(frame.planes[1].stride) * (frame.height / 2 - 1) + row_bytes;
        if (frame.planes[0].size < luma_needed || frame.planes[1].size < chroma_needed) { error = "plane_size_too_small"; return false; }
        uint64_t fd_size = 0;
        if (!descriptor_size(frame.fd, fd_size, error)) return false;
        for (uint32_t plane = 0; plane < 2; ++plane) {
            if (static_cast<uint64_t>(frame.planes[plane].offset) + frame.planes[plane].size > fd_size) {
                error = "planes_exceed_buffer_size";
                return false;
            }
        }
        VkExternalMemoryBufferCreateInfo external{};
        external.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO;
        external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkBufferCreateInfo buffer_info{};
        buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
        buffer_info.pNext = &external;
        buffer_info.size = fd_size;
        buffer_info.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
        buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkResult result = api_.vkCreateBuffer(device_, &buffer_info, nullptr, &resources.source_buffer);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateBuffer(import)", result); return false; }
        VkBufferMemoryRequirementsInfo2 requirements_info{};
        requirements_info.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_REQUIREMENTS_INFO_2;
        requirements_info.buffer = resources.source_buffer;
        VkMemoryRequirements2 requirements{};
        requirements.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
        api_.vkGetBufferMemoryRequirements2(device_, &requirements_info, &requirements);
        if (!import_memory(frame.fd, fd_size, requirements.memoryRequirements, VK_NULL_HANDLE, resources.source_buffer,
                           resources.source_memory, error)) return false;
        result = api_.vkBindBufferMemory(device_, resources.source_buffer, resources.source_memory, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkBindBufferMemory", result); return false; }

        ImageDescription description;
        describe_image(decision_.strategy, formats, frame.width, frame.height, nullptr, description);
        description.info.tiling = VK_IMAGE_TILING_OPTIMAL;
        description.info.usage |= VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        result = api_.vkCreateImage(device_, &description.info, nullptr, &resources.image);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateImage(optimal)", result); return false; }
        VkMemoryRequirements image_needs{};
        image_requirements(resources.image, image_needs);
        uint32_t type_index = 0;
        if (!find_memory_type(image_needs.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, type_index) &&
            !find_memory_type(image_needs.memoryTypeBits, 0, type_index)) {
            error = "no_memory_type_for_image";
            return false;
        }
        VkMemoryAllocateInfo allocate{};
        allocate.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
        allocate.allocationSize = image_needs.size;
        allocate.memoryTypeIndex = type_index;
        result = api_.vkAllocateMemory(device_, &allocate, nullptr, &resources.image_memory);
        if (result != VK_SUCCESS) { error = vk_error("vkAllocateMemory(image)", result); return false; }
        result = api_.vkBindImageMemory(device_, resources.image, resources.image_memory, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkBindImageMemory", result); return false; }
        resources.copy_from_buffer = true;
        return create_views(frame, formats, resources, error);
    }

    void image_requirements(VkImage image, VkMemoryRequirements& requirements) const {
        VkImageMemoryRequirementsInfo2 info{};
        info.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2;
        info.image = image;
        VkMemoryRequirements2 out{};
        out.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
        api_.vkGetImageMemoryRequirements2(device_, &info, &out);
        requirements = out.memoryRequirements;
    }

    // Two plane views for the plane-view strategies; for ycbcr_sampler one view
    // of the whole image with the conversion of the strategy's immutable sampler.
    bool create_views(const SourceFrame& frame, const PlaneFormat& formats, FrameResources& resources, std::string& error) {
        if (decision_.strategy == ImportStrategy::ycbcr_sampler) {
            VkSamplerYcbcrConversionInfo conversion{};
            conversion.sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_INFO;
            conversion.conversion = program_slot(decision_.strategy, frame.format).conversion;
            VkImageViewCreateInfo info{};
            info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
            info.pNext = &conversion;
            info.image = resources.image;
            info.viewType = VK_IMAGE_VIEW_TYPE_2D;
            info.format = formats.image_format;
            info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
            info.subresourceRange.levelCount = 1;
            info.subresourceRange.layerCount = 1;
            const VkResult result = api_.vkCreateImageView(device_, &info, nullptr, &resources.luma_view);
            if (result != VK_SUCCESS) { error = vk_error("vkCreateImageView(ycbcr)", result); return false; }
            return true;
        }
        const bool unorm = decision_.strategy == ImportStrategy::plane_views_unorm;
        const struct { VkImageAspectFlags aspect; VkFormat format; VkImageView* view; } views[2] = {
            {VK_IMAGE_ASPECT_PLANE_0_BIT, unorm ? formats.luma_unorm : formats.luma_uint, &resources.luma_view},
            {VK_IMAGE_ASPECT_PLANE_1_BIT, unorm ? formats.chroma_unorm : formats.chroma_uint, &resources.chroma_view},
        };
        for (const auto& entry : views) {
            VkImageViewCreateInfo info{};
            info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
            info.image = resources.image;
            info.viewType = VK_IMAGE_VIEW_TYPE_2D;
            info.format = entry.format;
            info.subresourceRange.aspectMask = entry.aspect;
            info.subresourceRange.levelCount = 1;
            info.subresourceRange.layerCount = 1;
            const VkResult result = api_.vkCreateImageView(device_, &info, nullptr, entry.view);
            if (result != VK_SUCCESS) { error = vk_error("vkCreateImageView(plane)", result); return false; }
        }
        return true;
    }

    // ---- the conversion stage ------------------------------------------------------

    bool ensure_output(uint64_t bytes, std::string& error) {
        if (output_buffer_ != VK_NULL_HANDLE && output_capacity_ >= bytes) return true;
        destroy_output();
        VkBufferCreateInfo info{};
        info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
        info.size = bytes;
        info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
        info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkResult result = api_.vkCreateBuffer(device_, &info, nullptr, &output_buffer_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateBuffer(output)", result); return false; }
        VkBufferMemoryRequirementsInfo2 requirements_info{};
        requirements_info.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_REQUIREMENTS_INFO_2;
        requirements_info.buffer = output_buffer_;
        VkMemoryRequirements2 requirements{};
        requirements.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
        api_.vkGetBufferMemoryRequirements2(device_, &requirements_info, &requirements);
        uint32_t type_index = 0;
        const VkMemoryPropertyFlags preferred = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT |
                                                VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
        if (!find_memory_type(requirements.memoryRequirements.memoryTypeBits, preferred, type_index) &&
            !find_memory_type(requirements.memoryRequirements.memoryTypeBits,
                              VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, type_index) &&
            !find_memory_type(requirements.memoryRequirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT, type_index)) {
            error = "no_host_visible_memory_type";
            return false;
        }
        output_coherent_ = (memory_properties_.memoryTypes[type_index].propertyFlags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) != 0;
        VkMemoryAllocateInfo allocate{};
        allocate.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
        allocate.allocationSize = requirements.memoryRequirements.size;
        allocate.memoryTypeIndex = type_index;
        result = api_.vkAllocateMemory(device_, &allocate, nullptr, &output_memory_);
        if (result != VK_SUCCESS) { error = vk_error("vkAllocateMemory(output)", result); return false; }
        result = api_.vkBindBufferMemory(device_, output_buffer_, output_memory_, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkBindBufferMemory(output)", result); return false; }
        result = api_.vkMapMemory(device_, output_memory_, 0, VK_WHOLE_SIZE, 0, &output_mapped_);
        if (result != VK_SUCCESS) { error = vk_error("vkMapMemory(output)", result); return false; }
        output_capacity_ = bytes;
        return true;
    }

    bool dispatch_and_read(const SourceFrame& frame, const FrameResources& resources, const Program& program, BgrImage& out,
                           uint64_t payload, std::string& error) {
        const PlaneFormat formats = plane_format(frame.format);
        const bool ycbcr = decision_.strategy == ImportStrategy::ycbcr_sampler;
        VkResult result = api_.vkResetDescriptorPool(device_, descriptor_pool_, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkResetDescriptorPool", result); return false; }
        VkDescriptorSetAllocateInfo set_info{};
        set_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
        set_info.descriptorPool = descriptor_pool_;
        set_info.descriptorSetCount = 1;
        set_info.pSetLayouts = &program.set_layout;
        VkDescriptorSet set = VK_NULL_HANDLE;
        result = api_.vkAllocateDescriptorSets(device_, &set_info, &set);
        if (result != VK_SUCCESS) { error = vk_error("vkAllocateDescriptorSets", result); return false; }
        VkDescriptorImageInfo images[2]{};
        images[0].sampler = ycbcr ? VK_NULL_HANDLE : sampler_;  // the ycbcr_sampler's own is immutable in the layout
        images[0].imageView = resources.luma_view;
        images[0].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        images[1].sampler = sampler_;
        images[1].imageView = resources.chroma_view;
        images[1].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        VkDescriptorBufferInfo buffer{};
        buffer.buffer = output_buffer_;
        buffer.range = payload;
        VkWriteDescriptorSet writes[3]{};
        uint32_t write_count = 0;
        for (uint32_t i = 0; i < 3; ++i) {
            if (ycbcr && i == 1) continue;
            VkWriteDescriptorSet& write = writes[write_count++];
            write.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            write.dstSet = set;
            write.dstBinding = i;
            write.descriptorCount = 1;
            write.descriptorType = i < 2 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            if (i < 2) write.pImageInfo = &images[i];
            else write.pBufferInfo = &buffer;
        }
        api_.vkUpdateDescriptorSets(device_, write_count, writes, 0, nullptr);

        result = api_.vkResetCommandPool(device_, command_pool_, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkResetCommandPool", result); return false; }
        VkCommandBufferBeginInfo begin{};
        begin.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        result = api_.vkBeginCommandBuffer(command_buffer_, &begin);
        if (result != VK_SUCCESS) { error = vk_error("vkBeginCommandBuffer", result); return false; }

        VkImageMemoryBarrier barrier{};
        barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
        barrier.image = resources.image;
        barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        barrier.subresourceRange.levelCount = 1;
        barrier.subresourceRange.layerCount = 1;
        if (resources.copy_from_buffer) {
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            barrier.srcAccessMask = 0;
            barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            api_.vkCmdPipelineBarrier(command_buffer_, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0,
                                      0, nullptr, 0, nullptr, 1, &barrier);
            VkBufferImageCopy copies[2]{};
            const VkImageAspectFlags aspects[2] = {VK_IMAGE_ASPECT_PLANE_0_BIT, VK_IMAGE_ASPECT_PLANE_1_BIT};
            const uint32_t texel_bytes[2] = {formats.luma_texel_bytes, formats.chroma_texel_bytes};
            for (uint32_t plane = 0; plane < 2; ++plane) {
                copies[plane].bufferOffset = frame.planes[plane].offset;
                copies[plane].bufferRowLength = frame.planes[plane].stride / texel_bytes[plane];
                copies[plane].imageSubresource.aspectMask = aspects[plane];
                copies[plane].imageSubresource.layerCount = 1;
                copies[plane].imageExtent = {plane ? frame.width / 2 : frame.width, plane ? frame.height / 2 : frame.height, 1};
            }
            api_.vkCmdCopyBufferToImage(command_buffer_, resources.source_buffer, resources.image,
                                        VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 2, copies);
            barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
            barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            api_.vkCmdPipelineBarrier(command_buffer_, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0,
                                      0, nullptr, 0, nullptr, 1, &barrier);
        } else {
            // Acquire the buffer from its foreign producer; the contents are preserved.
            barrier.srcQueueFamilyIndex = foreign_queue_extension_ ? VK_QUEUE_FAMILY_FOREIGN_EXT : VK_QUEUE_FAMILY_EXTERNAL;
            barrier.dstQueueFamilyIndex = queue_family_;
            barrier.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
            barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
            barrier.srcAccessMask = 0;
            barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            api_.vkCmdPipelineBarrier(command_buffer_, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0,
                                      0, nullptr, 0, nullptr, 1, &barrier);
        }

        const YcbcrCoefficients coefficients = ycbcr_coefficients(frame.encoding);
        PushConstants constants{};
        constants.width = frame.width;
        constants.height = frame.height;
        constants.stride_words = bgr_stride_bytes(frame.width) / 4;
        constants.sample_shift = source_sample_shift(frame.format);
        constants.sample_max = source_sample_max(frame.format);
        constants.cr_r = coefficients.cr_r;
        constants.cb_g = coefficients.cb_g;
        constants.cr_g = coefficients.cr_g;
        constants.cb_b = coefficients.cb_b;
        api_.vkCmdBindPipeline(command_buffer_, VK_PIPELINE_BIND_POINT_COMPUTE, program.pipeline);
        api_.vkCmdBindDescriptorSets(command_buffer_, VK_PIPELINE_BIND_POINT_COMPUTE, program.pipeline_layout, 0, 1, &set, 0, nullptr);
        api_.vkCmdPushConstants(command_buffer_, program.pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof constants, &constants);
        const uint32_t groups = (frame.width + 3) / 4;
        api_.vkCmdDispatch(command_buffer_, (groups + 15) / 16, (frame.height + 15) / 16, 1);
        VkBufferMemoryBarrier host_barrier{};
        host_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
        host_barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        host_barrier.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        host_barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        host_barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        host_barrier.buffer = output_buffer_;
        host_barrier.size = payload;
        api_.vkCmdPipelineBarrier(command_buffer_, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr,
                                  1, &host_barrier, 0, nullptr);
        result = api_.vkEndCommandBuffer(command_buffer_);
        if (result != VK_SUCCESS) { error = vk_error("vkEndCommandBuffer", result); return false; }

        result = api_.vkResetFences(device_, 1, &fence_);
        if (result != VK_SUCCESS) { error = vk_error("vkResetFences", result); return false; }
        VkSubmitInfo submit{};
        submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &command_buffer_;
        result = api_.vkQueueSubmit(queue_, 1, &submit, fence_);
        if (result != VK_SUCCESS) { error = vk_error("vkQueueSubmit", result); return false; }
        result = api_.vkWaitForFences(device_, 1, &fence_, VK_TRUE, kFenceTimeoutNs);
        if (result != VK_SUCCESS) {
            error = vk_error("vkWaitForFences", result);
            api_.vkDeviceWaitIdle(device_);  // never free an image the queue may still read
            return false;
        }
        if (!output_coherent_) {
            VkMappedMemoryRange range{};
            range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
            range.memory = output_memory_;
            range.size = VK_WHOLE_SIZE;
            result = api_.vkInvalidateMappedMemoryRanges(device_, 1, &range);
            if (result != VK_SUCCESS) { error = vk_error("vkInvalidateMappedMemoryRanges", result); return false; }
        }
        out.width = frame.width;
        out.height = frame.height;
        out.stride_bytes = bgr_stride_bytes(frame.width);
        out.pixels.resize(static_cast<std::size_t>(payload));
        std::memcpy(out.pixels.data(), output_mapped_, static_cast<std::size_t>(payload));
        ++frames_;
        return true;
    }

    // ---- the query (--query-gpu) -----------------------------------------------------

    // Whether each strategy's pipeline builds on this device. The sampler
    // conversion is created with the chroma location the format's features allow.
    std::string query_programs() {
        std::string out = "{";
        const struct { ImportStrategy strategy; SourceFormat format; const char* key; } entries[4] = {
            {ImportStrategy::plane_views_uint, SourceFormat::nv12, "plane_views_uint"},
            {ImportStrategy::plane_views_unorm, SourceFormat::nv12, "plane_views_unorm"},
            {ImportStrategy::ycbcr_sampler, SourceFormat::nv12, "ycbcr_sampler_nv12"},
            {ImportStrategy::ycbcr_sampler, SourceFormat::p010, "ycbcr_sampler_p010"},
        };
        for (int index = 0; index < 4; ++index) {
            std::string answer = "ok";
            VkChromaLocation location = VK_CHROMA_LOCATION_MIDPOINT;
            if (entries[index].strategy == ImportStrategy::ycbcr_sampler) {
                VkFormatProperties plain{};
                std::vector<VkDrmFormatModifierPropertiesEXT> known;
                format_properties(plane_format(entries[index].format).image_format, plain, known);
                const TilingFacts compressed = modifier_facts(known, kDrmModifierQcomCompressed);
                const VkFormatFeatureFlags features = compressed.listed ? compressed.features : plain.optimalTilingFeatures;
                if (!ycbcr_feature_) answer = "feature_missing:sampler_ycbcr_conversion";
                else if (!chroma_location_for(features, location)) answer = "feature_missing:chroma_samples";
            }
            if (answer == "ok") {
                Program* program = nullptr;
                std::string error;
                if (!ensure_program(entries[index].strategy, entries[index].format, location, program, error)) answer = error;
                else if (entries[index].strategy == ImportStrategy::ycbcr_sampler) answer = std::string("ok:") + chroma_location_name(location);
            }
            out += std::string(index ? "," : "") + "\"" + entries[index].key + "\":\"" + escape(answer.c_str()) + "\"";
        }
        return out + "}";
    }

    std::string query_tiling(const PlaneFormat& formats, bool modifier_tiling, uint64_t modifier, const TilingFacts& facts) const {
        const VkFormatFeatureFlags features = facts.features;
        std::string out = std::string("{\"tiling\":\"") + (modifier_tiling ? "drm_format_modifier" : "optimal") + "\",\"modifier\":" +
                          (modifier_tiling ? "\"" + hex_u64(modifier, 16) + "\"" : std::string("null")) +
                          ",\"listed\":" + json_bool(facts.listed) + ",\"plane_count\":" + std::to_string(facts.plane_count) +
                          ",\"features\":\"" + hex_u32(features) + "\"";
        const struct { const char* key; VkFormatFeatureFlags bit; } bits[8] = {
            {"sampled_image", VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT},
            {"midpoint_chroma_samples", VK_FORMAT_FEATURE_MIDPOINT_CHROMA_SAMPLES_BIT},
            {"cosited_chroma_samples", VK_FORMAT_FEATURE_COSITED_CHROMA_SAMPLES_BIT},
            {"ycbcr_linear_filter", VK_FORMAT_FEATURE_SAMPLED_IMAGE_YCBCR_CONVERSION_LINEAR_FILTER_BIT},
            {"ycbcr_separate_reconstruction_filter", VK_FORMAT_FEATURE_SAMPLED_IMAGE_YCBCR_CONVERSION_SEPARATE_RECONSTRUCTION_FILTER_BIT},
            {"ycbcr_explicit_reconstruction", VK_FORMAT_FEATURE_SAMPLED_IMAGE_YCBCR_CONVERSION_CHROMA_RECONSTRUCTION_EXPLICIT_BIT},
            {"ycbcr_explicit_forceable", VK_FORMAT_FEATURE_SAMPLED_IMAGE_YCBCR_CONVERSION_CHROMA_RECONSTRUCTION_EXPLICIT_FORCEABLE_BIT},
            {"disjoint", VK_FORMAT_FEATURE_DISJOINT_BIT},
        };
        for (const auto& bit : bits) out += std::string(",\"") + bit.key + "\":" + json_bool((features & bit.bit) != 0);
        // Twelve rows, in the order of "columns": MUTABLE_FORMAT off and on, no format list, the UINT
        // plane views, the UNORM plane views, usage SAMPLED and SAMPLED | TRANSFER_SRC.
        out += ",\"image_format\":[";
        const ViewFormats view_sets[3] = {ViewFormats::none, ViewFormats::uint_views, ViewFormats::unorm_views};
        const char* const view_names[3] = {"none", "uint", "unorm"};
        bool first = true;
        for (int mutable_format = 0; mutable_format < 2; ++mutable_format) {
            for (int views = 0; views < 3; ++views) {
                for (int transfer_src = 0; transfer_src < 2; ++transfer_src) {
                    const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | (transfer_src ? VK_IMAGE_USAGE_TRANSFER_SRC_BIT : 0);
                    out += std::string(first ? "" : ",") + "[" + std::to_string(mutable_format) + ",\"" + view_names[views] + "\"," +
                           std::to_string(transfer_src) + ",";
                    first = false;
                    if (view_sets[views] != ViewFormats::none && !format_list_available_) {
                        out += "null,0,0,0,0]";  // no format list on this device: not asked
                        continue;
                    }
                    const ImageQueryResult answer = query_image(formats, modifier_tiling, modifier, mutable_format != 0, view_sets[views], usage);
                    out += std::to_string(static_cast<int>(answer.result)) + "," + std::to_string(answer.max_extent.width) + "," +
                           std::to_string(answer.max_extent.height) + "," + std::to_string(answer.external_features) + "," +
                           std::to_string(answer.compatible_handle_types) + "]";
                }
            }
        }
        return out + "]}";
    }

    std::string query_format(SourceFormat format, std::size_t other_modifiers) const {
        const PlaneFormat formats = plane_format(format);
        VkFormatProperties plain{};
        std::vector<VkDrmFormatModifierPropertiesEXT> known;
        format_properties(formats.image_format, plain, known);
        std::string out = std::string("{\"format\":\"") + source_format_name(format) + "\",\"vk_format\":" +
                          std::to_string(static_cast<int>(formats.image_format)) + ",\"modifiers_listed\":" +
                          std::to_string(known.size()) + ",\"tilings\":[";
        // The UBWC modifier always; then the driver's other modifiers in its order, other_modifiers at most.
        std::size_t reported = 0, omitted = 0;
        if (modifier_extension_) {
            out += query_tiling(formats, true, kDrmModifierQcomCompressed, modifier_facts(known, kDrmModifierQcomCompressed)) + ",";
            for (const VkDrmFormatModifierPropertiesEXT& entry : known) {
                if (entry.drmFormatModifier == kDrmModifierQcomCompressed) continue;
                if (reported == other_modifiers) { ++omitted; continue; }
                ++reported;
                out += query_tiling(formats, true, entry.drmFormatModifier, modifier_facts(known, entry.drmFormatModifier)) + ",";
            }
        }
        TilingFacts optimal;
        optimal.listed = plain.optimalTilingFeatures != 0;
        optimal.plane_count = 2;
        optimal.features = plain.optimalTilingFeatures;
        out += query_tiling(formats, false, 0, optimal);
        return out + "],\"modifiers_omitted\":" + std::to_string(omitted) + "}";
    }

    // Creates, and destroys at once, an image with the layout the Frame's camera
    // service delivered (E97: NV12, UBWC, 2464x2464, both strides 2560, the
    // chroma plane at 6348800), for each strategy the driver's query accepts,
    // and reports the memory the driver would want. No memory is bound.
    std::string query_trial_image() const {
        if (!modifier_extension_) return "null";
        const PlaneFormat formats = plane_format(SourceFormat::nv12);
        const uint32_t side = 2464;
        PlaneMeta planes[2];
        planes[0].offset = 0;
        planes[0].stride = 2560;
        planes[0].size = 6348800;
        planes[1].offset = 6348800;
        planes[1].stride = 2560;
        planes[1].size = 3215360;
        std::string out = "{\"basis\":\"e097\",\"format\":\"nv12\",\"modifier\":\"" + hex_u64(kDrmModifierQcomCompressed, 16) +
                          "\",\"width\":2464,\"height\":2464,\"planes\":\"0/2560/6348800,6348800/2560/3215360\",\"buffer_bytes\":9564160";
        for (ImportStrategy strategy : kStrategies) {
            std::string answer;
            const ImageQueryResult query = query_strategy(strategy, formats, true, kDrmModifierQcomCompressed);
            if (query.result != VK_SUCCESS) {
                answer = "not_created:query:" + vk_result(query.result);
            } else {
                VkImage image = VK_NULL_HANDLE;
                const VkResult created = create_modifier_image(strategy, formats, kDrmModifierQcomCompressed, side, side, 2, planes, image);
                if (created != VK_SUCCESS) {
                    answer = "vkCreateImage:" + vk_result(created);
                } else {
                    VkMemoryRequirements requirements{};
                    image_requirements(image, requirements);
                    api_.vkDestroyImage(device_, image, nullptr);
                    answer = "created:size=" + std::to_string(requirements.size) + ":alignment=" + std::to_string(requirements.alignment) +
                             ":memory_type_bits=" + hex_u32(requirements.memoryTypeBits);
                }
            }
            out += std::string(",\"") + strategy_name(strategy) + "\":\"" + answer + "\"";
        }
        return out + "}";
    }

    // ---- teardown ------------------------------------------------------------------

    void destroy(FrameResources& resources) {
        if (device_ == VK_NULL_HANDLE) return;
        if (resources.luma_view) api_.vkDestroyImageView(device_, resources.luma_view, nullptr);
        if (resources.chroma_view) api_.vkDestroyImageView(device_, resources.chroma_view, nullptr);
        if (resources.image) api_.vkDestroyImage(device_, resources.image, nullptr);
        if (resources.image_memory) api_.vkFreeMemory(device_, resources.image_memory, nullptr);
        if (resources.source_buffer) api_.vkDestroyBuffer(device_, resources.source_buffer, nullptr);
        if (resources.source_memory) api_.vkFreeMemory(device_, resources.source_memory, nullptr);
        resources = FrameResources();
    }

    void destroy_output() {
        if (device_ == VK_NULL_HANDLE) return;
        if (output_mapped_) api_.vkUnmapMemory(device_, output_memory_);
        if (output_buffer_) api_.vkDestroyBuffer(device_, output_buffer_, nullptr);
        if (output_memory_) api_.vkFreeMemory(device_, output_memory_, nullptr);
        output_mapped_ = nullptr;
        output_buffer_ = VK_NULL_HANDLE;
        output_memory_ = VK_NULL_HANDLE;
        output_capacity_ = 0;
    }

    void close() {
        if (device_ != VK_NULL_HANDLE) {
            api_.vkDeviceWaitIdle(device_);
            destroy_output();
            if (fence_) api_.vkDestroyFence(device_, fence_, nullptr);
            if (command_pool_) api_.vkDestroyCommandPool(device_, command_pool_, nullptr);
            if (descriptor_pool_) api_.vkDestroyDescriptorPool(device_, descriptor_pool_, nullptr);
            if (sampler_) api_.vkDestroySampler(device_, sampler_, nullptr);
            for (auto& by_strategy : programs_) {
                for (Program& program : by_strategy) destroy_program(program);
            }
            api_.vkDestroyDevice(device_, nullptr);
            device_ = VK_NULL_HANDLE;
        }
        if (instance_ != VK_NULL_HANDLE && api_.vkDestroyInstance) {
            api_.vkDestroyInstance(instance_, nullptr);
            instance_ = VK_NULL_HANDLE;
        }
        if (api_.library) {
            ::dlclose(api_.library);
            api_.library = nullptr;
        }
    }

    const std::string only_strategy_;  // tests only: the one strategy that may be chosen; empty in the helper
    VulkanApi api_;
    VkInstance instance_ = VK_NULL_HANDLE;
    VkPhysicalDevice physical_ = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties_{};
    VkPhysicalDeviceMemoryProperties memory_properties_{};
    VkDevice device_ = VK_NULL_HANDLE;
    VkQueue queue_ = VK_NULL_HANDLE;
    uint32_t queue_family_ = 0;
    bool modifier_extension_ = false, foreign_queue_extension_ = false, format_list_extension_ = false;
    bool format_list_available_ = false, ycbcr_feature_ = false;
    Program programs_[kStrategyCount][2];
    Decision decision_;
    VkSampler sampler_ = VK_NULL_HANDLE;
    VkDescriptorPool descriptor_pool_ = VK_NULL_HANDLE;
    VkCommandPool command_pool_ = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer_ = VK_NULL_HANDLE;
    VkFence fence_ = VK_NULL_HANDLE;
    VkBuffer output_buffer_ = VK_NULL_HANDLE;
    VkDeviceMemory output_memory_ = VK_NULL_HANDLE;
    void* output_mapped_ = nullptr;
    uint64_t output_capacity_ = 0;
    bool output_coherent_ = true;
    unsigned frames_ = 0;
    std::string device_facts_ = "{\"path\":\"gpu_convert\"";
};

} // namespace

std::unique_ptr<PixelPath> make_gpu_convert_path(const char* only_strategy) {
    return std::unique_ptr<PixelPath>(new GpuConvertPath(only_strategy));
}

bool query_gpu_convert(std::string& json, std::string& error) {
    GpuConvertPath path(nullptr);
    std::string diagnostics;
    if (!path.open(error, diagnostics)) return false;
    json = path.query_json();
    return true;
}

} // namespace sightcircuit_camera
