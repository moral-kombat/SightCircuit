// A Vulkan "driver" that only writes plain text, for the camera helper's
// stderr capture (tests/test_camera_export.py). The loader loads it from a
// manifest named by VK_ICD_FILENAMES / VK_DRIVER_FILES while the helper creates
// its instance; it then writes what real drivers and implicit layers write
// straight to fd 2 (and one line to stdout), and refuses the interface version,
// so the loader skips it. Listed beside lavapipe, lavapipe still serves the
// device. CAMERA_CHATTY_ICD_BURST_BYTES, when set, adds that many more bytes,
// more than a pipe holds, to show that nothing blocks on the capture. No
// Vulkan header: the two entry points need only their C signatures. C++17.
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#include <unistd.h>

namespace {

void put(int fd, const std::string& text) {
    const char* data = text.data();
    std::size_t size = text.size();
    while (size) {
        const ssize_t count = ::write(fd, data, size);
        if (count <= 0) return;  // a full non-blocking pipe: the rest is lost, as with a real driver
        data += count;
        size -= static_cast<std::size_t>(count);
    }
}

} // namespace

extern "C" {

__attribute__((visibility("default"))) int32_t vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t* version) {
    (void)version;
    put(2, "ATTENTION: default value of option vk_wsi_force_swapchain_to_current_extent overridden by environment.\n");
    put(1, "[Gamescope WSI] Failed to connect to gamescope socket: gamescope-0. Bypass layer will be unavailable.\n");
    put(2, std::string("chatty-icd: bytes \xff\x01 end\n"));
    put(2, "chatty-icd: long " + std::string(3000, 'x') + "\n");
    for (int line = 0; line < 40; ++line) put(2, "chatty-icd: line " + std::to_string(line) + "\n");
    if (const char* burst = std::getenv("CAMERA_CHATTY_ICD_BURST_BYTES")) {
        const long bytes = std::strtol(burst, nullptr, 10);
        const std::string row = "chatty-icd: burst " + std::string(80, 'b') + "\n";
        for (long total = 0; total < bytes; total += static_cast<long>(row.size())) put(2, row);
    }
    put(2, "chatty-icd: unterminated tail");
    return -9;  // VK_ERROR_INCOMPATIBLE_DRIVER: the loader skips this driver
}

__attribute__((visibility("default"))) void* vk_icdGetInstanceProcAddr(void* instance, const char* name) {
    (void)instance;
    (void)name;
    return nullptr;
}

} // extern "C"
