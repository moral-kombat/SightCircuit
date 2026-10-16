#include "capture_host.hpp"
#include <iostream>
#include <string>
#include <vector>
using namespace framejacker;
static std::string fault;
static std::vector<std::string> calls;
static char bytes[4096];
static bool small_heap = false, no_type = false, bad_requirement = false;
static void require(bool value) { if (!value) throw std::runtime_error("fixture_assertion_failed"); }
static VkResult step(const char *name) {
    calls.emplace_back(name);
    return fault == name ? VK_ERROR_OUT_OF_HOST_MEMORY : VK_SUCCESS;
}
static VKAPI_ATTR VkResult VKAPI_CALL create(VkDevice, const VkBufferCreateInfo *info,
        const VkAllocationCallbacks *, VkBuffer *out) {
    require(info->size == 1024 && info->usage == VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    auto result = step("create");
    *out = reinterpret_cast<VkBuffer>(result == VK_SUCCESS ? 1 : 999);
    return result;
}
static VKAPI_ATTR void VKAPI_CALL destroy(VkDevice, VkBuffer value, const VkAllocationCallbacks *) {
    require(value == reinterpret_cast<VkBuffer>(1)); calls.emplace_back("destroy");
}
static VKAPI_ATTR void VKAPI_CALL requirements(VkDevice, VkBuffer, VkMemoryRequirements *out) {
    *out = {bad_requirement ? 1u : 4096u, 256, uint32_t{1} << 31};
}
static VKAPI_ATTR VkResult VKAPI_CALL allocate(VkDevice, const VkMemoryAllocateInfo *info,
        const VkAllocationCallbacks *, VkDeviceMemory *out) {
    require(info->allocationSize == 4096 && info->memoryTypeIndex == 31);
    auto result = step("allocate");
    *out = reinterpret_cast<VkDeviceMemory>(result == VK_SUCCESS ? 2 : 999);
    return result;
}
static VKAPI_ATTR void VKAPI_CALL free_memory(VkDevice, VkDeviceMemory value, const VkAllocationCallbacks *) {
    require(value == reinterpret_cast<VkDeviceMemory>(2)); calls.emplace_back("free");
}
static VKAPI_ATTR VkResult VKAPI_CALL bind(VkDevice, VkBuffer, VkDeviceMemory, VkDeviceSize offset) {
    require(offset == 0); return step("bind");
}
static VKAPI_ATTR VkResult VKAPI_CALL map(VkDevice, VkDeviceMemory, VkDeviceSize, VkDeviceSize size,
        VkMemoryMapFlags, void **out) {
    require(size == VK_WHOLE_SIZE);
    *out = bytes; return step("map");
}
static VKAPI_ATTR void VKAPI_CALL unmap(VkDevice, VkDeviceMemory) { calls.emplace_back("unmap"); }
static capture_host::Context context(capture_import::Completion &completion) {
    capture_host::Context result;
    result.device = reinterpret_cast<VkDevice>(3); result.completion = &completion;
    result.memory.memoryTypeCount = 32; result.memory.memoryHeapCount = 1;
    result.memory.memoryHeaps[0].size = small_heap ? 1024 : 8192;
    result.memory.memoryTypes[31].propertyFlags = no_type ? VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT :
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
    result.api = {create, destroy, requirements, allocate, free_memory, bind, map, unmap};
    return result;
}
static void rejects(const capture_host::Context &ctx, VkDeviceSize size = 1024) {
    bool rejected = false;
    try { auto buffer = capture_host::create(ctx, size); }
    catch (const std::runtime_error &) { rejected = true; }
    require(rejected);
}
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    try {
        const std::string mode = argv[1];
        capture_import::Completion completion;
        if (mode == "success") {
            { auto buffer = capture_host::create(context(completion), 1024);
              require(buffer.data() == bytes);
              capture_host::Buffer moved(std::move(buffer));
              require(buffer.close());
              capture_host::Buffer assigned; assigned = std::move(moved);
              require(moved.close() && assigned.close() && assigned.close()); }
            require(calls == std::vector<std::string>({"create", "allocate", "bind", "map", "unmap", "destroy", "free"}));
        } else if (mode == "failures") {
            for (const auto &name : {"create", "allocate", "bind", "map"}) {
                fault = name; calls.clear(); rejects(context(completion));
                std::vector<std::string> expected;
                for (const auto &step_name : {"create", "allocate", "bind", "map"}) {
                    expected.emplace_back(step_name); if (fault == step_name) break;
                }
                if (fault != "create") expected.emplace_back("destroy");
                if (fault == "bind" || fault == "map") expected.emplace_back("free");
                require(calls == expected);
            }
        } else if (mode == "bounds") {
            auto ctx = context(completion);
            rejects(ctx, 0); rejects(ctx, capture_import::maximum_allocation + 1);
            ctx.memory.memoryTypeCount = 33; rejects(ctx);
            ctx = context(completion); ctx.api.map = nullptr; rejects(ctx);
            require(calls.empty());
            bad_requirement = true; rejects(context(completion));
            require(calls == std::vector<std::string>({"create", "destroy"}));
        } else if (mode == "memory") {
            for (int scenario = 0; scenario < 3; ++scenario) {
                calls.clear(); small_heap = scenario == 0; no_type = scenario == 1;
                auto ctx = context(completion);
                if (scenario == 2) ctx.memory.memoryTypes[0].heapIndex = 4;
                rejects(ctx);
                require(calls == std::vector<std::string>({"create", "destroy"}));
            }
        } else if (mode == "in_flight") {
            auto buffer = capture_host::create(context(completion), 1024);
            completion.submitted(); calls.clear();
            rejects(context(completion));
            require(!buffer.close() && completion.cleanup_deferred && calls.empty());
            require(buffer.close());
        } else if (mode == "move_assignment") {
            auto first = capture_host::create(context(completion), 1024);
            auto second = capture_host::create(context(completion), 1024);
            calls.clear(); first = std::move(second);
            require(calls == std::vector<std::string>({"unmap", "destroy", "free"}));
            require(second.close() && first.close());
            require(calls.size() == 6);
        } else return 64;
        std::cout << "{\"status\":\"passed\",\"synthetic\":true,\"runtime_initialized\":false}\n";
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
