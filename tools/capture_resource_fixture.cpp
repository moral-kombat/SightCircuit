// Compile the actual adapted worker against fake Vulkan entry points. No loader.
#define main framejacker_inspection_main
#include "ptgrab.cpp"
#undef main
#include <map>
#include <iostream>

namespace fake {
struct Resource { std::string kind; uintptr_t dependency = 0; bool mapped = false; };
std::map<uintptr_t, Resource> live;
unsigned acquisitions = 0, fail_at = 0;
std::string scenario;
uintptr_t next = 100;
bool invalid = false;
std::vector<std::string> destroyed;
void require(bool ok) { if (!ok) throw std::runtime_error("fixture_assertion_failed"); }
template <typename T> uintptr_t id(T handle) { return reinterpret_cast<uintptr_t>(handle); }
template <typename T> VkResult acquire(const char *kind, T *out) {
    if (++acquisitions == fail_at) { *out = reinterpret_cast<T>(99999); return VK_ERROR_OUT_OF_HOST_MEMORY; }
    *out = reinterpret_cast<T>(++next); live.emplace(next, Resource{kind}); return VK_SUCCESS;
}
template <typename T> void release(T value, const char *kind) {
    auto item = live.find(id(value));
    if (item == live.end() || item->second.kind != kind || item->second.mapped) { invalid = true; return; }
    for (const auto &other : live) if (other.second.dependency == id(value)) invalid = true;
    if (std::string(kind) == "device" && live.size() != 2) invalid = true;
    if (std::string(kind) == "instance" && live.size() != 1) invalid = true;
    destroyed.emplace_back(kind); live.erase(item);
}
}
extern "C" {
VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(const VkInstanceCreateInfo *, const VkAllocationCallbacks *, VkInstance *out) { return fake::acquire("instance", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyInstance(VkInstance value, const VkAllocationCallbacks *) { fake::release(value, "instance"); }
VKAPI_ATTR VkResult VKAPI_CALL vkEnumeratePhysicalDevices(VkInstance, uint32_t *count, VkPhysicalDevice *out) { *count = fake::scenario == "many_devices" ? 2 : 1; if (out) *out = reinterpret_cast<VkPhysicalDevice>(1); return fake::scenario == "enumeration_error" ? VK_ERROR_INITIALIZATION_FAILED : VK_SUCCESS; }
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceMemoryProperties(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties *out) {
    *out = {}; out->memoryTypeCount = fake::scenario == "bad_memory" ? 33 : 1; out->memoryHeapCount = 1;
    out->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
    out->memoryHeaps[0].size = 1ULL << 30;
}
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties(VkPhysicalDevice, uint32_t *count, VkQueueFamilyProperties *out) { *count = 1; if (out) { *out = {}; out->queueCount = 1; out->queueFlags = fake::scenario == "compute_only" ? VK_QUEUE_COMPUTE_BIT : VK_QUEUE_GRAPHICS_BIT; } }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(VkPhysicalDevice, const VkDeviceCreateInfo *, const VkAllocationCallbacks *, VkDevice *out) { return fake::acquire("device", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyDevice(VkDevice value, const VkAllocationCallbacks *) { fake::release(value, "device"); }
VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue(VkDevice, uint32_t, uint32_t, VkQueue *out) { *out = reinterpret_cast<VkQueue>(2); }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateCommandPool(VkDevice, const VkCommandPoolCreateInfo *, const VkAllocationCallbacks *, VkCommandPool *out) { return fake::acquire("pool", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyCommandPool(VkDevice, VkCommandPool value, const VkAllocationCallbacks *) { fake::release(value, "pool"); }
static void fake_dispatch() {}
VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice, const char *) { return fake::scenario == "missing_dispatch" ? nullptr : fake_dispatch; }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateImage(VkDevice, const VkImageCreateInfo *, const VkAllocationCallbacks *, VkImage *out) { return fake::acquire("image", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyImage(VkDevice, VkImage value, const VkAllocationCallbacks *) { fake::release(value, "image"); }
VKAPI_ATTR void VKAPI_CALL vkGetImageMemoryRequirements(VkDevice, VkImage, VkMemoryRequirements *out) { *out = {8ULL << 20, 256, 1}; }
VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory(VkDevice, const VkMemoryAllocateInfo *, const VkAllocationCallbacks *, VkDeviceMemory *out) { return fake::acquire("memory", out); }
VKAPI_ATTR void VKAPI_CALL vkFreeMemory(VkDevice, VkDeviceMemory value, const VkAllocationCallbacks *) { fake::release(value, "memory"); }
VKAPI_ATTR VkResult VKAPI_CALL vkBindImageMemory(VkDevice, VkImage image, VkDeviceMemory memory, VkDeviceSize) { if (++fake::acquisitions == fake::fail_at) return VK_ERROR_OUT_OF_DEVICE_MEMORY; fake::live.at(fake::id(image)).dependency = fake::id(memory); return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer(VkDevice, const VkBufferCreateInfo *, const VkAllocationCallbacks *, VkBuffer *out) { return fake::acquire("buffer", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyBuffer(VkDevice, VkBuffer value, const VkAllocationCallbacks *) { fake::release(value, "buffer"); }
VKAPI_ATTR void VKAPI_CALL vkGetBufferMemoryRequirements(VkDevice, VkBuffer, VkMemoryRequirements *out) { *out = {8ULL << 20, 256, 1}; }
VKAPI_ATTR VkResult VKAPI_CALL vkBindBufferMemory(VkDevice, VkBuffer buffer, VkDeviceMemory memory, VkDeviceSize) { if (++fake::acquisitions == fake::fail_at) return VK_ERROR_OUT_OF_DEVICE_MEMORY; fake::live.at(fake::id(buffer)).dependency = fake::id(memory); return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkMapMemory(VkDevice, VkDeviceMemory memory, VkDeviceSize, VkDeviceSize, VkMemoryMapFlags, void **out) {
    if (++fake::acquisitions == fake::fail_at) { *out = reinterpret_cast<void *>(99999); return VK_ERROR_MEMORY_MAP_FAILED; }
    static char byte; *out = &byte; fake::live.at(fake::id(memory)).mapped = true; return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL vkUnmapMemory(VkDevice, VkDeviceMemory memory) { fake::live.at(fake::id(memory)).mapped = false; }
VKAPI_ATTR VkResult VKAPI_CALL vkAllocateCommandBuffers(VkDevice, const VkCommandBufferAllocateInfo *info, VkCommandBuffer *out) {
    auto result = fake::acquire("command", out);
    if (result == VK_SUCCESS) fake::live.at(fake::id(*out)).dependency = fake::id(info->commandPool);
    return result;
}
VKAPI_ATTR void VKAPI_CALL vkFreeCommandBuffers(VkDevice, VkCommandPool, uint32_t count, const VkCommandBuffer *values) { for (uint32_t i=0; i<count; ++i) fake::release(values[i], "command"); }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateFence(VkDevice, const VkFenceCreateInfo *, const VkAllocationCallbacks *, VkFence *out) { return fake::acquire("fence", out); }
VKAPI_ATTR void VKAPI_CALL vkDestroyFence(VkDevice, VkFence value, const VkAllocationCallbacks *) { fake::release(value, "fence"); }
VKAPI_ATTR VkResult VKAPI_CALL vkResetCommandBuffer(VkCommandBuffer, VkCommandBufferResetFlags) { return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkBeginCommandBuffer(VkCommandBuffer, const VkCommandBufferBeginInfo *) { return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkEndCommandBuffer(VkCommandBuffer) { return VK_SUCCESS; }
VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier(VkCommandBuffer, VkPipelineStageFlags, VkPipelineStageFlags, VkDependencyFlags, uint32_t, const VkMemoryBarrier *, uint32_t, const VkBufferMemoryBarrier *, uint32_t, const VkImageMemoryBarrier *) {}
VKAPI_ATTR void VKAPI_CALL vkCmdCopyImageToBuffer(VkCommandBuffer, VkImage, VkImageLayout, VkBuffer, uint32_t, const VkBufferImageCopy *) {}
VKAPI_ATTR VkResult VKAPI_CALL vkResetFences(VkDevice, uint32_t, const VkFence *) { return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit(VkQueue, uint32_t, const VkSubmitInfo *, VkFence) { return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences(VkDevice, uint32_t, const VkFence *, VkBool32, uint64_t) { return VK_SUCCESS; }
VKAPI_ATTR VkResult VKAPI_CALL vkInvalidateMappedMemoryRanges(VkDevice, uint32_t, const VkMappedMemoryRange *) { return VK_SUCCESS; }

}
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    try {
        const std::string mode(argv[1]);
        if (mode == "partial") {
            for (unsigned failure = 1; failure <= 18; ++failure) {
                fake::acquisitions = 0; fake::fail_at = failure;
                bool rejected = false;
                try { Gpu gpu; gpu.init(); Downscaler scaler; scaler.init(gpu, 2464, 640); }
                catch (const std::exception &) { rejected = true; }
                fake::require(rejected && fake::live.empty() && !fake::invalid);
            }
        } else if (mode == "probe") {
            for (unsigned failure = 0; failure <= 2; ++failure) {
                bool rejected = false;
                { Gpu gpu; gpu.init();
                  { auto host = hostBuffer(gpu, 1024);
                    fake::fail_at = failure ? fake::acquisitions + failure : 0;
                    std::vector<CamImage> images;
                    try { copyRegions(gpu, images, {}, host); }
                    catch (const std::exception &) { rejected = true; }
                    fake::require(rejected == (failure != 0));
                    if (!failure) {
                        const auto count = fake::acquisitions;
                        copyRegions(gpu, images, {}, host);
                        fake::require(fake::acquisitions == count);
                    }
                  }
                }
                fake::require(fake::live.empty() && !fake::invalid);
                fake::fail_at = 0;
            }
        } else if (mode == "bootstrap") {
            for (const auto &scenario : {"many_devices", "enumeration_error", "compute_only", "bad_memory", "missing_dispatch"}) {
                fake::scenario = scenario;
                bool rejected = false;
                try { Gpu gpu; gpu.init(); } catch (const std::exception &) { rejected = true; }
                fake::require(rejected && fake::live.empty() && !fake::invalid);
            }
        } else if (mode == "success" || mode == "pending") {
            { Gpu gpu; gpu.init();
              { Downscaler scaler; scaler.init(gpu, 2464, 640);
                if (mode == "pending") gpu.importCompletion.submitted(); }
              if (mode == "success") fake::require(gpu.close() && gpu.close());
              else fake::require(!gpu.close() && gpu.importCompletion.cleanup_deferred);
            }
            fake::require(!fake::invalid);
            if (mode == "success") fake::require(fake::live.empty());
            else { fake::require(fake::destroyed.empty() && !fake::live.empty()); fake::live.clear(); }
        } else if (mode == "move") {
            framejacker::capture_import::Completion completion;
            unsigned releases = 0;
            Owned<VkFence> owner(completion, [&](VkFence) { ++releases; });
            owner.acquire([](VkFence *out) { *out = reinterpret_cast<VkFence>(1); return VK_SUCCESS; });
            Owned<VkFence> moved(std::move(owner));
            fake::require(owner.close());
            fake::require(moved.close() && moved.close() && releases == 1);
        } else return 64;
        std::cout << "{\"status\":\"passed\",\"synthetic\":true,\"runtime_initialized\":false}\n";
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
