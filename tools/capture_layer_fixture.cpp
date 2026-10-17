// Actual layer hooks against fake downstream dispatch. No loader or camera.
#include "capture_layer_impl.hpp"
#include <filesystem>
#include <iostream>
#include <sys/mman.h>
namespace l = framejacker::layer;
namespace p = framejacker::provider;
namespace t = framejacker::transport;
struct Dispatchable { void *dispatch; };
static int table, device_table;
static Dispatchable instance_handle{&table}, physical_handle{&table}, device_handles[2]{{&device_table}, {&device_table}};
static unsigned created = 0, destroyed = 0, images = 0, allocations = 0;
static bool fail_image = false, fail_memory = false;
static void require(bool value) { if (!value) throw t::Error("layer_fixture_assertion"); }
static VKAPI_ATTR VkResult VKAPI_CALL create_instance(const VkInstanceCreateInfo *, const VkAllocationCallbacks *, VkInstance *out) {
    *out = reinterpret_cast<VkInstance>(&instance_handle); return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL destroy_instance(VkInstance, const VkAllocationCallbacks *) { require(created == destroyed); }
static VKAPI_ATTR VkResult VKAPI_CALL create_device(VkPhysicalDevice, const VkDeviceCreateInfo *, const VkAllocationCallbacks *, VkDevice *out) {
    require(created < 2); *out = reinterpret_cast<VkDevice>(&device_handles[created++]); return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL destroy_device(VkDevice, const VkAllocationCallbacks *) { ++destroyed; }
static VKAPI_ATTR void VKAPI_CALL properties(VkPhysicalDevice, VkPhysicalDeviceProperties2 *out) {
    auto ids = static_cast<VkPhysicalDeviceIDProperties *>(out->pNext); ids->deviceUUID[0] = 1; ids->driverUUID[0] = 2;
}
static VKAPI_ATTR VkResult VKAPI_CALL create_image(VkDevice, const VkImageCreateInfo *, const VkAllocationCallbacks *, VkImage *out) {
    *out = reinterpret_cast<VkImage>(10); if (fail_image) return VK_ERROR_OUT_OF_DEVICE_MEMORY; ++images; return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL destroy_image(VkDevice, VkImage, const VkAllocationCallbacks *) { --images; }
static VKAPI_ATTR VkResult VKAPI_CALL allocate(VkDevice, const VkMemoryAllocateInfo *info, const VkAllocationCallbacks *, VkDeviceMemory *out) {
    *out = reinterpret_cast<VkDeviceMemory>(20); if (fail_memory) return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    for (auto next = static_cast<const VkBaseInStructure *>(info->pNext); next; next = next->pNext)
        if (next->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR)
            close(reinterpret_cast<const VkImportMemoryFdInfoKHR *>(next)->fd);
    ++allocations; return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL free_memory(VkDevice, VkDeviceMemory, const VkAllocationCallbacks *) { --allocations; }
static VKAPI_ATTR VkResult VKAPI_CALL bind_memory(VkDevice, VkImage, VkDeviceMemory, VkDeviceSize) { return VK_SUCCESS; }
static VKAPI_ATTR VkResult VKAPI_CALL bind_memory2(VkDevice, uint32_t, const VkBindImageMemoryInfo *) { return VK_SUCCESS; }
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gdpa(VkDevice, const char *name) {
#define FJ_FAKE(n, f) if (!std::strcmp(name, n)) return reinterpret_cast<PFN_vkVoidFunction>(f);
    FJ_FAKE("vkDestroyDevice", destroy_device) FJ_FAKE("vkCreateImage", create_image)
    FJ_FAKE("vkDestroyImage", destroy_image) FJ_FAKE("vkAllocateMemory", allocate) FJ_FAKE("vkFreeMemory", free_memory)
    FJ_FAKE("vkBindImageMemory", bind_memory) FJ_FAKE("vkBindImageMemory2", bind_memory2)
#undef FJ_FAKE
    return nullptr;
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gipa(VkInstance, const char *name) {
    if (!std::strcmp(name, "vkCreateInstance")) return reinterpret_cast<PFN_vkVoidFunction>(create_instance);
    if (!std::strcmp(name, "vkDestroyInstance")) return reinterpret_cast<PFN_vkVoidFunction>(destroy_instance);
    if (!std::strcmp(name, "vkCreateDevice")) return reinterpret_cast<PFN_vkVoidFunction>(create_device);
    if (!std::strcmp(name, "vkGetPhysicalDeviceProperties2")) return reinterpret_cast<PFN_vkVoidFunction>(properties);
    return gdpa(VK_NULL_HANDLE, name);
}
static VkInstance start_instance() {
    VkLayerInstanceLink link{}; link.pfnNextGetInstanceProcAddr = gipa;
    VkLayerInstanceCreateInfo chain{}; chain.sType = VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO;
    chain.function = VK_LAYER_LINK_INFO; chain.u.pLayerInfo = &link;
    VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, &chain}; VkInstance handle{};
    require(l::CreateInstance(&info, nullptr, &handle) == VK_SUCCESS); return handle;
}
static VkDevice start_device() {
    VkLayerDeviceLink link{}; link.pfnNextGetInstanceProcAddr = gipa; link.pfnNextGetDeviceProcAddr = gdpa;
    VkLayerDeviceCreateInfo chain{}; chain.sType = VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO;
    chain.function = VK_LAYER_LINK_INFO; chain.u.pLayerInfo = &link;
    VkDeviceCreateInfo info{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &chain}; VkDevice handle{};
    require(l::CreateDevice(reinterpret_cast<VkPhysicalDevice>(&physical_handle), &info, nullptr, &handle) == VK_SUCCESS);
    return handle;
}
static void import(VkDevice device) {
    VkExternalMemoryImageCreateInfo external{VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO};
    external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkSubresourceLayout layouts[2]{}; layouts[0].rowPitch = layouts[1].rowPitch = 2560; layouts[1].offset = 8 << 20;
    VkImageDrmFormatModifierExplicitCreateInfoEXT modifier{VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT, &external};
    modifier.drmFormatModifier = 0x0500000000000001; modifier.drmFormatModifierPlaneCount = 2; modifier.pPlaneLayouts = layouts;
    VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, &modifier};
    info.imageType = VK_IMAGE_TYPE_2D; info.format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM;
    info.extent = {2464, 2464, 1}; info.arrayLayers = info.mipLevels = 1; info.samples = VK_SAMPLE_COUNT_1_BIT;
    info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT; info.usage = 5;
    VkImage image{}; require(l::CreateImage(device, &info, nullptr, &image) == VK_SUCCESS);
    t::Fd memory(memfd_create("layer-generated", MFD_CLOEXEC)); require(memory.get() >= 0 && ftruncate(memory.get(), 16 << 20) == 0);
    VkImportMemoryFdInfoKHR fd{VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR};
    fd.fd = memory.get(); fd.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &fd}; allocation.allocationSize = 16 << 20;
    VkDeviceMemory out{}; require(l::AllocateMemory(device, &allocation, nullptr, &out) == VK_SUCCESS); memory.release();
    require(l::BindImageMemory(device, image, out, 0) == VK_SUCCESS);
}
static void lifecycle(const std::string &runtime) {
    const auto inst = start_instance(); const auto first = start_device(); const auto second = start_device();
    // Deliberately identical dispatch-table pointers; handles still remain distinct.
    require(l::devices.size() == 2 && l::device(first) != l::device(second));
    import(first); import(second);
    p::Packet terminal; auto records = l::registry().snapshot(terminal);
    require(records.size() == 2 && records[0].info.device != records[1].info.device);
    for (const auto &record : records) p::validate_profile(record.info);
    p::Lease lease(runtime + "/framejacker-camera-provider/camera.sock"); lease.check();
    auto retained = l::device(first);
    l::DestroyDevice(first, nullptr); // Purges only this device, even without per-resource callbacks.
    require(!l::device(first) && retained.use_count() == 1 && l::devices.size() == 1);
    bool rejected = false; try { lease.check(); } catch (...) { rejected = true; } require(rejected);
    records = l::registry().snapshot(terminal); require(records.size() == 1);
    l::DestroyImage(second, reinterpret_cast<VkImage>(10), nullptr);
    records = l::registry().snapshot(terminal); require(records[0].info.buffer.hasImage == 0);
    l::FreeMemory(second, reinterpret_cast<VkDeviceMemory>(20), nullptr);
    l::DestroyDevice(second, nullptr); l::DestroyInstance(inst, nullptr);
    require(l::registry().empty() && l::devices.empty() && l::instances.empty());
    require(!std::filesystem::exists(runtime + "/framejacker-camera-provider/camera.sock"));
}
static void failures() {
    const auto instance = start_instance(); const auto device = start_device();
    VkImageCreateInfo image_info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO}; VkImage image{}; fail_image = true;
    require(l::CreateImage(device, &image_info, nullptr, &image) == VK_ERROR_OUT_OF_DEVICE_MEMORY);
    t::Fd memory(memfd_create("failure-generated", MFD_CLOEXEC));
    VkImportMemoryFdInfoKHR fd{VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR}; fd.fd = memory.get(); fd.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkMemoryAllocateInfo info{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &fd}; info.allocationSize = 16 << 20;
    VkDeviceMemory allocation{}; fail_memory = true;
    require(l::AllocateMemory(device, &info, nullptr, &allocation) == VK_ERROR_OUT_OF_DEVICE_MEMORY);
    require(fcntl(memory.get(), F_GETFD) >= 0); p::Packet terminal;
    require(l::registry().snapshot(terminal).empty());
    const auto count = l::devices.size();
    require(mrhand_GetDeviceProcAddr(reinterpret_cast<VkDevice>(1), "vkCreateImage") == nullptr && l::devices.size() == count);
    require(mrhand_GetDeviceProcAddr(device, "vkBindImageMemory2KHR") == nullptr);
    l::DestroyDevice(device, nullptr); l::DestroyInstance(instance, nullptr);
}
static void gating(const std::string &runtime) {
    VkNegotiateLayerInterface info{}; info.loaderLayerInterfaceVersion = 2;
    require(mrhand_NegotiateLoaderLayerInterfaceVersion(&info) == VK_ERROR_INITIALIZATION_FAILED);
    require(framejacker::layer_gate::claim(runtime)); require(framejacker::layer_gate::claim(runtime));
    require(!framejacker::layer_gate::claim(runtime));
    const auto path = runtime + "/framejacker-layer-guard/starts";
    unlink(path.c_str()); require(symlink("/dev/null", path.c_str()) == 0);
    require(!framejacker::layer_gate::claim(runtime)); require(std::filesystem::is_symlink(path));
}
int main(int argc, char **argv) {
    if (argc == 1) { std::cout << "{\"status\":\"not_run\",\"exit_code\":3}\n"; return 3; }
    if (argc != 3 || std::string(argv[1]) != "--case") return 64;
    char name[] = "/tmp/fj-layer-XXXXXX"; const char *runtime = mkdtemp(name); if (!runtime) return 1;
    setenv("XDG_RUNTIME_DIR", runtime, 1);
    int code = 0;
    try {
        std::string selected = argv[2];
        if (selected == "lifecycle") lifecycle(runtime);
        else if (selected == "failures") failures();
        else if (selected == "gating") gating(runtime);
        else code = 64;
        if (!code) std::cout << "{\"report_kind\":\"capture_layer_fixture\",\"schema_version\":1,\"status\":\"passed\",\"tests_run\":1,\"runtime_initialized\":false,\"capture_started\":false}\n";
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; code = 1; }
    std::filesystem::remove_all(runtime); return code;
}
