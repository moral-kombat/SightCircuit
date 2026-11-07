// Synthetic Vulkan function-pointer dispatch only. Never link/load Vulkan.
#define VK_NO_PROTOTYPES
#include "capture_import.hpp"
#include <dirent.h>
#include <functional>
#include <iostream>
#include <map>
#include <sys/mman.h>

namespace ci = sightcircuit::capture_import;
using sightcircuit::transport::Fd;
using ci::Error;

static void require(bool value) { if (!value) throw Error("fixture_assertion_failed"); }
static size_t descriptors_open() {
    DIR *directory = opendir("/proc/self/fd"); require(directory != nullptr);
    size_t count = 0;
    while (readdir(directory)) ++count;
    closedir(directory);
    return count;
}
static MrhandBufInfo metadata(uint32_t plane_count = 4) {
    require(plane_count >= 1 && plane_count <= MRHAND_MAX_PLANES);
    MrhandBufInfo info{};
    info.magic = MRHAND_MAGIC; info.id = 9; info.generation = 7;
    info.size = 1 << 24; info.hasImage = 1;
    info.format = ci::camera_format; info.width = info.height = 2464; info.arrayLayers = 1;
    info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT; info.usage = ci::import_usage;
    info.hasModifier = 1; info.modifier = ci::camera_modifier; info.planeCount = plane_count;
    for (uint32_t i = 0; i < info.planeCount; ++i) {
        info.planeOffset[i] = i * 4096; info.planeRowPitch[i] = 256;
    }
    return info;
}
static Fd payload() {
    Fd fd(memfd_create("sightcircuit-synthetic-import", MFD_CLOEXEC));
    require(fd.get() >= 0 && ftruncate(fd.get(), metadata().size) == 0);
    require(write(fd.get(), "synthetic", 9) == 9);
    return fd;
}
static void readable(int fd) {
    char text[9];
    require(fcntl(fd, F_GETFD) >= 0 && pread(fd, text, 9, 0) == 9 && !memcmp(text, "synthetic", 9));
}
template <typename F> static void rejects(const char *reason, F action) {
    try { action(); }
    catch (const std::exception &error) {
        require(std::string(error.what()).find(reason) == 0); return;
    }
    throw Error("fixture_expected_rejection");
}

struct Fake {
    static Fake *active;
    ci::Completion completion;
    ci::Context context;
    std::vector<std::string> calls;
    std::map<VkImage, bool> images;
    struct Memory { Fd fd; VkImage image; };
    std::map<VkDeviceMemory, Memory> memories;
    uintptr_t next_handle = 10;
    VkResult fail_format = VK_SUCCESS, fail_fd = VK_SUCCESS, fail_create = VK_SUCCESS;
    VkResult fail_allocate = VK_SUCCESS, fail_bind = VK_SUCCESS;
    VkResult fail_reset = VK_SUCCESS, fail_submit = VK_SUCCESS, fail_wait = VK_SUCCESS;
    uint32_t modifier_count = 1, memory_planes = 4, layers = 2;
    uint32_t fd_bits = 0b1010, req_bits = 0b1100;
    uint64_t req_size = 1 << 20, alignment = 4096;
    uint64_t resource_limit = 1ULL << 30, extent = 4096;
    bool modifier_present = true, importable = true, compatible_handle = true, grow_count = false;
    uint32_t last_memory_type = 99;
    int last_duplicate = -1;
    Fake() {
        require(active == nullptr); active = this;
        context.physical = reinterpret_cast<VkPhysicalDevice>(1);
        context.device = reinterpret_cast<VkDevice>(2); context.completion = &completion;
        context.memory.memoryTypeCount = 4; context.memory.memoryHeapCount = 1;
        context.api = {formats, image_properties, fd_properties, create_image, destroy_image,
                       requirements, allocate, free_memory, bind_image};
    }
    ~Fake() { active = nullptr; }
    bool empty() const { return images.empty() && memories.empty(); }
    void simulate_process_exit() { memories.clear(); images.clear(); }
    static Fake &self() { require(active != nullptr); return *active; }
    static VKAPI_ATTR void VKAPI_CALL formats(VkPhysicalDevice physical, VkFormat format, VkFormatProperties2 *output) {
        auto &s = self(); s.calls.emplace_back("format");
        require(physical == s.context.physical && format == ci::camera_format);
        auto *list = static_cast<VkDrmFormatModifierPropertiesListEXT *>(output->pNext);
        require(list && list->sType == VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT);
        if (list->pDrmFormatModifierProperties) {
            require(list->drmFormatModifierCount == 1);
            list->pDrmFormatModifierProperties[0] = {s.modifier_present ? ci::camera_modifier : 0,
                s.memory_planes, VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT};
            list->drmFormatModifierCount = s.grow_count ? 2 : 1;
        } else list->drmFormatModifierCount = s.modifier_count;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL image_properties(VkPhysicalDevice physical,
            const VkPhysicalDeviceImageFormatInfo2 *input, VkImageFormatProperties2 *output) {
        auto &s = self(); s.calls.emplace_back("image_format");
        require(physical == s.context.physical && input->format == ci::camera_format &&
                input->type == VK_IMAGE_TYPE_2D && input->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&
                input->usage == ci::import_usage && !input->flags);
        auto *drm = static_cast<const VkPhysicalDeviceImageDrmFormatModifierInfoEXT *>(input->pNext);
        require(drm && drm->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT &&
                drm->drmFormatModifier == ci::camera_modifier && drm->sharingMode == VK_SHARING_MODE_EXCLUSIVE &&
                !drm->queueFamilyIndexCount);
        auto *external = static_cast<const VkPhysicalDeviceExternalImageFormatInfo *>(drm->pNext);
        require(external && external->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO &&
                external->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);
        if (s.fail_format != VK_SUCCESS) return s.fail_format;
        auto *ext = static_cast<VkExternalImageFormatProperties *>(output->pNext);
        require(ext && ext->sType == VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES);
        ext->externalMemoryProperties.externalMemoryFeatures = VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT |
            (s.importable ? VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT : 0);
        ext->externalMemoryProperties.compatibleHandleTypes = s.compatible_handle ? VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT : 0;
        output->imageFormatProperties.maxExtent = {static_cast<uint32_t>(s.extent), static_cast<uint32_t>(s.extent), 1};
        output->imageFormatProperties.maxArrayLayers = s.layers;
        output->imageFormatProperties.maxMipLevels = 1;
        output->imageFormatProperties.sampleCounts = VK_SAMPLE_COUNT_1_BIT;
        output->imageFormatProperties.maxResourceSize = s.resource_limit;
        return VK_SUCCESS;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL fd_properties(VkDevice device, VkExternalMemoryHandleTypeFlagBits type,
                                                       int fd, VkMemoryFdPropertiesKHR *output) {
        auto &s = self(); s.calls.emplace_back("fd");
        require(device == s.context.device && type == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT &&
                output->sType == VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR && (fcntl(fd, F_GETFD) & FD_CLOEXEC));
        readable(fd); s.last_duplicate = fd;
        output->memoryTypeBits = s.fd_bits;
        return s.fail_fd;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL create_image(VkDevice device, const VkImageCreateInfo *input,
                                                      const VkAllocationCallbacks *, VkImage *output) {
        auto &s = self(); s.calls.emplace_back("create");
        require(device == s.context.device && input->imageType == VK_IMAGE_TYPE_2D && input->format == ci::camera_format &&
                input->extent.width == 2464 && input->extent.height == 2464 && input->extent.depth == 1 &&
                input->mipLevels == 1 && input->arrayLayers == 1 &&
                input->samples == VK_SAMPLE_COUNT_1_BIT && input->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&
                input->usage == ci::import_usage && input->sharingMode == VK_SHARING_MODE_EXCLUSIVE &&
                input->initialLayout == VK_IMAGE_LAYOUT_UNDEFINED && !input->flags);
        auto *external = static_cast<const VkExternalMemoryImageCreateInfo *>(input->pNext);
        require(external && external->sType == VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO &&
                external->handleTypes == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);
        auto *drm = static_cast<const VkImageDrmFormatModifierExplicitCreateInfoEXT *>(external->pNext);
        require(drm && drm->sType == VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT &&
                drm->drmFormatModifier == ci::camera_modifier && drm->drmFormatModifierPlaneCount == s.memory_planes);
        for (uint32_t i = 0; i < drm->drmFormatModifierPlaneCount; ++i) {
            const auto &plane = drm->pPlaneLayouts[i];
            require(plane.offset == i * 4096 && plane.rowPitch == 256 && !plane.size && !plane.arrayPitch && !plane.depthPitch);
        }
        *output = reinterpret_cast<VkImage>(s.next_handle++);
        // Failure outputs are undefined: the caller must not destroy this handle.
        if (s.fail_create != VK_SUCCESS) return s.fail_create;
        s.images.emplace(*output, false);
        return VK_SUCCESS;
    }
    static VKAPI_ATTR void VKAPI_CALL destroy_image(VkDevice device, VkImage image, const VkAllocationCallbacks *) {
        auto &s = self(); s.calls.emplace_back("destroy");
        require(device == s.context.device && s.completion.known_idle && s.images.erase(image) == 1);
    }
    static VKAPI_ATTR void VKAPI_CALL requirements(VkDevice device, const VkImageMemoryRequirementsInfo2 *input,
                                                  VkMemoryRequirements2 *output) {
        auto &s = self(); s.calls.emplace_back("requirements");
        require(device == s.context.device && s.images.count(input->image) &&
                input->sType == VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2);
        auto *dedicated = static_cast<VkMemoryDedicatedRequirements *>(output->pNext);
        require(dedicated && dedicated->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS);
        dedicated->requiresDedicatedAllocation = VK_TRUE;
        output->memoryRequirements = {s.req_size, s.alignment, s.req_bits};
    }
    static VKAPI_ATTR VkResult VKAPI_CALL allocate(VkDevice device, const VkMemoryAllocateInfo *input,
                                                  const VkAllocationCallbacks *, VkDeviceMemory *output) {
        auto &s = self(); s.calls.emplace_back("allocate");
        require(device == s.context.device && input->allocationSize == s.req_size && input->allocationSize <= metadata().size);
        auto *import = static_cast<const VkImportMemoryFdInfoKHR *>(input->pNext);
        require(import && import->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR &&
                import->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT && import->fd == s.last_duplicate);
        auto *dedicated = static_cast<const VkMemoryDedicatedAllocateInfo *>(import->pNext);
        require(dedicated && dedicated->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO &&
                s.images.count(dedicated->image) && !dedicated->buffer);
        readable(import->fd); s.last_memory_type = input->memoryTypeIndex;
        *output = reinterpret_cast<VkDeviceMemory>(s.next_handle++);
        if (s.fail_allocate != VK_SUCCESS) return s.fail_allocate;
        s.memories.emplace(*output, Memory{Fd(import->fd), dedicated->image});
        return VK_SUCCESS;
    }
    static VKAPI_ATTR void VKAPI_CALL free_memory(VkDevice device, VkDeviceMemory memory, const VkAllocationCallbacks *) {
        auto &s = self(); s.calls.emplace_back("free");
        require(device == s.context.device && s.completion.known_idle && s.memories.count(memory) &&
                !s.images.count(s.memories.at(memory).image));
        s.memories.erase(memory);
    }
    static VKAPI_ATTR VkResult VKAPI_CALL bind_image(VkDevice device, VkImage image, VkDeviceMemory memory, VkDeviceSize offset) {
        auto &s = self(); s.calls.emplace_back("bind");
        require(device == s.context.device && !offset && s.images.count(image) && s.memories.count(memory));
        if (s.fail_bind != VK_SUCCESS) return s.fail_bind;
        s.images.at(image) = true;
        return VK_SUCCESS;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL reset(VkDevice device, uint32_t count, const VkFence *fences) {
        auto &s = self(); s.calls.emplace_back("reset");
        require(device == s.context.device && count == 1 && fences[0] && s.completion.known_idle);
        return s.fail_reset;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL submit(VkQueue queue, uint32_t count, const VkSubmitInfo *submission, VkFence fence) {
        auto &s = self(); s.calls.emplace_back("submit");
        require(queue && count == 1 && submission->sType == VK_STRUCTURE_TYPE_SUBMIT_INFO && fence && !s.completion.known_idle);
        return s.fail_submit;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL wait(VkDevice device, uint32_t count, const VkFence *fences,
                                              VkBool32 all, uint64_t timeout) {
        auto &s = self(); s.calls.emplace_back("wait");
        require(device == s.context.device && count == 1 && fences[0] && all && timeout == 2000000000ULL && !s.completion.known_idle);
        return s.fail_wait;
    }
};
Fake *Fake::active = nullptr;

static void profile_validation() {
    Fake fake;
    auto fd = payload();
    using Change = std::function<void(MrhandBufInfo &)>;
    const std::vector<std::pair<const char *, Change>> changes = {
        {"import_invalid_record", [](auto &i) { i.magic = 0; }},
        {"import_invalid_record", [](auto &i) { i.id = 0; }},
        {"import_invalid_record", [](auto &i) { i.generation = 0; }},
        {"import_invalid_record", [](auto &i) { i.hasImage = 0; }},
        {"import_allocation_size_bounds", [](auto &i) { i.size = 0; }},
        {"import_allocation_size_bounds", [](auto &i) { i.size = ci::maximum_allocation + 1; }},
        {"import_nonzero_binding_offset", [](auto &i) { i.bindOffset = 4096; }},
        {"import_unsupported_handle_type", [](auto &i) { i.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT; }},
        {"import_unsupported_image_profile", [](auto &i) { i.format = VK_FORMAT_R8_UNORM; }},
        {"import_unsupported_image_profile", [](auto &i) { i.width = 1056; }},
        {"import_unsupported_image_profile", [](auto &i) { i.height = 1024; }},
        {"import_unsupported_image_profile", [](auto &i) { i.arrayLayers = 0; }},
        {"import_unsupported_image_profile", [](auto &i) { i.arrayLayers = 2; }},
        {"import_unsupported_image_profile", [](auto &i) { i.arrayLayers = 3; }},
        {"import_unsupported_image_profile", [](auto &i) { i.tiling = VK_IMAGE_TILING_OPTIMAL; }},
        {"import_unsupported_image_profile", [](auto &i) { i.usage = VK_IMAGE_USAGE_SAMPLED_BIT; }},
        {"import_unsupported_image_profile", [](auto &i) { i.hasModifier = 0; }},
        {"import_unsupported_image_profile", [](auto &i) { i.modifier = 0; }},
        {"import_plane_count_bounds", [](auto &i) { i.planeCount = 0; }},
        {"import_plane_count_bounds", [](auto &i) { i.planeCount = 5; }},
        {"import_plane_layout_bounds", [](auto &i) { i.planeOffset[3] = UINT64_MAX; }},
        {"import_plane_layout_bounds", [](auto &i) { i.planeRowPitch[3] = UINT64_MAX; }},
        {"import_plane_layout_bounds", [](auto &i) { i.planeRowPitch[0] = 0; }},
        {"import_plane_layout_bounds", [](auto &i) { i.planeOffset[0] = i.size - 1; i.planeRowPitch[0] = 2; }},
        {"import_unused_plane_metadata", [](auto &i) { i.planeCount = 2; }},
    };
    for (const auto &change : changes) {
        auto info = metadata(); change.second(info);
        rejects(change.first, [&] { ci::import_image(fake.context, info, fd.get()); });
        require(fake.calls.empty()); readable(fd.get());
    }
}
static void capability_validation() {
    auto fd = payload();
    const std::vector<std::pair<const char *, std::function<void(Fake &)>>> changes = {
        {"import_modifier_or_plane_count_unsupported", [](auto &f) { f.modifier_present = false; }},
        {"import_modifier_or_plane_count_unsupported", [](auto &f) { f.memory_planes = 2; }},
        {"import_modifier_or_plane_count_unsupported", [](auto &f) { f.modifier_count = 0; }},
        {"import_modifier_count_bounds", [](auto &f) { f.modifier_count = 4097; }},
        {"import_modifier_count_changed", [](auto &f) { f.grow_count = true; }},
        {"import_image_format_query_failed", [](auto &f) { f.fail_format = VK_ERROR_FORMAT_NOT_SUPPORTED; }},
        {"import_dma_buf_not_importable", [](auto &f) { f.importable = false; }},
        {"import_dma_buf_not_importable", [](auto &f) { f.compatible_handle = false; }},
        {"import_image_limits", [](auto &f) { f.extent = 1024; }},
        {"import_image_limits", [](auto &f) { f.layers = 0; }},
        {"import_image_limits", [](auto &f) { f.resource_limit = 1024; }},
    };
    for (const auto &change : changes) {
        Fake fake; change.second(fake);
        rejects(change.first, [&] { ci::import_image(fake.context, metadata(), fd.get()); });
        require(std::find(fake.calls.begin(), fake.calls.end(), "create") == fake.calls.end() && fake.empty());
        readable(fd.get());
    }
}
static void requirements_validation() {
    auto fd = payload();
    const std::vector<std::pair<const char *, std::function<void(Fake &)>>> changes = {
        {"import_memory_requirements_bounds", [](auto &f) { f.req_size = 0; }},
        {"import_memory_requirements_bounds", [](auto &f) { f.req_size = metadata().size + 1; }},
        {"import_memory_requirements_bounds", [](auto &f) { f.alignment = 0; }},
        {"import_memory_requirements_bounds", [](auto &f) { f.alignment = 3; }},
        {"import_no_compatible_memory_type", [](auto &f) { f.fd_bits = 0; }},
        {"import_no_compatible_memory_type", [](auto &f) { f.req_bits = 0; }},
        {"import_no_compatible_memory_type", [](auto &f) { f.req_bits = f.fd_bits = 1u << 10; }},
    };
    for (const auto &change : changes) {
        Fake fake; change.second(fake);
        rejects(change.first, [&] { ci::import_image(fake.context, metadata(), fd.get()); });
        require(fake.empty() && fake.calls.back() == "destroy"); readable(fd.get());
    }
}
static void success_and_ownership() {
    Fake fake;
    auto original = payload();
    size_t before = descriptors_open();
    auto image = ci::import_image(fake.context, metadata(), original.get());
    require(image.image() && image.memory() && fake.last_memory_type == 3);
    require(fake.memories.size() == 1 && fake.images.size() == 1 && fake.last_duplicate != original.get());
    require(descriptors_open() == before + 1); readable(original.get()); readable(fake.last_duplicate);
    require(image.close() && image.close());
    require(fake.empty() && descriptors_open() == before && !fake.completion.cleanup_deferred);
    require(fake.calls[fake.calls.size() - 2] == "destroy" && fake.calls.back() == "free");
    readable(original.get());
}
static void driver_failures() {
    auto fd = payload();
    const std::vector<std::pair<const char *, std::function<void(Fake &)>>> changes = {
        {"import_fd_query_failed", [](auto &f) { f.fail_fd = VK_ERROR_INVALID_EXTERNAL_HANDLE; }},
        {"import_create_image_failed", [](auto &f) { f.fail_create = VK_ERROR_OUT_OF_DEVICE_MEMORY; }},
        {"import_allocate_memory_failed", [](auto &f) { f.fail_allocate = VK_ERROR_OUT_OF_DEVICE_MEMORY; }},
        {"import_bind_image_failed", [](auto &f) { f.fail_bind = VK_ERROR_DEVICE_LOST; }},
    };
    size_t before = descriptors_open();
    for (int repetition = 0; repetition < 20; ++repetition)
        for (const auto &change : changes) {
            Fake fake; change.second(fake);
            rejects(change.first, [&] { ci::import_image(fake.context, metadata(), fd.get()); });
            require(fake.empty() && descriptors_open() == before); readable(fd.get());
            require(fcntl(fake.last_duplicate, F_GETFD) == -1 && errno == EBADF);
        }
}
static void two_plane_layout() {
    // The Frame's Turnip capability query advertised two memory planes. This
    // generated layout exercises that count; its offsets/pitches are not camera data.
    Fake fake;
    fake.memory_planes = 2;
    auto fd = payload();
    auto info = metadata(2);
    require(!info.planeOffset[2] && !info.planeRowPitch[2] && !info.planeOffset[3] && !info.planeRowPitch[3]);
    auto image = ci::import_image(fake.context, info, fd.get());
    require(image.image() && image.memory() && fake.last_memory_type == 3);
    require(image.close() && fake.empty());
    readable(fd.get());
    // Four is still a valid synthetic modifier case, but not the count this GPU reported.
    rejects("import_modifier_or_plane_count_unsupported", [&] {
        ci::import_image(fake.context, metadata(4), fd.get());
    });
    require(fake.empty()); readable(fd.get());
}
static void moves_and_batch_rollback() {
    Fake fake;
    auto fd = payload();
    {
        auto first = ci::import_image(fake.context, metadata(), fd.get());
        auto second = ci::import_image(fake.context, metadata(), fd.get());
        auto first_handle = first.image();
        second = std::move(first);
        require(!first.image() && second.image() == first_handle && fake.images.size() == 1);
        auto moved = std::move(second);
        require(!second.image() && moved.image() == first_handle);
    }
    require(fake.empty());
    rejects("import_allocate_memory_failed", [&] {
        std::vector<ci::Image> images;
        for (int i = 0; i < 16; ++i) images.push_back(ci::import_image(fake.context, metadata(), fd.get()));
        fake.fail_allocate = VK_ERROR_OUT_OF_DEVICE_MEMORY;
        images.push_back(ci::import_image(fake.context, metadata(), fd.get()));
    });
    require(fake.empty()); readable(fd.get());
}
static void pending_work_defers_cleanup() {
    Fake fake;
    auto fd = payload();
    fake.completion.submitted();
    rejects("import_work_in_flight", [&] { ci::import_image(fake.context, metadata(), fd.get()); });
    require(fake.calls.empty());
    fake.completion.completed();
    auto image = ci::import_image(fake.context, metadata(), fd.get());
    size_t calls = fake.calls.size();
    fake.completion.submitted();
    require(!image.close() && fake.completion.cleanup_deferred);
    require(fake.calls.size() == calls && fake.images.size() == 1 && fake.memories.size() == 1);
    // Model worker/process teardown, not a Vulkan idle wait or confirmed API cleanup.
    fake.simulate_process_exit();
    require(fake.empty()); readable(fd.get());
}
static void context_and_memory_bounds() {
    Fake fake;
    auto fd = payload();
    auto incomplete = fake.context;
    incomplete.api.bind_image = nullptr;
    rejects("import_context_incomplete", [&] { ci::import_image(incomplete, metadata(), fd.get()); });
    incomplete = fake.context; incomplete.completion = nullptr;
    rejects("import_context_incomplete", [&] { ci::import_image(incomplete, metadata(), fd.get()); });
    for (uint32_t count : {0u, 33u}) {
        auto context = fake.context; context.memory.memoryTypeCount = count;
        rejects("import_memory_properties_bounds", [&] { ci::import_image(context, metadata(), fd.get()); });
    }
    auto context = fake.context; context.memory.memoryTypes[0].heapIndex = 1;
    rejects("import_memory_heap_bounds", [&] { ci::import_image(context, metadata(), fd.get()); });
    rejects("descriptor_duplication_failed", [&] { ci::import_image(fake.context, metadata(), -1); });
    require(fake.calls.empty());
    fake.context.memory.memoryTypeCount = 32; fake.fd_bits = fake.req_bits = 1u << 31;
    { auto image = ci::import_image(fake.context, metadata(), fd.get()); require(fake.last_memory_type == 31); }
    require(fake.empty()); readable(fd.get());
}
static void submission_completion() {
    auto fd = payload();
    for (int mode = 0; mode < 4; ++mode) {
        Fake fake;
        auto image = ci::import_image(fake.context, metadata(), fd.get());
        const ci::SubmissionApi api{Fake::reset, Fake::submit, Fake::wait};
        VkSubmitInfo submission{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        VkQueue queue = reinterpret_cast<VkQueue>(3);
        VkFence fence = reinterpret_cast<VkFence>(4);
        if (mode == 1) fake.fail_reset = VK_ERROR_DEVICE_LOST;
        if (mode == 2) fake.fail_submit = VK_ERROR_DEVICE_LOST;
        if (mode == 3) fake.fail_wait = VK_TIMEOUT;
        auto action = [&] { ci::submit_and_wait(fake.completion, api, fake.context.device, queue, fence, submission); };
        if (!mode) action();
        else rejects(mode == 1 ? "import_fence_reset_failed" : mode == 2 ? "import_queue_submit_failed" :
                     "import_completion_wait_failed", action);
        if (mode < 2) {
            require(fake.completion.known_idle && image.close() && fake.empty());
            if (mode == 1) require(std::find(fake.calls.begin(), fake.calls.end(), "submit") == fake.calls.end());
        } else {
            require(!fake.completion.known_idle && !image.close() && fake.completion.cleanup_deferred);
            fake.simulate_process_exit();
        }
        readable(fd.get());
    }
}

int main(int argc, char **argv) {
    const std::vector<std::pair<const char *, void (*)()>> tests = {
        {"profile", profile_validation}, {"capabilities", capability_validation},
        {"requirements", requirements_validation}, {"ownership", success_and_ownership},
        {"failures", driver_failures}, {"moves", moves_and_batch_rollback},
        {"pending", pending_work_defers_cleanup}, {"context", context_and_memory_bounds},
        {"submission", submission_completion},
        {"two_planes", two_plane_layout},
    };
    bool all = argc == 2 && !strcmp(argv[1], "--self-test");
    bool one = argc == 3 && !strcmp(argv[1], "--case");
    if (!all && !one) {
        std::cout << "{\"report_kind\":\"capture_import_fixture\",\"status\":\"not_run\",\"runtime_initialized\":false}\n";
        return argc == 1 ? 3 : 64;
    }
    try {
        size_t before = descriptors_open(), ran = 0;
        for (const auto &test : tests) {
            if (!all && strcmp(test.first, argv[2])) continue;
            test.second(); ++ran;
            require(Fake::active == nullptr && descriptors_open() == before);
        }
        if (!ran) throw Error("unknown_fixture_case");
        std::cout << "{\"report_kind\":\"capture_import_fixture\",\"schema_version\":1,\"status\":\"passed\",\"tests_run\":"
                  << ran << ",\"descriptor_leak_detected\":false,\"runtime_initialized\":false,\"capture_started\":false,"
                     "\"scope\":\"Synthetic function-pointer dispatch and generated memfd only; no GPU import or camera\"}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
