// Original SightCircuit GPU ownership. Injected dispatch; no loader or camera access.
#pragma once
#include "capture_import.hpp"
#include <memory>

namespace sightcircuit::gpu {
namespace ci = capture_import;
using transport::Error;
using ci::check;
constexpr uint64_t allocation_budget = 256ULL << 20;

struct Api {
    ci::Api imports;
    PFN_vkEnumerateInstanceVersion instance_version = nullptr;
    PFN_vkCreateInstance create_instance = nullptr;
    PFN_vkDestroyInstance destroy_instance = nullptr;
    PFN_vkEnumeratePhysicalDevices devices = nullptr;
    PFN_vkGetPhysicalDeviceProperties properties = nullptr;
    PFN_vkEnumerateDeviceExtensionProperties extensions = nullptr;
    PFN_vkGetPhysicalDeviceFeatures2 features = nullptr;
    PFN_vkGetPhysicalDeviceMemoryProperties memory = nullptr;
    PFN_vkGetPhysicalDeviceQueueFamilyProperties families = nullptr;
    PFN_vkCreateDevice create_device = nullptr;
    PFN_vkDestroyDevice destroy_device = nullptr;
    PFN_vkGetDeviceQueue queue = nullptr;
    PFN_vkGetDeviceProcAddr device_proc = nullptr;
    PFN_vkCreateCommandPool create_pool = nullptr;
    PFN_vkDestroyCommandPool destroy_pool = nullptr;
    PFN_vkCreateBuffer create_buffer = nullptr;
    PFN_vkDestroyBuffer destroy_buffer = nullptr;
    PFN_vkGetBufferMemoryRequirements2 buffer_requirements = nullptr;
    PFN_vkBindBufferMemory bind_buffer = nullptr;
    PFN_vkMapMemory map = nullptr;
    PFN_vkUnmapMemory unmap = nullptr;
    PFN_vkInvalidateMappedMemoryRanges invalidate = nullptr;
    PFN_vkAllocateCommandBuffers allocate_commands = nullptr;
    PFN_vkFreeCommandBuffers free_commands = nullptr;
    PFN_vkCreateFence create_fence = nullptr;
    PFN_vkDestroyFence destroy_fence = nullptr;
    bool complete() const {
        return instance_version && create_instance && destroy_instance && devices && properties && extensions &&
            features && memory && families && create_device && destroy_device && queue && device_proc &&
            create_pool && destroy_pool && create_buffer && destroy_buffer && buffer_requirements && bind_buffer &&
            map && unmap && invalidate && allocate_commands && free_commands && create_fence && destroy_fence &&
            imports.format_properties && imports.image_properties && imports.create_image && imports.destroy_image &&
            imports.image_requirements && imports.allocate_memory && imports.free_memory && imports.bind_image;
    }
};

struct State {
    Api api;
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkPhysicalDeviceMemoryProperties memory{};
    uint32_t family = 0;
    ci::Completion completion;
    uint64_t allocated_bytes = 0;
    explicit State(const Api &value) : api(value) {}
    ~State() {
        if (!completion.known_idle || completion.cleanup_deferred) return;
        if (pool) api.destroy_pool(device, pool, nullptr);
        if (device) api.destroy_device(device, nullptr);
        if (instance) api.destroy_instance(instance, nullptr);
    }
    void require_idle() const {
        if (!completion.known_idle || completion.cleanup_deferred) throw Error("gpu_work_or_cleanup_unconfirmed");
    }
    uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags required, VkMemoryPropertyFlags preferred = 0) const {
        uint32_t fallback = VK_MAX_MEMORY_TYPES;
        for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
            const auto flags = memory.memoryTypes[i].propertyFlags;
            if ((bits & (uint32_t{1} << i)) && (flags & required) == required) {
                if ((flags & preferred) == preferred) return i;
                if (fallback == VK_MAX_MEMORY_TYPES) fallback = i;
            }
        }
        if (fallback == VK_MAX_MEMORY_TYPES) throw Error("gpu_memory_type_unavailable");
        return fallback;
    }
};

// A resource keeps its device alive. The last child releases the pool/device/
// instance only after its own destruction. Uncertain work poisons the whole
// state and defers all remaining API destruction to worker/process teardown.
class Resource {
    std::shared_ptr<State> state_;
    VkImage image_ = VK_NULL_HANDLE;
    VkBuffer buffer_ = VK_NULL_HANDLE;
    VkDeviceMemory memory_ = VK_NULL_HANDLE;
    VkCommandBuffer command_ = VK_NULL_HANDLE;
    VkFence fence_ = VK_NULL_HANDLE;
    void *mapped_ = nullptr;
    uint64_t allocation_size_ = 0, size_ = 0;
    explicit Resource(std::shared_ptr<State> state) : state_(std::move(state)) {}
    void allocate(const VkMemoryRequirements &req, VkMemoryPropertyFlags flags, VkMemoryPropertyFlags preferred = 0) {
        if (!req.size || req.size > allocation_budget || !req.alignment || (req.alignment & (req.alignment - 1)) ||
            req.size > allocation_budget - state_->allocated_bytes) throw Error("gpu_allocation_bounds_or_budget");
        VkMemoryDedicatedAllocateInfo dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO};
        dedicated.image = image_; dedicated.buffer = buffer_;
        VkMemoryAllocateInfo info{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &dedicated};
        info.allocationSize = req.size; info.memoryTypeIndex = state_->memory_type(req.memoryTypeBits, flags, preferred);
        VkDeviceMemory memory = VK_NULL_HANDLE;
        check(state_->api.imports.allocate_memory(state_->device, &info, nullptr, &memory), "gpu_allocate_failed");
        if (!memory) throw Error("gpu_null_memory");
        memory_ = memory; allocation_size_ = req.size; state_->allocated_bytes += req.size;
    }
public:
    Resource() = default;
    Resource(const Resource &) = delete;
    Resource &operator=(const Resource &) = delete;
    Resource(Resource &&other) noexcept { *this = std::move(other); }
    Resource &operator=(Resource &&other) noexcept {
        if (this != &other) {
            close(); state_ = std::move(other.state_);
            image_ = std::exchange(other.image_, VK_NULL_HANDLE);
            buffer_ = std::exchange(other.buffer_, VK_NULL_HANDLE);
            memory_ = std::exchange(other.memory_, VK_NULL_HANDLE);
            command_ = std::exchange(other.command_, VK_NULL_HANDLE);
            fence_ = std::exchange(other.fence_, VK_NULL_HANDLE);
            mapped_ = std::exchange(other.mapped_, nullptr);
            allocation_size_ = std::exchange(other.allocation_size_, 0);
            size_ = std::exchange(other.size_, 0);
        }
        return *this;
    }
    ~Resource() { close(); }
    bool close() noexcept {
        bool safe = true;
        if (state_) {
            auto &s = *state_;
            safe = s.completion.known_idle && !s.completion.cleanup_deferred;
            if (safe) {
                if (mapped_) s.api.unmap(s.device, memory_);
                if (fence_) s.api.destroy_fence(s.device, fence_, nullptr);
                if (command_) s.api.free_commands(s.device, s.pool, 1, &command_);
                if (image_) s.api.imports.destroy_image(s.device, image_, nullptr);
                if (buffer_) s.api.destroy_buffer(s.device, buffer_, nullptr);
                if (memory_) { s.api.imports.free_memory(s.device, memory_, nullptr); s.allocated_bytes -= allocation_size_; }
            } else s.completion.cleanup_deferred = true;
        }
        image_ = VK_NULL_HANDLE; buffer_ = VK_NULL_HANDLE; memory_ = VK_NULL_HANDLE;
        command_ = VK_NULL_HANDLE; fence_ = VK_NULL_HANDLE; mapped_ = nullptr;
        allocation_size_ = size_ = 0;
        state_.reset();
        return safe;
    }
    VkImage image() const { return image_; }
    VkBuffer buffer() const { return buffer_; }
    VkDeviceMemory memory() const { return memory_; }
    VkCommandBuffer command() const { return command_; }
    VkFence fence() const { return fence_; }
    void *mapped() const { return mapped_; }
    uint64_t size() const { return size_; }
    void invalidate() const {
        if (!state_ || !mapped_) throw Error("gpu_buffer_not_mapped");
        state_->require_idle();
        VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
        range.memory = memory_; range.size = VK_WHOLE_SIZE;
        check(state_->api.invalidate(state_->device, 1, &range), "gpu_invalidate_failed");
    }
    friend class Device;
};

class ImportedImage {
    std::shared_ptr<State> state_;
    ci::Image image_;
    ImportedImage(std::shared_ptr<State> state, ci::Image image)
        : state_(std::move(state)), image_(std::move(image)) {}
public:
    ImportedImage() = default;
    ImportedImage(const ImportedImage &) = delete;
    ImportedImage &operator=(const ImportedImage &) = delete;
    ImportedImage(ImportedImage &&other) noexcept { *this = std::move(other); }
    ImportedImage &operator=(ImportedImage &&other) noexcept {
        if (this != &other) {
            close(); state_ = std::move(other.state_); image_ = std::move(other.image_);
        }
        return *this;
    }
    ~ImportedImage() { close(); }
    bool close() noexcept {
        // A poisoned state is never considered retirable again in this worker.
        if (state_ && state_->completion.cleanup_deferred) state_->completion.known_idle = false;
        bool clean = image_.close(); state_.reset(); return clean;
    }
    VkImage image() const { return image_.image(); }
    friend class Device;
};

inline void bounded_count(uint32_t value, uint32_t maximum) {
    if (value > maximum) throw Error("gpu_enumeration_bounds");
}

class Device {
    std::shared_ptr<State> state_;
    explicit Device(std::shared_ptr<State> state) : state_(std::move(state)) {}
    State &state() const {
        if (!state_) throw Error("gpu_device_not_initialized");
        return *state_;
    }
    static bool suitable(State &s, VkPhysicalDevice physical, uint32_t &family,
                         VkPhysicalDeviceMemoryProperties &memory) {
        VkPhysicalDeviceProperties properties{}; s.api.properties(physical, &properties);
        if (properties.apiVersion < VK_API_VERSION_1_3 || properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU) return false;
        uint32_t count = 0;
        check(s.api.extensions(physical, nullptr, &count, nullptr), "gpu_extension_count_failed");
        bounded_count(count, 4096);
        std::vector<VkExtensionProperties> extensions(count);
        if (count) check(s.api.extensions(physical, nullptr, &count, extensions.data()), "gpu_extensions_failed");
        bounded_count(count, static_cast<uint32_t>(extensions.size()));
        for (const char *required : {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
                                     "VK_EXT_image_drm_format_modifier", "VK_EXT_queue_family_foreign"}) {
            bool found = false;
            for (uint32_t i = 0; i < count; ++i)
                found |= strnlen(extensions[i].extensionName, VK_MAX_EXTENSION_NAME_SIZE) < VK_MAX_EXTENSION_NAME_SIZE &&
                    std::strcmp(extensions[i].extensionName, required) == 0;
            if (!found) return false;
        }
        VkPhysicalDeviceSamplerYcbcrConversionFeatures conversion{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES};
        VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &conversion};
        s.api.features(physical, &features);
        if (!conversion.samplerYcbcrConversion) return false;
        s.api.memory(physical, &memory);
        bounded_count(memory.memoryTypeCount, VK_MAX_MEMORY_TYPES); bounded_count(memory.memoryHeapCount, VK_MAX_MEMORY_HEAPS);
        bool host = false, local = false;
        for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
            if (memory.memoryTypes[i].heapIndex >= memory.memoryHeapCount) throw Error("gpu_memory_heap_bounds");
            auto flags = memory.memoryTypes[i].propertyFlags;
            constexpr auto needed = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
            host |= (flags & needed) == needed; local |= (flags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) != 0;
        }
        if (!host || !local) return false;
        count = 0; s.api.families(physical, &count, nullptr); bounded_count(count, 256);
        std::vector<VkQueueFamilyProperties> queues(count);
        if (count) s.api.families(physical, &count, queues.data());
        bounded_count(count, static_cast<uint32_t>(queues.size()));
        bool graphics = false;
        for (uint32_t i = 0; i < count; ++i)
            if (queues[i].queueCount && (queues[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) { family = i; graphics = true; break; }
        if (!graphics) return false;
        VkFormatProperties2 r8{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2};
        s.api.imports.format_properties(physical, VK_FORMAT_R8_UNORM, &r8);
        constexpr auto needed = VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_BLIT_DST_BIT |
            VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
        if ((r8.formatProperties.optimalTilingFeatures & needed) != needed) return false;
        VkPhysicalDeviceImageFormatInfo2 input{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
        input.format = VK_FORMAT_R8_UNORM; input.type = VK_IMAGE_TYPE_2D;
        input.tiling = VK_IMAGE_TILING_OPTIMAL; input.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        VkImageFormatProperties2 output{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
        auto result = s.api.imports.image_properties(physical, &input, &output);
        if (result == VK_ERROR_FORMAT_NOT_SUPPORTED) return false;
        check(result, "gpu_r8_format_query_failed");
        const auto &limits = output.imageFormatProperties;
        return limits.maxExtent.width >= 2464 && limits.maxExtent.height >= 2464 && limits.maxExtent.depth >= 1 &&
            limits.maxArrayLayers >= 1 && limits.maxMipLevels >= 1 && (limits.sampleCounts & VK_SAMPLE_COUNT_1_BIT);
    }
public:
    Device() = default;
    Device(const Device &) = delete;
    Device &operator=(const Device &) = delete;
    Device(Device &&) = default;
    Device &operator=(Device &&) = default;
    static Device open(const Api &api) {
        if (!api.complete()) throw Error("gpu_dispatch_incomplete");
        auto s = std::make_shared<State>(api);
        uint32_t version = 0; check(api.instance_version(&version), "gpu_loader_version_failed");
        if (version < VK_API_VERSION_1_3) throw Error("gpu_vulkan_1_3_required");
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.pApplicationName = "sightcircuit-capture";
        app.apiVersion = VK_API_VERSION_1_3;
        VkInstanceCreateInfo instance_info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; instance_info.pApplicationInfo = &app;
        VkInstance instance = VK_NULL_HANDLE;
        check(api.create_instance(&instance_info, nullptr, &instance), "gpu_create_instance_failed");
        if (!instance) throw Error("gpu_null_instance");
        s->instance = instance;
        uint32_t count = 0; check(api.devices(instance, &count, nullptr), "gpu_device_count_failed"); bounded_count(count, 32);
        std::vector<VkPhysicalDevice> devices(count);
        if (count) check(api.devices(instance, &count, devices.data()), "gpu_devices_failed");
        bounded_count(count, static_cast<uint32_t>(devices.size()));
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t family = 0; VkPhysicalDeviceMemoryProperties memory{};
            if (!suitable(*s, devices[i], family, memory)) continue;
            if (s->physical) throw Error("gpu_multiple_compatible_devices_require_selection");
            s->physical = devices[i]; s->family = family; s->memory = memory;
        }
        if (!s->physical) throw Error("gpu_no_compatible_device");
        const float priority = 0;
        VkDeviceQueueCreateInfo queue{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
        queue.queueFamilyIndex = s->family; queue.queueCount = 1; queue.pQueuePriorities = &priority;
        const char *extensions[] = {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
                                    "VK_EXT_image_drm_format_modifier", "VK_EXT_queue_family_foreign"};
        VkPhysicalDeviceSamplerYcbcrConversionFeatures conversion{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES};
        conversion.samplerYcbcrConversion = VK_TRUE;
        VkDeviceCreateInfo info{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &conversion};
        info.queueCreateInfoCount = 1; info.pQueueCreateInfos = &queue; info.enabledExtensionCount = 4; info.ppEnabledExtensionNames = extensions;
        VkDevice device = VK_NULL_HANDLE;
        check(api.create_device(s->physical, &info, nullptr, &device), "gpu_create_device_failed");
        if (!device) throw Error("gpu_null_device");
        s->device = device; api.queue(device, s->family, 0, &s->queue);
        if (!s->queue) throw Error("gpu_null_queue");
        s->api.imports.fd_properties = reinterpret_cast<PFN_vkGetMemoryFdPropertiesKHR>(api.device_proc(device, "vkGetMemoryFdPropertiesKHR"));
        if (!s->api.imports.fd_properties) throw Error("gpu_fd_import_dispatch_unavailable");
        VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        pool_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT; pool_info.queueFamilyIndex = s->family;
        VkCommandPool pool = VK_NULL_HANDLE;
        check(api.create_pool(device, &pool_info, nullptr, &pool), "gpu_create_pool_failed");
        if (!pool) throw Error("gpu_null_pool");
        s->pool = pool;
        return Device(std::move(s));
    }
    ImportedImage import_image(const MrhandBufInfo &info, int fd) const {
        auto &s = state(); s.require_idle();
        ci::Context context{s.physical, s.device, s.memory, s.api.imports, &s.completion};
        auto image = ci::import_image(context, info, fd);
        return ImportedImage(state_, std::move(image));
    }
    ci::Completion &completion() const { return state().completion; }
    VkDevice handle() const { return state().device; }
    VkPhysicalDevice physical() const { return state().physical; }
    VkQueue queue() const { return state().queue; }
    uint32_t family() const { return state().family; }
    uint64_t allocated_bytes() const { return state().allocated_bytes; }
    bool close() {
        if (!state_) return true;
        if (state_.use_count() != 1) throw Error("gpu_resources_still_owned");
        if (state_->completion.known_idle && !state_->completion.cleanup_deferred && state_->allocated_bytes)
            throw Error("gpu_allocations_still_owned");
        bool safe = state_->completion.known_idle && !state_->completion.cleanup_deferred;
        if (!safe) state_->completion.cleanup_deferred = true;
        state_.reset();
        return safe;
    }
    Resource host_buffer(uint64_t bytes) const {
        auto &s = state(); s.require_idle();
        if (!bytes || bytes > (64ULL << 20)) throw Error("gpu_host_buffer_size_bounds");
        Resource result(state_); result.size_ = bytes;
        VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO}; info.size = bytes; info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        VkBuffer buffer = VK_NULL_HANDLE;
        check(s.api.create_buffer(s.device, &info, nullptr, &buffer), "gpu_create_buffer_failed");
        if (!buffer) throw Error("gpu_null_buffer");
        result.buffer_ = buffer;
        VkMemoryDedicatedRequirements dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS};
        VkMemoryRequirements2 requirements{VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2, &dedicated};
        VkBufferMemoryRequirementsInfo2 query{VK_STRUCTURE_TYPE_BUFFER_MEMORY_REQUIREMENTS_INFO_2}; query.buffer = buffer;
        s.api.buffer_requirements(s.device, &query, &requirements);
        if (requirements.memoryRequirements.size < bytes) throw Error("gpu_buffer_requirements_too_small");
        result.allocate(requirements.memoryRequirements, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        check(s.api.bind_buffer(s.device, buffer, result.memory_, 0), "gpu_bind_buffer_failed");
        void *mapped = nullptr;
        check(s.api.map(s.device, result.memory_, 0, VK_WHOLE_SIZE, 0, &mapped), "gpu_map_failed");
        if (!mapped) throw Error("gpu_null_mapping");
        result.mapped_ = mapped;
        return result;
    }
    Resource image(uint32_t width, uint32_t height) const {
        auto &s = state(); s.require_idle();
        if (!width || !height || width > 2464 || height > 2464) throw Error("gpu_image_dimensions_bounds");
        Resource result(state_);
        VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO}; info.imageType = VK_IMAGE_TYPE_2D; info.format = VK_FORMAT_R8_UNORM;
        info.extent = {width, height, 1}; info.mipLevels = info.arrayLayers = 1; info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.tiling = VK_IMAGE_TILING_OPTIMAL; info.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        VkImage image = VK_NULL_HANDLE;
        check(s.api.imports.create_image(s.device, &info, nullptr, &image), "gpu_create_scratch_image_failed");
        if (!image) throw Error("gpu_null_image");
        result.image_ = image;
        VkMemoryDedicatedRequirements dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS};
        VkMemoryRequirements2 requirements{VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2, &dedicated};
        VkImageMemoryRequirementsInfo2 query{VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2}; query.image = image;
        s.api.imports.image_requirements(s.device, &query, &requirements);
        result.allocate(requirements.memoryRequirements, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        check(s.api.imports.bind_image(s.device, image, result.memory_, 0), "gpu_bind_scratch_image_failed");
        return result;
    }
    Resource commands() const {
        auto &s = state(); s.require_idle(); Resource result(state_);
        VkCommandBufferAllocateInfo info{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        info.commandPool = s.pool; info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; info.commandBufferCount = 1;
        VkCommandBuffer command = VK_NULL_HANDLE;
        check(s.api.allocate_commands(s.device, &info, &command), "gpu_allocate_commands_failed");
        if (!command) throw Error("gpu_null_command");
        result.command_ = command;
        VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; VkFence fence = VK_NULL_HANDLE;
        check(s.api.create_fence(s.device, &fence_info, nullptr, &fence), "gpu_create_fence_failed");
        if (!fence) throw Error("gpu_null_fence");
        result.fence_ = fence;
        return result;
    }
};

#ifndef VK_NO_PROTOTYPES
inline Api system_api() {
    Api api;
    api.imports = {vkGetPhysicalDeviceFormatProperties2, vkGetPhysicalDeviceImageFormatProperties2, nullptr,
        vkCreateImage, vkDestroyImage, vkGetImageMemoryRequirements2, vkAllocateMemory, vkFreeMemory, vkBindImageMemory};
    api.instance_version = vkEnumerateInstanceVersion; api.create_instance = vkCreateInstance; api.destroy_instance = vkDestroyInstance;
    api.devices = vkEnumeratePhysicalDevices; api.properties = vkGetPhysicalDeviceProperties;
    api.extensions = vkEnumerateDeviceExtensionProperties; api.features = vkGetPhysicalDeviceFeatures2;
    api.memory = vkGetPhysicalDeviceMemoryProperties; api.families = vkGetPhysicalDeviceQueueFamilyProperties;
    api.create_device = vkCreateDevice; api.destroy_device = vkDestroyDevice; api.queue = vkGetDeviceQueue; api.device_proc = vkGetDeviceProcAddr;
    api.create_pool = vkCreateCommandPool; api.destroy_pool = vkDestroyCommandPool;
    api.create_buffer = vkCreateBuffer; api.destroy_buffer = vkDestroyBuffer; api.buffer_requirements = vkGetBufferMemoryRequirements2;
    api.bind_buffer = vkBindBufferMemory; api.map = vkMapMemory; api.unmap = vkUnmapMemory; api.invalidate = vkInvalidateMappedMemoryRanges;
    api.allocate_commands = vkAllocateCommandBuffers; api.free_commands = vkFreeCommandBuffers;
    api.create_fence = vkCreateFence; api.destroy_fence = vkDestroyFence;
    return api;
}
#endif
} // namespace sightcircuit::gpu
