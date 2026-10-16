// Exercise actual adapted callbacks with fake downstream dispatch, no loader.
#include "handcam_layer.cpp"
#include <iostream>
#include <filesystem>
#include <future>

static std::atomic<bool> entered{false}, release_call{false}, destroyed{false};
static bool delay_create = false;
static unsigned instance_destroyed = 0;
static VKAPI_ATTR void VKAPI_CALL fakeDestroyInstance(VkInstance, const VkAllocationCallbacks *) { ++instance_destroyed; }
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL fakeGipa(VkInstance, const char *) { return nullptr; }
static void require(bool ok) { if (!ok) throw std::runtime_error("fixture_assertion_failed"); }
static VKAPI_ATTR VkResult VKAPI_CALL fakeCreate(VkDevice, const VkImageCreateInfo *, const VkAllocationCallbacks *, VkImage *out) {
    if (delay_create) { entered = true; while (!release_call) std::this_thread::yield(); }
    *out = reinterpret_cast<VkImage>(101); return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL fakeDestroyImage(VkDevice, VkImage, const VkAllocationCallbacks *) {}
static VKAPI_ATTR VkResult VKAPI_CALL fakeAllocate(VkDevice, const VkMemoryAllocateInfo *, const VkAllocationCallbacks *, VkDeviceMemory *out) {
    *out = reinterpret_cast<VkDeviceMemory>(102); return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL fakeFree(VkDevice, VkDeviceMemory, const VkAllocationCallbacks *) {}
static VKAPI_ATTR VkResult VKAPI_CALL fakeBind(VkDevice, VkImage, VkDeviceMemory, VkDeviceSize) { return VK_SUCCESS; }
static VKAPI_ATTR void VKAPI_CALL fakeDestroyDevice(VkDevice, const VkAllocationCallbacks *) { destroyed = true; }
static void attach(VkDevice device) {
    auto data = std::make_shared<DeviceData>(); data->device = device;
    data->CreateImage = fakeCreate; data->DestroyImage = fakeDestroyImage;
    data->AllocateMemory = fakeAllocate; data->FreeMemory = fakeFree;
    data->BindImageMemory = fakeBind; data->DestroyDevice = fakeDestroyDevice;
    g_dev[key(device)] = data;
}
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    char temporary[] = "/tmp/framejacker-layer-XXXXXX";
    if (!mkdtemp(temporary)) return 1;
    setenv("XDG_RUNTIME_DIR", temporary, 1);
    struct Cleanup { std::string path; ~Cleanup() { g_server.stop(); std::filesystem::remove_all(path); } } cleanup{temporary};
    try {
        int dispatch_a, dispatch_b;
        void *handle_a = &dispatch_a, *handle_b = &dispatch_b;
        VkDevice a = reinterpret_cast<VkDevice>(&handle_a), b = reinterpret_cast<VkDevice>(&handle_b);
        attach(a); attach(b);
        VkExternalMemoryImageCreateInfo external{VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO};
        external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkImageCreateInfo create{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, &external};
        create.extent = {2464,2464,1}; create.arrayLayers = 1;
        VkImage image;
        const std::string mode(argv[1]);
        if (mode == "registry") {
            require(CreateImage(a, &create, nullptr, &image) == VK_SUCCESS);
            require(CreateImage(b, &create, nullptr, &image) == VK_SUCCESS);
            framejacker::transport::Fd input(open((std::string(temporary)+"/input").c_str(), O_CREAT|O_RDWR|O_CLOEXEC,0600));
            require(input.get() >= 0 && lseek(input.get(), 7, SEEK_SET) == 7);
            VkImportMemoryFdInfoKHR imported{VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR};
            imported.fd = input.get(); imported.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
            VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &imported}; allocation.allocationSize = 4096;
            VkDeviceMemory memory;
            require(AllocateMemory(a, &allocation, nullptr, &memory) == VK_SUCCESS);
            require(AllocateMemory(b, &allocation, nullptr, &memory) == VK_SUCCESS);
            require(lseek(input.get(), 0, SEEK_CUR) == 7); // importer must not seek shared FD
            require(BindImageMemory(a, image, memory, 0) == VK_SUCCESS);
            require(BindImageMemory(b, image, memory, 0) == VK_SUCCESS);
            require(g_images.size()==2 && g_mems.size()==2);
            const int owned_a = g_mems.at(objectKey(a,memory)).fd;
            const int owned_b = g_mems.at(objectKey(b,memory)).fd;
            auto generation = g_generation.load(); DestroyImage(a, image, nullptr);
            require(g_generation != generation && !g_mems.at(objectKey(a,memory)).image);
            DestroyDevice(a, nullptr);
            require(g_mems.size()==1 && g_images.size()==1 && fcntl(owned_a,F_GETFD)==-1 && fcntl(owned_b,F_GETFD)>=0);
            FreeMemory(b,memory,nullptr); require(g_mems.empty() && fcntl(owned_b,F_GETFD)==-1);
            DestroyDevice(b,nullptr); require(g_images.empty() && g_dev.empty());
        } else if (mode == "instance") {
            VkInstance instance = reinterpret_cast<VkInstance>(&handle_a);
            auto data = std::make_shared<InstanceData>();
            data->DestroyInstance = fakeDestroyInstance; data->gipa = fakeGipa;
            g_inst[key(instance)] = data;
            require(mrhand_GetInstanceProcAddr(instance, "unknown") == nullptr);
            DestroyInstance(instance, nullptr);
            require(instance_destroyed == 1 && data->retiring && g_inst.empty());
            require(mrhand_GetInstanceProcAddr(instance, "unknown") == nullptr);
            DestroyInstance(instance, nullptr); require(instance_destroyed == 1);
            DestroyDevice(a,nullptr); DestroyDevice(b,nullptr);
        } else if (mode == "retirement") {
            delay_create = true;
            auto callback = std::async(std::launch::async, [&] { VkImage out; return CreateImage(a,&create,nullptr,&out); });
            auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(1);
            while (!entered && std::chrono::steady_clock::now()<deadline) std::this_thread::yield();
            if (!entered) release_call = true;
            require(entered);
            auto retire = std::async(std::launch::async, [&] { DestroyDevice(a,nullptr); });
            const bool waited = retire.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout;
            release_call = true;
            require(callback.get()==VK_SUCCESS); retire.get();
            require(waited && destroyed && g_images.empty());
            require(CreateImage(a,&create,nullptr,&image)==VK_ERROR_DEVICE_LOST);
            DestroyDevice(b,nullptr);
        } else return 64;
        g_server.stop();
        std::cout << "{\"status\":\"passed\",\"synthetic\":true,\"runtime_initialized\":false}\n";
        return 0;
    } catch (const std::exception &error) { release_call = true; std::cerr<<error.what()<<'\n'; return 1; }
}
