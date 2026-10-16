// Host-visible transfer-buffer ownership; fake dispatch tests require no driver.
#pragma once
#include "capture_import.hpp"

namespace framejacker::capture_host {
using capture_import::check;
using transport::Error;
struct Api {
    PFN_vkCreateBuffer create = nullptr;
    PFN_vkDestroyBuffer destroy = nullptr;
    PFN_vkGetBufferMemoryRequirements requirements = nullptr;
    PFN_vkAllocateMemory allocate = nullptr;
    PFN_vkFreeMemory free = nullptr;
    PFN_vkBindBufferMemory bind = nullptr;
    PFN_vkMapMemory map = nullptr;
    PFN_vkUnmapMemory unmap = nullptr;
    bool complete() const { return create && destroy && requirements && allocate && free && bind && map && unmap; }
};
struct Context {
    VkDevice device = VK_NULL_HANDLE;
    VkPhysicalDeviceMemoryProperties memory{};
    capture_import::Completion *completion = nullptr;
    Api api;
};
class Buffer {
    Context context_{};
    VkBuffer buffer_ = VK_NULL_HANDLE;
    VkDeviceMemory memory_ = VK_NULL_HANDLE;
    void *mapped_ = nullptr;
    bool mapping_active_ = false;
public:
    Buffer() = default;
    explicit Buffer(const Context &context) : context_(context) {}
    ~Buffer() { close(); }
    Buffer(const Buffer &) = delete;
    Buffer &operator=(const Buffer &) = delete;
    Buffer(Buffer &&other) noexcept { *this = std::move(other); }
    Buffer &operator=(Buffer &&other) noexcept {
        if (this != &other) {
            close(); context_ = other.context_;
            buffer_ = std::exchange(other.buffer_, VK_NULL_HANDLE);
            memory_ = std::exchange(other.memory_, VK_NULL_HANDLE);
            mapped_ = std::exchange(other.mapped_, nullptr);
            mapping_active_ = std::exchange(other.mapping_active_, false);
        }
        return *this;
    }
    bool close() noexcept {
        if (!buffer_ && !memory_) return true;
        const bool safe = context_.completion->known_idle;
        if (safe) {
            if (mapping_active_) context_.api.unmap(context_.device, memory_);
            if (buffer_) context_.api.destroy(context_.device, buffer_, nullptr);
            if (memory_) context_.api.free(context_.device, memory_, nullptr);
        } else context_.completion->cleanup_deferred = true;
        buffer_ = VK_NULL_HANDLE; memory_ = VK_NULL_HANDLE; mapped_ = nullptr; mapping_active_ = false;
        return safe;
    }
    VkBuffer buffer() const { return buffer_; }
    VkDeviceMemory memory() const { return memory_; }
    void *data() const { return mapped_; }
    friend Buffer create(const Context &, VkDeviceSize);
};

inline Buffer create(const Context &context, VkDeviceSize size) {
    if (!context.device || !context.completion || !context.api.complete())
        throw Error("host_context_incomplete");
    if (!context.completion->known_idle) throw Error("host_work_in_flight");
    if (!size || size > capture_import::maximum_allocation) throw Error("host_size_bounds");
    const auto &mp = context.memory;
    if (!mp.memoryTypeCount || mp.memoryTypeCount > VK_MAX_MEMORY_TYPES ||
        !mp.memoryHeapCount || mp.memoryHeapCount > VK_MAX_MEMORY_HEAPS)
        throw Error("host_memory_properties_bounds");
    Buffer result(context);
    VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    info.size = size; info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VkBuffer buffer = VK_NULL_HANDLE;
    check(context.api.create(context.device, &info, nullptr, &buffer), "host_create_failed");
    result.buffer_ = buffer;
    VkMemoryRequirements requirements{};
    context.api.requirements(context.device, buffer, &requirements);
    if (requirements.size < size || requirements.size > capture_import::maximum_allocation ||
        !requirements.alignment || (requirements.alignment & (requirements.alignment - 1)))
        throw Error("host_requirements_bounds");
    uint32_t type = mp.memoryTypeCount;
    constexpr auto flags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        if (mp.memoryTypes[i].heapIndex >= mp.memoryHeapCount) throw Error("host_memory_heap_bounds");
        if (type == mp.memoryTypeCount && (requirements.memoryTypeBits & (uint32_t{1} << i)) &&
            (mp.memoryTypes[i].propertyFlags & flags) == flags &&
            mp.memoryHeaps[mp.memoryTypes[i].heapIndex].size >= requirements.size) type = i;
    }
    if (type == mp.memoryTypeCount) throw Error("host_memory_type_unavailable");
    VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    allocation.allocationSize = requirements.size; allocation.memoryTypeIndex = type;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    check(context.api.allocate(context.device, &allocation, nullptr, &memory), "host_allocate_failed");
    result.memory_ = memory;
    check(context.api.bind(context.device, buffer, memory, 0), "host_bind_failed");
    void *mapped = nullptr;
    check(context.api.map(context.device, memory, 0, VK_WHOLE_SIZE, 0, &mapped), "host_map_failed");
    result.mapped_ = mapped;
    result.mapping_active_ = true;
    if (!mapped) throw Error("host_null_mapping");
    return result;
}
} // namespace framejacker::capture_host
