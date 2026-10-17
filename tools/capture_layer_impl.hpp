// Original Framejacker Vulkan dispatch/registry lifecycle. MIT licensed.
// Inspection adaptation; no commands, queues, camera controls or source switching.
#pragma once
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>
#include "capture_provider.hpp"
#include "capture_layer_gate.hpp"
#include <memory>
#include <unordered_map>

#define FJ_EXPORT extern "C" __attribute__((visibility("default")))
namespace framejacker::layer {
namespace p = provider;
template<class Handle> static void *key(Handle handle) { return handle ? *reinterpret_cast<void **>(handle) : nullptr; }
template<class Handle> static uint64_t handle_id(Handle handle) { return uint64_t(reinterpret_cast<uintptr_t>(handle)); }
struct Instance {
    VkInstance handle = VK_NULL_HANDLE;
    PFN_vkGetInstanceProcAddr gipa = nullptr;
    PFN_vkDestroyInstance destroy = nullptr;
};
struct Device {
    uint64_t token = 0;
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    PFN_vkDestroyDevice DestroyDevice = nullptr;
    PFN_vkCreateImage CreateImage = nullptr;
    PFN_vkDestroyImage DestroyImage = nullptr;
    PFN_vkAllocateMemory AllocateMemory = nullptr;
    PFN_vkFreeMemory FreeMemory = nullptr;
    PFN_vkBindImageMemory BindImageMemory = nullptr;
    PFN_vkBindImageMemory2 BindImageMemory2 = nullptr;
};
static std::mutex maps_mutex, lifecycle_mutex;
static std::unordered_map<void *, std::shared_ptr<Instance>> instances;
static std::unordered_map<VkDevice, std::shared_ptr<Device>> devices;
static p::Registry &registry() { static p::Registry value; return value; }
static p::Server &server() { static p::Server value(registry()); return value; }
static std::shared_ptr<Device> device(VkDevice handle) {
    std::lock_guard<std::mutex> lock(maps_mutex);
    auto found = devices.find(handle); return found == devices.end() ? nullptr : found->second;
}
static std::shared_ptr<Instance> instance(void *dispatch) {
    std::lock_guard<std::mutex> lock(maps_mutex);
    auto found = instances.find(dispatch); return found == instances.end() ? nullptr : found->second;
}
template<class F> static void record(F call) noexcept {
    try { call(); } catch (...) { try { registry().invalidate(); } catch (...) {} }
}
static p::Packet describe(const VkImageCreateInfo &info) {
    p::Packet out;
    out.image_flags = info.flags; out.sharing_mode = info.sharingMode; out.image_type = info.imageType;
    out.depth = info.extent.depth; out.mip_levels = info.mipLevels; out.samples = info.samples;
    out.metadata_complete = 1;
    out.buffer.format = info.format; out.buffer.width = info.extent.width; out.buffer.height = info.extent.height;
    out.buffer.arrayLayers = info.arrayLayers; out.buffer.tiling = info.tiling; out.buffer.usage = info.usage;
    for (auto next = static_cast<const VkBaseInStructure *>(info.pNext); next; next = next->pNext) {
        if (next->sType == VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO) {
            out.external_types = reinterpret_cast<const VkExternalMemoryImageCreateInfo *>(next)->handleTypes;
        } else if (next->sType == VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT) {
            const auto &modifier = *reinterpret_cast<const VkImageDrmFormatModifierExplicitCreateInfoEXT *>(next);
            out.buffer.hasModifier = 1; out.buffer.modifier = modifier.drmFormatModifier;
            out.buffer.planeCount = modifier.drmFormatModifierPlaneCount;
            if (!modifier.pPlaneLayouts || modifier.drmFormatModifierPlaneCount > 4) { out.metadata_complete = 0; continue; }
            for (unsigned i = 0; i < modifier.drmFormatModifierPlaneCount; ++i) {
                out.buffer.planeOffset[i] = modifier.pPlaneLayouts[i].offset;
                out.buffer.planeRowPitch[i] = modifier.pPlaneLayouts[i].rowPitch;
                out.plane_array_pitch[i] = modifier.pPlaneLayouts[i].arrayPitch;
                out.plane_depth_pitch[i] = modifier.pPlaneLayouts[i].depthPitch;
            }
        } else out.metadata_complete = 0;
    }
    return out;
}
static VKAPI_ATTR VkResult VKAPI_CALL CreateImage(VkDevice handle, const VkImageCreateInfo *info,
                                                  const VkAllocationCallbacks *allocator, VkImage *out) {
    auto current = device(handle); if (!current) return VK_ERROR_DEVICE_LOST;
    const auto result = current->CreateImage(handle, info, allocator, out);
    if (result == VK_SUCCESS && current->token) record([&] {
        auto description = describe(*info);
        if (description.external_types) registry().image(current->token, handle_id(*out), description);
    });
    return result;
}
static VKAPI_ATTR void VKAPI_CALL DestroyImage(VkDevice handle, VkImage image, const VkAllocationCallbacks *allocator) {
    auto current = device(handle); if (!current) return;
    if (current->token) record([&] { registry().destroy_image(current->token, handle_id(image)); });
    current->DestroyImage(handle, image, allocator);
}
static VKAPI_ATTR VkResult VKAPI_CALL AllocateMemory(VkDevice handle, const VkMemoryAllocateInfo *info,
                                                     const VkAllocationCallbacks *allocator, VkDeviceMemory *out) {
    auto current = device(handle); if (!current) return VK_ERROR_DEVICE_LOST;
    const VkImportMemoryFdInfoKHR *import = nullptr;
    uint64_t dedicated = 0; uint32_t flags = 0, mask = 0; bool complete = true;
    for (auto next = static_cast<const VkBaseInStructure *>(info->pNext); next; next = next->pNext) {
        if (next->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR) import = reinterpret_cast<const VkImportMemoryFdInfoKHR *>(next);
        else if (next->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO)
            dedicated = handle_id(reinterpret_cast<const VkMemoryDedicatedAllocateInfo *>(next)->image);
        else if (next->sType == VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO) {
            const auto &value = *reinterpret_cast<const VkMemoryAllocateFlagsInfo *>(next); flags = value.flags; mask = value.deviceMask;
        } else complete = false;
    }
    transport::Fd owned;
    // Keep registry failures from changing the compositor's Vulkan result.
    if (current->token && import && import->fd >= 0) record([&] { owned = transport::duplicate(import->fd); });
    const auto result = current->AllocateMemory(handle, info, allocator, out);
    if (result == VK_SUCCESS && owned.get() >= 0) record([&] {
        registry().memory(current->token, handle_id(*out), std::move(owned), info->allocationSize,
                          import->handleType, dedicated, flags, mask, complete);
    });
    return result;
}
static VKAPI_ATTR void VKAPI_CALL FreeMemory(VkDevice handle, VkDeviceMemory memory, const VkAllocationCallbacks *allocator) {
    auto current = device(handle); if (!current) return;
    if (current->token) record([&] { registry().free_memory(current->token, handle_id(memory)); });
    current->FreeMemory(handle, memory, allocator);
}
static VKAPI_ATTR VkResult VKAPI_CALL BindImageMemory(VkDevice handle, VkImage image, VkDeviceMemory memory, VkDeviceSize offset) {
    auto current = device(handle); if (!current) return VK_ERROR_DEVICE_LOST;
    auto result = current->BindImageMemory(handle, image, memory, offset);
    if (result == VK_SUCCESS && current->token) record([&] { registry().bind(current->token, handle_id(memory), handle_id(image), offset); });
    return result;
}
static VKAPI_ATTR VkResult VKAPI_CALL BindImageMemory2(VkDevice handle, uint32_t count, const VkBindImageMemoryInfo *bindings) {
    auto current = device(handle); if (!current || !current->BindImageMemory2) return VK_ERROR_DEVICE_LOST;
    auto result = current->BindImageMemory2(handle, count, bindings);
    if (result == VK_SUCCESS && current->token) record([&] {
        for (uint32_t i = 0; i < count; ++i) {
            if (bindings[i].pNext) { registry().invalidate(); break; }
            registry().bind(current->token, handle_id(bindings[i].memory), handle_id(bindings[i].image), bindings[i].memoryOffset);
        }
    });
    return result;
}
static VKAPI_ATTR VkResult VKAPI_CALL CreateInstance(const VkInstanceCreateInfo *info, const VkAllocationCallbacks *allocator, VkInstance *out) {
    auto chain = const_cast<VkLayerInstanceCreateInfo *>(static_cast<const VkLayerInstanceCreateInfo *>(info->pNext));
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && chain->function == VK_LAYER_LINK_INFO))
        chain = const_cast<VkLayerInstanceCreateInfo *>(static_cast<const VkLayerInstanceCreateInfo *>(chain->pNext));
    if (!chain || !chain->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
    const auto gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    const auto create = reinterpret_cast<PFN_vkCreateInstance>(gipa(VK_NULL_HANDLE, "vkCreateInstance"));
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    std::shared_ptr<Instance> current;
    try { current = std::make_shared<Instance>(); } catch (...) { return VK_ERROR_OUT_OF_HOST_MEMORY; }
    auto result = create(info, allocator, out); if (result != VK_SUCCESS) return result;
    current->handle = *out; current->gipa = gipa;
    current->destroy = reinterpret_cast<PFN_vkDestroyInstance>(gipa(*out, "vkDestroyInstance"));
    try { std::lock_guard<std::mutex> lock(maps_mutex); instances.emplace(key(*out), current); }
    catch (...) { current->destroy(*out, allocator); return VK_ERROR_OUT_OF_HOST_MEMORY; }
    return result;
}
static VKAPI_ATTR void VKAPI_CALL DestroyInstance(VkInstance handle, const VkAllocationCallbacks *allocator) {
    std::shared_ptr<Instance> current;
    { std::lock_guard<std::mutex> lock(maps_mutex); auto found = instances.find(key(handle));
      if (found == instances.end()) return;
      current = found->second; instances.erase(found); }
    current->destroy(handle, allocator);
}
static VKAPI_ATTR VkResult VKAPI_CALL CreateDevice(VkPhysicalDevice physical, const VkDeviceCreateInfo *info,
                                                   const VkAllocationCallbacks *allocator, VkDevice *out) {
    auto chain = const_cast<VkLayerDeviceCreateInfo *>(static_cast<const VkLayerDeviceCreateInfo *>(info->pNext));
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && chain->function == VK_LAYER_LINK_INFO))
        chain = const_cast<VkLayerDeviceCreateInfo *>(static_cast<const VkLayerDeviceCreateInfo *>(chain->pNext));
    if (!chain || !chain->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
    auto gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    auto gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    auto parent = instance(key(physical));
    auto parent_handle = parent ? parent->handle : VK_NULL_HANDLE;
    auto create = reinterpret_cast<PFN_vkCreateDevice>(gipa(parent_handle, "vkCreateDevice"));
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    std::shared_ptr<Device> current;
    try { current = std::make_shared<Device>(); } catch (...) { return VK_ERROR_OUT_OF_HOST_MEMORY; }
    const auto result = create(physical, info, allocator, out); if (result != VK_SUCCESS) return result;
    current->gdpa = gdpa;
#define FJ_FUNCTION(name) current->name = reinterpret_cast<PFN_vk##name>(gdpa(*out, "vk" #name));
    FJ_FUNCTION(DestroyDevice) FJ_FUNCTION(CreateImage) FJ_FUNCTION(DestroyImage) FJ_FUNCTION(AllocateMemory)
    FJ_FUNCTION(FreeMemory) FJ_FUNCTION(BindImageMemory) FJ_FUNCTION(BindImageMemory2)
#undef FJ_FUNCTION
    if (!current->BindImageMemory2) current->BindImageMemory2 = reinterpret_cast<PFN_vkBindImageMemory2>(gdpa(*out, "vkBindImageMemory2KHR"));
    std::lock_guard<std::mutex> lifecycle(lifecycle_mutex);
    try {
        std::lock_guard<std::mutex> lock(maps_mutex); devices.emplace(*out, current);
    } catch (...) { current->DestroyDevice(*out, allocator); return VK_ERROR_OUT_OF_HOST_MEMORY; }
    record([&] {
        const auto properties = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties2>(gipa(parent_handle, "vkGetPhysicalDeviceProperties2"));
        if (!properties) throw transport::Error("provider_device_uuid_unavailable");
        VkPhysicalDeviceIDProperties ids{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
        VkPhysicalDeviceProperties2 props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &ids};
        properties(physical, &props);
        current->token = registry().add_device(ids.deviceUUID, ids.driverUUID);
        const char *runtime = std::getenv("XDG_RUNTIME_DIR");
        if (!runtime) throw transport::Error("provider_runtime_missing");
        try { server().start(runtime); } catch (...) { server().stop(); throw; }
    });
    return result;
}
static VKAPI_ATTR void VKAPI_CALL DestroyDevice(VkDevice handle, const VkAllocationCallbacks *allocator) {
    std::shared_ptr<Device> current;
    { std::lock_guard<std::mutex> lifecycle(lifecycle_mutex);
      bool last;
      { std::lock_guard<std::mutex> lock(maps_mutex); auto found = devices.find(handle);
        if (found == devices.end()) return;
        current = found->second; devices.erase(found); last = devices.empty(); }
      if (current->token) record([&] { registry().remove_device(current->token); });
      if (last) record([&] { if (!server().stop()) registry().invalidate(); }); }
    current->DestroyDevice(handle, allocator);
}
static PFN_vkVoidFunction hook(const char *name) {
#define FJ_HOOK(function) if (!std::strcmp(name, "vk" #function)) return reinterpret_cast<PFN_vkVoidFunction>(function);
    FJ_HOOK(DestroyDevice) FJ_HOOK(CreateImage) FJ_HOOK(DestroyImage) FJ_HOOK(AllocateMemory)
    FJ_HOOK(FreeMemory) FJ_HOOK(BindImageMemory) FJ_HOOK(BindImageMemory2)
#undef FJ_HOOK
    if (!std::strcmp(name, "vkBindImageMemory2KHR")) return reinterpret_cast<PFN_vkVoidFunction>(BindImageMemory2);
    return nullptr;
}
} // namespace framejacker::layer
FJ_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL mrhand_GetDeviceProcAddr(VkDevice handle, const char *name);
FJ_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL mrhand_GetInstanceProcAddr(VkInstance handle, const char *name) {
    using namespace framejacker::layer;
    if (!name) return nullptr;
    if (!std::strcmp(name, "vkGetInstanceProcAddr")) return reinterpret_cast<PFN_vkVoidFunction>(mrhand_GetInstanceProcAddr);
    if (!std::strcmp(name, "vkGetDeviceProcAddr")) return reinterpret_cast<PFN_vkVoidFunction>(mrhand_GetDeviceProcAddr);
    if (!std::strcmp(name, "vkCreateInstance")) return reinterpret_cast<PFN_vkVoidFunction>(CreateInstance);
    if (!std::strcmp(name, "vkCreateDevice")) return reinterpret_cast<PFN_vkVoidFunction>(CreateDevice);
    auto current = instance(key(handle)); if (!current) return nullptr;
    const auto next = current->gipa(handle, name); if (!next) return nullptr;
    if (!std::strcmp(name, "vkDestroyInstance")) return reinterpret_cast<PFN_vkVoidFunction>(DestroyInstance);
    if (auto wrapped = hook(name)) return wrapped;
    return next;
}
FJ_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL mrhand_GetDeviceProcAddr(VkDevice handle, const char *name) {
    using namespace framejacker::layer;
    if (!name) return nullptr;
    if (!std::strcmp(name, "vkGetDeviceProcAddr")) return reinterpret_cast<PFN_vkVoidFunction>(mrhand_GetDeviceProcAddr);
    auto current = device(handle); if (!current) return nullptr;
    const auto next = current->gdpa(handle, name); if (!next) return nullptr;
    if (auto wrapped = hook(name)) return wrapped;
    return next;
}
FJ_EXPORT VKAPI_ATTR VkResult VKAPI_CALL mrhand_NegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *info) {
    if (!info || !framejacker::layer_gate::allowed() || info->loaderLayerInterfaceVersion < 2) return VK_ERROR_INITIALIZATION_FAILED;
    info->loaderLayerInterfaceVersion = 2;
    info->pfnGetInstanceProcAddr = mrhand_GetInstanceProcAddr;
    info->pfnGetDeviceProcAddr = mrhand_GetDeviceProcAddr;
    info->pfnGetPhysicalDeviceProcAddr = nullptr;
    return VK_SUCCESS;
}
#undef FJ_EXPORT
