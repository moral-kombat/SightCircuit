// Test-only Vulkan dispatch. No driver, GPU, camera or external-memory operations.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>

namespace {
bool mode(const char *value) {
    const char *configured = std::getenv("FRAMEJACKER_FAKE_VULKAN_MODE");
    return configured && std::strcmp(configured, value) == 0;
}
void trace(const char *value) {
    const char *path = std::getenv("FRAMEJACKER_FAKE_VULKAN_TRACE");
    if (!path) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) _exit(90);
    if (write(fd, value, std::strlen(value)) < 0) _exit(91);
    close(fd);
}
__attribute__((constructor)) void loaded() { trace("load\n"); }
__attribute__((destructor)) void unloaded() { trace("unload\n"); }
VKAPI_ATTR VkResult VKAPI_CALL instance_version(uint32_t *value) {
    *value = mode("old_loader") ? VK_API_VERSION_1_0 : VK_API_VERSION_1_3;
    return VK_SUCCESS;
}
VKAPI_ATTR VkResult VKAPI_CALL create_instance(const VkInstanceCreateInfo *info,
                                             const VkAllocationCallbacks *, VkInstance *instance) {
    trace("create\n");
    if (mode("hang")) { signal(SIGTERM, SIG_IGN); for (;;) pause(); }
    if (mode("crash")) { raise(SIGSEGV); _exit(92); }
    if (info->pApplicationInfo->apiVersion != VK_API_VERSION_1_1 ||
        info->enabledExtensionCount || info->enabledLayerCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (mode("create_failure")) return VK_ERROR_INITIALIZATION_FAILED;
    *instance = reinterpret_cast<VkInstance>(1);
    return VK_SUCCESS;
}
VKAPI_ATTR VkResult VKAPI_CALL devices(VkInstance, uint32_t *count, VkPhysicalDevice *values) {
    if (mode("many_devices")) { *count = 33; return VK_SUCCESS; }
    if (mode("no_devices")) { *count = 0; return VK_SUCCESS; }
    if (values) values[0] = reinterpret_cast<VkPhysicalDevice>(2);
    *count = 1;
    return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL properties(VkPhysicalDevice, VkPhysicalDeviceProperties *value) {
    *value = {};
    value->apiVersion = VK_API_VERSION_1_3;
    value->vendorID = 0x5143; value->deviceID = 123;
    value->deviceType = VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
    std::strcpy(value->deviceName, "Fake \"GPU\"\n");
}
VKAPI_ATTR VkResult VKAPI_CALL extensions(VkPhysicalDevice, const char *, uint32_t *count,
                                         VkExtensionProperties *values) {
    const char *names[] = {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
                          "VK_EXT_image_drm_format_modifier", "VK_EXT_queue_family_foreign"};
    uint32_t size = mode("missing_extension") ? 3 : 4;
    if (mode("many_extensions")) { *count = 4097; return VK_SUCCESS; }
    if (values) {
        for (uint32_t i = 0; i < size; ++i) std::strcpy(values[i].extensionName, names[i]);
    }
    *count = size;
    return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL features(VkPhysicalDevice, VkPhysicalDeviceFeatures2 *value) {
    auto *ycbcr = static_cast<VkPhysicalDeviceSamplerYcbcrConversionFeatures *>(value->pNext);
    if (ycbcr->sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES) _exit(93);
    ycbcr->samplerYcbcrConversion = mode("no_ycbcr") ? VK_FALSE : VK_TRUE;
}
VKAPI_ATTR void VKAPI_CALL queues(VkPhysicalDevice, uint32_t *count, VkQueueFamilyProperties *values) {
    if (values) {
        values[0] = {};
        values[0].queueCount = 1;
        values[0].queueFlags = mode("compute_only") ? VK_QUEUE_COMPUTE_BIT : VK_QUEUE_GRAPHICS_BIT;
    }
    *count = 1;
}
VKAPI_ATTR void VKAPI_CALL memory(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties *value) {
    *value = {};
    value->memoryHeapCount = 1; value->memoryTypeCount = 1;
    value->memoryHeaps[0].size = 1024 * 1024;
    value->memoryTypes[0].propertyFlags = mode("no_host_cache") ? VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT :
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
}
VKAPI_ATTR void VKAPI_CALL formats(VkPhysicalDevice, VkFormat format, VkFormatProperties2 *value) {
    if (format == VK_FORMAT_R8_UNORM) {
        value->formatProperties.optimalTilingFeatures = mode("no_blit") ? 0 :
            VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_BLIT_DST_BIT |
            VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT |
            VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
        return;
    }
    if (format != VK_FORMAT_G8_B8R8_2PLANE_420_UNORM) _exit(94);
    auto *list = static_cast<VkDrmFormatModifierPropertiesListEXT *>(value->pNext);
    if (mode("many_modifiers")) { list->drmFormatModifierCount = 4097; return; }
    if (mode("no_modifier")) { list->drmFormatModifierCount = 0; return; }
    if (list->pDrmFormatModifierProperties) {
        list->pDrmFormatModifierProperties[0] = {0x0500000000000001ULL,
            mode("many_planes") ? 5u : 4u, VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT};
    }
    list->drmFormatModifierCount = 1;
}
VKAPI_ATTR VkResult VKAPI_CALL images(VkPhysicalDevice, const VkPhysicalDeviceImageFormatInfo2 *info,
                                     VkImageFormatProperties2 *value) {
    if (info->format == VK_FORMAT_R8_UNORM) {
        if (info->pNext || info->tiling != VK_IMAGE_TILING_OPTIMAL || info->type != VK_IMAGE_TYPE_2D ||
            info->usage != (VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT)) _exit(96);
        value->imageFormatProperties.maxExtent = {mode("small_r8") ? 1024u : 4096u, 4096, 1};
        value->imageFormatProperties.maxMipLevels = 1;
        value->imageFormatProperties.maxArrayLayers = 1;
        value->imageFormatProperties.sampleCounts = VK_SAMPLE_COUNT_1_BIT;
        return VK_SUCCESS;
    }
    trace("external_format\n");
    const auto *drm = static_cast<const VkPhysicalDeviceImageDrmFormatModifierInfoEXT *>(info->pNext);
    const auto *external = static_cast<const VkPhysicalDeviceExternalImageFormatInfo *>(drm->pNext);
    if (info->format != VK_FORMAT_G8_B8R8_2PLANE_420_UNORM || info->type != VK_IMAGE_TYPE_2D ||
        info->tiling != VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ||
        info->usage != (VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT) || info->flags ||
        drm->sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT ||
        drm->drmFormatModifier != 0x0500000000000001ULL || drm->sharingMode != VK_SHARING_MODE_EXCLUSIVE ||
        external->sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO ||
        external->handleType != VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT) _exit(95);
    if (mode("unsupported_image")) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    if (mode("image_error")) return VK_ERROR_DEVICE_LOST;
    value->imageFormatProperties.maxExtent = {mode("small_extent") ? 1024u : 4096u, 4096, 1};
    value->imageFormatProperties.maxMipLevels = 1;
    value->imageFormatProperties.maxArrayLayers = 2;
    value->imageFormatProperties.sampleCounts = VK_SAMPLE_COUNT_1_BIT;
    auto *output = static_cast<VkExternalImageFormatProperties *>(value->pNext);
    output->externalMemoryProperties.externalMemoryFeatures = VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT |
        (mode("not_importable") ? 0 : VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT);
    output->externalMemoryProperties.compatibleHandleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    return VK_SUCCESS;
}
} // namespace

extern "C" VKAPI_ATTR void VKAPI_CALL vkDestroyInstance(VkInstance, const VkAllocationCallbacks *) {
    trace("destroy\n");
    if (mode("hang_destroy")) { signal(SIGTERM, SIG_IGN); for (;;) pause(); }
}
extern "C" VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *name) {
    if (std::strcmp(name, "vkCreateInstance") == 0) return reinterpret_cast<PFN_vkVoidFunction>(create_instance);
    if (std::strcmp(name, "vkEnumerateInstanceVersion") == 0) return reinterpret_cast<PFN_vkVoidFunction>(instance_version);
    if (!instance) return nullptr; // Instance commands are not available from a null instance.
#define ENTRY(api, implementation) if (std::strcmp(name, api) == 0) return reinterpret_cast<PFN_vkVoidFunction>(implementation)
    ENTRY("vkEnumeratePhysicalDevices", devices);
    ENTRY("vkGetPhysicalDeviceProperties", properties);
    ENTRY("vkEnumerateDeviceExtensionProperties", extensions);
    ENTRY("vkGetPhysicalDeviceFeatures2", features);
    ENTRY("vkGetPhysicalDeviceQueueFamilyProperties", queues);
    ENTRY("vkGetPhysicalDeviceMemoryProperties", memory);
    ENTRY("vkGetPhysicalDeviceFormatProperties2", formats);
    ENTRY("vkGetPhysicalDeviceImageFormatProperties2", images);
#undef ENTRY
    return nullptr;
}
