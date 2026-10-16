// Original Framejacker import validation/ownership. No loader or device creation.
#pragma once
#include "capture_transport.hpp"
#include <vulkan/vulkan.h>
#include <limits>

namespace framejacker::capture_import {
using transport::Error;
constexpr uint64_t camera_modifier = 0x0500000000000001ULL;
constexpr VkFormat camera_format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM;
constexpr VkImageUsageFlags import_usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
constexpr uint64_t maximum_allocation = 1ULL << 30;

// Validate the one reviewed profile; other sources need their own contract.
inline void validate_metadata(const MrhandBufInfo &info) {
    if (info.magic != MRHAND_MAGIC || !info.generation || !info.id || info.hasImage != 1)
        throw Error("import_invalid_record");
    if (!info.size || info.size > maximum_allocation) throw Error("import_allocation_size_bounds");
    if (info.bindOffset) throw Error("import_nonzero_binding_offset");
    if (info.handleType != VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT)
        throw Error("import_unsupported_handle_type");
    if (info.format != camera_format || info.width != 2464 || info.height != 2464 ||
        info.arrayLayers != 1 || // The wire record has no per-plane arrayPitch.
        info.tiling != VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ||
        (info.usage & import_usage) != import_usage || info.hasModifier != 1 ||
        info.modifier != camera_modifier) throw Error("import_unsupported_image_profile");
    if (!info.planeCount || info.planeCount > MRHAND_MAX_PLANES)
        throw Error("import_plane_count_bounds");
    for (uint32_t i = 0; i < info.planeCount; ++i) {
        // Compressed modifier memory planes are not linear NV12 color planes.
        // Do not invent height/stride rules for their auxiliary compression data.
        if (info.planeOffset[i] >= info.size || !info.planeRowPitch[i] ||
            info.planeRowPitch[i] > info.size - info.planeOffset[i])
            throw Error("import_plane_layout_bounds");
    }
    for (uint32_t i = info.planeCount; i < MRHAND_MAX_PLANES; ++i)
        if (info.planeOffset[i] || info.planeRowPitch[i]) throw Error("import_unused_plane_metadata");
}

struct Api {
    PFN_vkGetPhysicalDeviceFormatProperties2 format_properties = nullptr;
    PFN_vkGetPhysicalDeviceImageFormatProperties2 image_properties = nullptr;
    PFN_vkGetMemoryFdPropertiesKHR fd_properties = nullptr;
    PFN_vkCreateImage create_image = nullptr;
    PFN_vkDestroyImage destroy_image = nullptr;
    PFN_vkGetImageMemoryRequirements2 image_requirements = nullptr;
    PFN_vkAllocateMemory allocate_memory = nullptr;
    PFN_vkFreeMemory free_memory = nullptr;
    PFN_vkBindImageMemory bind_image = nullptr;
    bool complete() const {
        return format_properties && image_properties && fd_properties && create_image &&
            destroy_image && image_requirements && allocate_memory && free_memory && bind_image;
    }
};

// One serial worker/queue. This only tracks our submissions, never XRService's.
struct Completion {
    bool known_idle = true;
    bool cleanup_deferred = false;
    void submitted() { known_idle = false; }
    void completed() { known_idle = true; }
};
struct Context {
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkPhysicalDeviceMemoryProperties memory{};
    Api api;
    Completion *completion = nullptr;
};

inline void check(VkResult result, const char *reason) {
    if (result != VK_SUCCESS)
        throw std::runtime_error(std::string(reason) + ": VkResult " + std::to_string(result));
}

struct SubmissionApi {
    PFN_vkResetFences reset = nullptr;
    PFN_vkQueueSubmit submit = nullptr;
    PFN_vkWaitForFences wait = nullptr;
};
inline void submit_and_wait(Completion &completion, const SubmissionApi &api,
                           VkDevice device, VkQueue queue, VkFence fence, const VkSubmitInfo &submission) {
    if (!device || !queue || !fence || !api.reset || !api.submit || !api.wait)
        throw Error("import_submission_context_incomplete");
    if (!completion.known_idle) throw Error("import_work_in_flight");
    check(api.reset(device, 1, &fence), "import_fence_reset_failed");
    completion.submitted();
    check(api.submit(queue, 1, &submission, fence), "import_queue_submit_failed");
    check(api.wait(device, 1, &fence, VK_TRUE, 2000000000ULL), "import_completion_wait_failed");
    completion.completed();
}

// The device and completion gate must outlive this move-only owner. On uncertain
// submitted work, abandon API destruction to process teardown; never free an
// image/memory that the GPU might still use, or perform an unbounded idle wait.
class Image {
    VkDevice device_ = VK_NULL_HANDLE;
    Api api_{};
    Completion *completion_ = nullptr;
    VkImage image_ = VK_NULL_HANDLE;
    VkDeviceMemory memory_ = VK_NULL_HANDLE;
public:
    Image() = default;
    explicit Image(const Context &context)
        : device_(context.device), api_(context.api), completion_(context.completion) {}
    ~Image() { close(); }
    Image(const Image &) = delete;
    Image &operator=(const Image &) = delete;
    Image(Image &&other) noexcept { *this = std::move(other); }
    Image &operator=(Image &&other) noexcept {
        if (this != &other) {
            close();
            device_ = other.device_; api_ = other.api_; completion_ = other.completion_;
            image_ = std::exchange(other.image_, VK_NULL_HANDLE);
            memory_ = std::exchange(other.memory_, VK_NULL_HANDLE);
        }
        return *this;
    }
    bool close() noexcept {
        if (!image_ && !memory_) return true;
        bool safe = completion_->known_idle;
        if (safe) {
            if (image_) api_.destroy_image(device_, image_, nullptr);
            if (memory_) api_.free_memory(device_, memory_, nullptr);
        } else completion_->cleanup_deferred = true;
        image_ = VK_NULL_HANDLE; memory_ = VK_NULL_HANDLE;
        return safe;
    }
    VkImage image() const { return image_; }
    VkDeviceMemory memory() const { return memory_; }
    friend Image import_image(const Context &, const MrhandBufInfo &, int);
};

inline void validate_capabilities(const Context &context, const MrhandBufInfo &info) {
    VkDrmFormatModifierPropertiesListEXT list{VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT};
    VkFormatProperties2 properties{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, &list};
    context.api.format_properties(context.physical, camera_format, &properties);
    if (list.drmFormatModifierCount > 4096) throw Error("import_modifier_count_bounds");
    std::vector<VkDrmFormatModifierPropertiesEXT> modifiers(list.drmFormatModifierCount);
    if (!modifiers.empty()) {
        list.pDrmFormatModifierProperties = modifiers.data();
        context.api.format_properties(context.physical, camera_format, &properties);
        if (list.drmFormatModifierCount > modifiers.size()) throw Error("import_modifier_count_changed");
    }
    bool found = false;
    for (uint32_t i = 0; i < list.drmFormatModifierCount; ++i)
        if (modifiers[i].drmFormatModifier == info.modifier &&
            modifiers[i].drmFormatModifierPlaneCount == info.planeCount) found = true;
    if (!found) throw Error("import_modifier_or_plane_count_unsupported");

    VkPhysicalDeviceExternalImageFormatInfo external{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};
    external.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkPhysicalDeviceImageDrmFormatModifierInfoEXT drm{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT, &external};
    drm.drmFormatModifier = info.modifier; drm.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VkPhysicalDeviceImageFormatInfo2 input{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2, &drm};
    input.format = camera_format; input.type = VK_IMAGE_TYPE_2D;
    input.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT; input.usage = import_usage;
    VkExternalImageFormatProperties ext{VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
    VkImageFormatProperties2 output{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2, &ext};
    check(context.api.image_properties(context.physical, &input, &output), "import_image_format_query_failed");
    const auto &memory = ext.externalMemoryProperties;
    const auto &limits = output.imageFormatProperties;
    if (!(memory.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT) ||
        !(memory.compatibleHandleTypes & VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT))
        throw Error("import_dma_buf_not_importable");
    if (limits.maxExtent.width < info.width || limits.maxExtent.height < info.height ||
        limits.maxExtent.depth < 1 || limits.maxArrayLayers < info.arrayLayers ||
        limits.maxMipLevels < 1 || !(limits.sampleCounts & VK_SAMPLE_COUNT_1_BIT) ||
        limits.maxResourceSize < info.size) throw Error("import_image_limits");
}

inline Image import_image(const Context &context, const MrhandBufInfo &info, int source_fd) {
    validate_metadata(info);
    if (!context.physical || !context.device || !context.completion || !context.api.complete())
        throw Error("import_context_incomplete");
    if (!context.completion->known_idle) throw Error("import_work_in_flight");
    if (!context.memory.memoryTypeCount || context.memory.memoryTypeCount > VK_MAX_MEMORY_TYPES ||
        !context.memory.memoryHeapCount || context.memory.memoryHeapCount > VK_MAX_MEMORY_HEAPS)
        throw Error("import_memory_properties_bounds");
    for (uint32_t i = 0; i < context.memory.memoryTypeCount; ++i)
        if (context.memory.memoryTypes[i].heapIndex >= context.memory.memoryHeapCount)
            throw Error("import_memory_heap_bounds");
    auto descriptor = transport::duplicate(source_fd);
    validate_capabilities(context, info);
    VkMemoryFdPropertiesKHR fd_properties{VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};
    check(context.api.fd_properties(context.device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
                                   descriptor.get(), &fd_properties), "import_fd_query_failed");

    VkSubresourceLayout planes[MRHAND_MAX_PLANES]{};
    for (uint32_t i = 0; i < info.planeCount; ++i) {
        planes[i].offset = info.planeOffset[i]; planes[i].rowPitch = info.planeRowPitch[i];
    }
    VkImageDrmFormatModifierExplicitCreateInfoEXT drm{VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT};
    drm.drmFormatModifier = info.modifier; drm.drmFormatModifierPlaneCount = info.planeCount;
    drm.pPlaneLayouts = planes;
    VkExternalMemoryImageCreateInfo external{VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO, &drm};
    external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkImageCreateInfo create{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, &external};
    create.imageType = VK_IMAGE_TYPE_2D; create.format = camera_format;
    create.extent = {info.width, info.height, 1}; create.mipLevels = 1;
    create.arrayLayers = info.arrayLayers; create.samples = VK_SAMPLE_COUNT_1_BIT;
    create.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT; create.usage = import_usage;
    create.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    Image result(context);
    VkImage created = VK_NULL_HANDLE;
    check(context.api.create_image(context.device, &create, nullptr, &created), "import_create_image_failed");
    result.image_ = created;
    VkMemoryDedicatedRequirements dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS};
    VkMemoryRequirements2 requirements{VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2, &dedicated};
    VkImageMemoryRequirementsInfo2 req_info{VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2};
    req_info.image = result.image_;
    context.api.image_requirements(context.device, &req_info, &requirements);
    const auto &req = requirements.memoryRequirements;
    if (!req.size || req.size > info.size || !req.alignment || (req.alignment & (req.alignment - 1)))
        throw Error("import_memory_requirements_bounds");
    uint32_t valid_bits = context.memory.memoryTypeCount == 32 ? std::numeric_limits<uint32_t>::max() :
        (uint32_t{1} << context.memory.memoryTypeCount) - 1;
    uint32_t compatible = req.memoryTypeBits & fd_properties.memoryTypeBits & valid_bits;
    if (!compatible) throw Error("import_no_compatible_memory_type");
    uint32_t type = 0;
    while (!(compatible & (uint32_t{1} << type))) ++type;
    // VUID-VkMemoryDedicatedAllocateInfo-image-02964 requires the exact image
    // requirement size. The declared FD allocation may be larger, never smaller.
    VkMemoryDedicatedAllocateInfo dedicate{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO};
    dedicate.image = result.image_;
    VkImportMemoryFdInfoKHR import{VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR, &dedicate};
    import.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT; import.fd = descriptor.get();
    VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &import};
    allocation.allocationSize = req.size; allocation.memoryTypeIndex = type;
    VkDeviceMemory allocated = VK_NULL_HANDLE;
    VkResult status = context.api.allocate_memory(context.device, &allocation, nullptr, &allocated);
    if (status == VK_SUCCESS) {
        descriptor.release(); // Vulkan owns the duplicate only after successful allocation.
        result.memory_ = allocated;
    }
    check(status, "import_allocate_memory_failed");
    check(context.api.bind_image(context.device, result.image_, result.memory_, 0), "import_bind_image_failed");
    return result;
}
} // namespace framejacker::capture_import
