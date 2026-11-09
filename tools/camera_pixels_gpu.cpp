// The gpu_convert pixel path: a camera DMA-BUF is imported into Vulkan as a
// multi-planar image with the frame's own DRM format modifier and plane layout
// (VK_EXT_image_drm_format_modifier, VK_EXT_external_memory_dma_buf), read
// through per-plane unsigned-integer views by the compute shader in
// camera_convert.comp (the fixed-point formula of camera_ycbcr.hpp, so the GPU
// delivers the same bytes as the CPU path), and the BGR3 rows are read back
// from a host-visible buffer. The import is a stage of its own (import_modifier
// and import_linear_copy below) so it can be swapped without touching the
// conversion.
//
// When the device lacks the modifier extension (the cloud runner's lavapipe)
// and the buffer is linear, the import falls back to importing the DMA-BUF as a
// Vulkan buffer and copying its planes into an optimally tiled image: the
// layout then comes from the frame's plane table, never from the driver. Tiled
// or compressed buffers (UBWC) need the modifier path; nothing here can read
// them otherwise.
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

std::string vk_error(const char* call, VkResult result) {
    return std::string(call) + ":VkResult=" + std::to_string(static_cast<int>(result));
}

struct PushConstants {
    uint32_t width, height, stride_words, sample_shift, sample_max;
    int32_t cr_r, cb_g, cr_g, cb_b;
};
static_assert(sizeof(PushConstants) == 36, "push constant layout drifted from camera_convert.comp");

struct PlaneFormat {
    VkFormat image_format;
    VkFormat luma_view;    // the UINT view of plane 0
    VkFormat chroma_view;  // the UINT view of plane 1
    uint32_t luma_texel_bytes;
    uint32_t chroma_texel_bytes;
};
PlaneFormat plane_format(SourceFormat format) {
    if (format == SourceFormat::p010) {
        return {VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16, VK_FORMAT_R16_UINT, VK_FORMAT_R16G16_UINT, 2, 4};
    }
    return {VK_FORMAT_G8_B8R8_2PLANE_420_UNORM, VK_FORMAT_R8_UINT, VK_FORMAT_R8G8_UINT, 1, 2};
}

// Everything created for one frame, destroyed after its readback.
struct FrameResources {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE;
    VkBuffer source_buffer = VK_NULL_HANDLE;  // linear-copy fallback only
    VkDeviceMemory source_memory = VK_NULL_HANDLE;
    VkImageView luma_view = VK_NULL_HANDLE;
    VkImageView chroma_view = VK_NULL_HANDLE;
    bool copy_from_buffer = false;
};

class GpuConvertPath : public PixelPath {
public:
    ~GpuConvertPath() override { close(); }

    const char* name() const override { return sightcircuit_camera_export::kPixelPathGpuConvert; }
    const char* derivation() const override { return sightcircuit_camera_export::kDerivationGpuConverted; }

    bool open(std::string& error, std::string& diagnostics_json) override {
        if (!load_loader(error)) return false;
        if (!create_instance(error)) return false;
        if (!pick_device(error)) return false;
        if (!create_device(error)) return false;
        if (!create_pipeline(error)) return false;
        diagnostics_json = diagnostics_;
        return true;
    }

    bool convert(const SourceFrame& frame, BgrImage& out, std::string& error) override {
        if (device_ == VK_NULL_HANDLE) { error = "gpu_path_not_open"; return false; }
        const uint64_t payload = static_cast<uint64_t>(bgr_stride_bytes(frame.width)) * frame.height;
        if (payload > sightcircuit_camera_export::kMaxPayloadBytes) { error = "payload_exceeds_protocol_bound"; return false; }
        if (frame.plane_count < 2) { error = "plane_count_below_two"; return false; }
        FrameResources resources;
        bool ok = import_frame(frame, resources, error) && ensure_output(payload, error) &&
                  dispatch_and_read(frame, resources, out, payload, error);
        destroy(resources);
        return ok;
    }

    std::string notes_json() const override {
        return std::string("{\"import\":\"") + (modifier_extension_ ? "drm_format_modifier" : "linear_copy_fallback") +
               "\",\"frames_imported\":" + std::to_string(frames_) + "}";
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
        VkPhysicalDeviceSamplerYcbcrConversionFeatures ycbcr{};
        ycbcr.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES;
        VkPhysicalDeviceFeatures2 features{};
        features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
        features.pNext = &ycbcr;
        api_.vkGetPhysicalDeviceFeatures2(physical_, &features);
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
        diagnostics_ = std::string("{\"path\":\"gpu_convert\",\"device_name\":\"") + escape(properties_.deviceName) +
                       "\",\"device_type\":" + std::to_string(static_cast<int>(properties_.deviceType)) +
                       ",\"api_version\":\"" + std::to_string(VK_VERSION_MAJOR(properties_.apiVersion)) + "." +
                       std::to_string(VK_VERSION_MINOR(properties_.apiVersion)) + "." +
                       std::to_string(VK_VERSION_PATCH(properties_.apiVersion)) + "\",\"driver_version\":" +
                       std::to_string(properties_.driverVersion) + ",\"queue_family\":" + std::to_string(queue_family_) +
                       ",\"import\":\"" + (modifier_extension_ ? "drm_format_modifier" : "linear_copy_fallback") +
                       "\",\"sampler_ycbcr_conversion_feature\":" + (ycbcr.samplerYcbcrConversion ? "true" : "false") +
                       ",\"device_extensions\":" + extension_list + ",\"shader_source_sha256\":\"" +
                       kCameraConvertSourceSha256 + "\",\"chroma_siting\":\"nearest\",\"fixed_point_bits\":16}";
        return true;
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

    bool create_pipeline(std::string& error) {
        VkDescriptorSetLayoutBinding bindings[3]{};
        for (uint32_t i = 0; i < 3; ++i) {
            bindings[i].binding = i;
            bindings[i].descriptorCount = 1;
            bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
            bindings[i].descriptorType = i < 2 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        }
        VkDescriptorSetLayoutCreateInfo layout_info{};
        layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
        layout_info.bindingCount = 3;
        layout_info.pBindings = bindings;
        VkResult result = api_.vkCreateDescriptorSetLayout(device_, &layout_info, nullptr, &set_layout_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateDescriptorSetLayout", result); return false; }
        VkPushConstantRange range{};
        range.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        range.size = sizeof(PushConstants);
        VkPipelineLayoutCreateInfo pipeline_layout_info{};
        pipeline_layout_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
        pipeline_layout_info.setLayoutCount = 1;
        pipeline_layout_info.pSetLayouts = &set_layout_;
        pipeline_layout_info.pushConstantRangeCount = 1;
        pipeline_layout_info.pPushConstantRanges = &range;
        result = api_.vkCreatePipelineLayout(device_, &pipeline_layout_info, nullptr, &pipeline_layout_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreatePipelineLayout", result); return false; }
        VkShaderModuleCreateInfo shader_info{};
        shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
        shader_info.codeSize = kCameraConvertSpirvWords * sizeof(uint32_t);
        shader_info.pCode = kCameraConvertSpirv;
        VkShaderModule shader = VK_NULL_HANDLE;
        result = api_.vkCreateShaderModule(device_, &shader_info, nullptr, &shader);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateShaderModule", result); return false; }
        VkComputePipelineCreateInfo pipeline_info{};
        pipeline_info.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
        pipeline_info.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        pipeline_info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        pipeline_info.stage.module = shader;
        pipeline_info.stage.pName = "main";
        pipeline_info.layout = pipeline_layout_;
        result = api_.vkCreateComputePipelines(device_, VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &pipeline_);
        api_.vkDestroyShaderModule(device_, shader, nullptr);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateComputePipelines", result); return false; }
        VkSamplerCreateInfo sampler_info{};
        sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
        sampler_info.magFilter = VK_FILTER_NEAREST;
        sampler_info.minFilter = VK_FILTER_NEAREST;
        sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
        sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        sampler_info.unnormalizedCoordinates = VK_TRUE;
        result = api_.vkCreateSampler(device_, &sampler_info, nullptr, &sampler_);
        if (result != VK_SUCCESS) { error = vk_error("vkCreateSampler", result); return false; }
        VkDescriptorPoolSize sizes[2]{};
        sizes[0].type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        sizes[0].descriptorCount = 2;
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
        if (!modifier_is_linear(frame.drm_modifier)) {
            error = "modifier_import_unsupported_by_device:" + hex_u64(frame.drm_modifier, 16);
            return false;
        }
        return import_linear_copy(frame, resources, error);
    }

    VkImageCreateInfo base_image_info(const SourceFrame& frame, const PlaneFormat& formats) const {
        VkImageCreateInfo info{};
        info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
        info.flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
        info.imageType = VK_IMAGE_TYPE_2D;
        info.format = formats.image_format;
        info.extent = {frame.width, frame.height, 1};
        info.mipLevels = 1;
        info.arrayLayers = 1;
        info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
        info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        return info;
    }

    // The device path: the DMA-BUF becomes the image's memory, with the frame's
    // modifier and plane table describing the layout. The queue-family
    // ownership acquire from the foreign producer carries the contents in.
    bool import_modifier(const SourceFrame& frame, FrameResources& resources, std::string& error) {
        const PlaneFormat formats = plane_format(frame.format);
        // The driver must know this modifier for this format with the frame's plane count.
        VkDrmFormatModifierPropertiesListEXT modifiers{};
        modifiers.sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT;
        VkFormatProperties2 format_properties{};
        format_properties.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2;
        format_properties.pNext = &modifiers;
        api_.vkGetPhysicalDeviceFormatProperties2(physical_, formats.image_format, &format_properties);
        std::vector<VkDrmFormatModifierPropertiesEXT> known(modifiers.drmFormatModifierCount);
        modifiers.pDrmFormatModifierProperties = known.data();
        api_.vkGetPhysicalDeviceFormatProperties2(physical_, formats.image_format, &format_properties);
        bool listed = false;
        for (const VkDrmFormatModifierPropertiesEXT& entry : known) {
            if (entry.drmFormatModifier != frame.drm_modifier) continue;
            listed = true;
            if (entry.drmFormatModifierPlaneCount != frame.plane_count) {
                error = "modifier_plane_count_disagrees_with_driver:" + std::to_string(frame.plane_count) + "!=" +
                        std::to_string(entry.drmFormatModifierPlaneCount);
                return false;
            }
            if (!(entry.drmFormatModifierTilingFeatures & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT)) {
                error = "modifier_not_sampleable:" + hex_u64(frame.drm_modifier, 16);
                return false;
            }
        }
        if (!listed) { error = "modifier_unknown_to_driver:" + hex_u64(frame.drm_modifier, 16); return false; }

        const VkFormat view_formats[2] = {formats.luma_view, formats.chroma_view};
        VkImageFormatListCreateInfo format_list{};
        format_list.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO;
        format_list.viewFormatCount = 2;
        format_list.pViewFormats = view_formats;
        VkPhysicalDeviceImageDrmFormatModifierInfoEXT modifier_query{};
        modifier_query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT;
        modifier_query.pNext = &format_list;
        modifier_query.drmFormatModifier = frame.drm_modifier;
        modifier_query.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VkPhysicalDeviceExternalImageFormatInfo external_query{};
        external_query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO;
        external_query.pNext = &modifier_query;
        external_query.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkPhysicalDeviceImageFormatInfo2 query{};
        query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2;
        query.pNext = &external_query;
        query.format = formats.image_format;
        query.type = VK_IMAGE_TYPE_2D;
        query.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
        query.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
        query.flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
        VkExternalImageFormatProperties external_properties{};
        external_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES;
        VkImageFormatProperties2 image_properties{};
        image_properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
        image_properties.pNext = &external_properties;
        VkResult result = api_.vkGetPhysicalDeviceImageFormatProperties2(physical_, &query, &image_properties);
        if (result != VK_SUCCESS) { error = vk_error("vkGetPhysicalDeviceImageFormatProperties2(modifier)", result); return false; }
        if (!(external_properties.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)) {
            error = "dma_buf_not_importable_for_modifier";
            return false;
        }
        if (frame.width > image_properties.imageFormatProperties.maxExtent.width ||
            frame.height > image_properties.imageFormatProperties.maxExtent.height) {
            error = "frame_exceeds_device_image_extent";
            return false;
        }

        VkSubresourceLayout layouts[kMaxPlanes]{};
        for (uint32_t plane = 0; plane < frame.plane_count; ++plane) {
            layouts[plane].offset = frame.planes[plane].offset;
            layouts[plane].size = frame.planes[plane].size;
            layouts[plane].rowPitch = frame.planes[plane].stride;
        }
        VkImageDrmFormatModifierExplicitCreateInfoEXT explicit_layout{};
        explicit_layout.sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT;
        explicit_layout.pNext = &format_list;
        explicit_layout.drmFormatModifier = frame.drm_modifier;
        explicit_layout.drmFormatModifierPlaneCount = frame.plane_count;
        explicit_layout.pPlaneLayouts = layouts;
        VkExternalMemoryImageCreateInfo external{};
        external.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
        external.pNext = &explicit_layout;
        external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkImageCreateInfo info = base_image_info(frame, formats);
        info.pNext = &external;
        info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
        result = api_.vkCreateImage(device_, &info, nullptr, &resources.image);
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

        const VkFormat view_formats[2] = {formats.luma_view, formats.chroma_view};
        VkImageFormatListCreateInfo format_list{};
        format_list.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO;
        format_list.viewFormatCount = 2;
        format_list.pViewFormats = view_formats;
        VkImageCreateInfo info = base_image_info(frame, formats);
        info.pNext = &format_list;
        info.tiling = VK_IMAGE_TILING_OPTIMAL;
        info.usage |= VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        result = api_.vkCreateImage(device_, &info, nullptr, &resources.image);
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

    bool create_views(const SourceFrame& frame, const PlaneFormat& formats, FrameResources& resources, std::string& error) {
        (void)frame;
        const struct { VkImageAspectFlags aspect; VkFormat format; VkImageView* view; } views[2] = {
            {VK_IMAGE_ASPECT_PLANE_0_BIT, formats.luma_view, &resources.luma_view},
            {VK_IMAGE_ASPECT_PLANE_1_BIT, formats.chroma_view, &resources.chroma_view},
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

    bool dispatch_and_read(const SourceFrame& frame, const FrameResources& resources, BgrImage& out, uint64_t payload,
                           std::string& error) {
        const PlaneFormat formats = plane_format(frame.format);
        VkResult result = api_.vkResetDescriptorPool(device_, descriptor_pool_, 0);
        if (result != VK_SUCCESS) { error = vk_error("vkResetDescriptorPool", result); return false; }
        VkDescriptorSetAllocateInfo set_info{};
        set_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
        set_info.descriptorPool = descriptor_pool_;
        set_info.descriptorSetCount = 1;
        set_info.pSetLayouts = &set_layout_;
        VkDescriptorSet set = VK_NULL_HANDLE;
        result = api_.vkAllocateDescriptorSets(device_, &set_info, &set);
        if (result != VK_SUCCESS) { error = vk_error("vkAllocateDescriptorSets", result); return false; }
        VkDescriptorImageInfo images[2]{};
        images[0].sampler = sampler_;
        images[0].imageView = resources.luma_view;
        images[0].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        images[1].sampler = sampler_;
        images[1].imageView = resources.chroma_view;
        images[1].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        VkDescriptorBufferInfo buffer{};
        buffer.buffer = output_buffer_;
        buffer.range = payload;
        VkWriteDescriptorSet writes[3]{};
        for (uint32_t i = 0; i < 3; ++i) {
            writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[i].dstSet = set;
            writes[i].dstBinding = i;
            writes[i].descriptorCount = 1;
            writes[i].descriptorType = i < 2 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            if (i < 2) writes[i].pImageInfo = &images[i];
            else writes[i].pBufferInfo = &buffer;
        }
        api_.vkUpdateDescriptorSets(device_, 3, writes, 0, nullptr);

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
        api_.vkCmdBindPipeline(command_buffer_, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_);
        api_.vkCmdBindDescriptorSets(command_buffer_, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout_, 0, 1, &set, 0, nullptr);
        api_.vkCmdPushConstants(command_buffer_, pipeline_layout_, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof constants, &constants);
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
            if (pipeline_) api_.vkDestroyPipeline(device_, pipeline_, nullptr);
            if (pipeline_layout_) api_.vkDestroyPipelineLayout(device_, pipeline_layout_, nullptr);
            if (set_layout_) api_.vkDestroyDescriptorSetLayout(device_, set_layout_, nullptr);
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

    VulkanApi api_;
    VkInstance instance_ = VK_NULL_HANDLE;
    VkPhysicalDevice physical_ = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties_{};
    VkPhysicalDeviceMemoryProperties memory_properties_{};
    VkDevice device_ = VK_NULL_HANDLE;
    VkQueue queue_ = VK_NULL_HANDLE;
    uint32_t queue_family_ = 0;
    bool modifier_extension_ = false, foreign_queue_extension_ = false, format_list_extension_ = false;
    VkDescriptorSetLayout set_layout_ = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout_ = VK_NULL_HANDLE;
    VkPipeline pipeline_ = VK_NULL_HANDLE;
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
    std::string diagnostics_ = "{}";
};

} // namespace

std::unique_ptr<PixelPath> make_gpu_convert_path() {
    return std::unique_ptr<PixelPath>(new GpuConvertPath());
}

} // namespace sightcircuit_camera
