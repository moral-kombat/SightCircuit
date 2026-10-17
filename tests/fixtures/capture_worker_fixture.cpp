// Controlled capture-worker stand-in. No Vulkan or camera calls.
#include "capture_publication.hpp"
#include <iostream>
#include <memory>
using namespace framejacker;
static void event(const std::string &text) { std::cout << text << std::endl; }
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    const std::string mode = argv[1];
    if (mode == "malformed") { event("not json"); pause(); return 1; }
    if (mode == "partial") { std::cout << "{"; return 1; }
    if (mode == "flood") { for (;;) std::cout << std::string(4096, 'a') << std::endl; }
    if (mode == "missing") return 0;
    if (mode == "crash") { raise(SIGSEGV); return 1; }
    if (mode == "hang_startup") { signal(SIGTERM, SIG_IGN); for (;;) pause(); }
    const std::string path = std::getenv("FRAMEJACKER_CAPTURE_OUTPUT");
    publication::RunLimit limit(10);
    auto publisher = std::make_unique<publication::Publisher>(path, 16, 16, 2);
    struct stat info{};
    if (lstat(path.c_str(), &info)) return 1;
    if (mode != "unannounced")
        event("{\"event\":\"publication_ready\",\"device\":" + std::to_string(info.st_dev) +
              ",\"inode\":" + std::to_string(info.st_ino) + ",\"size\":" + std::to_string(info.st_size) + "}");
    if (mode == "replace") {
        unlink(path.c_str());
        transport::Fd file(open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600));
        if (write(file.get(), "foreign", 7) != 7) return 1;
    }
    if (mode.rfind("hang_", 0) == 0 || mode == "unannounced" || mode == "replace") {
        signal(SIGTERM, SIG_IGN);
        event("{\"event\":\"stage\",\"name\":\"" + mode + "\"}");
        for (;;) pause();
    }
    if (mode == "cancel") while (limit.running()) usleep(10000);
    const bool clean = publisher->close();
    const int code = limit.signal() ? 128 + limit.signal() : 0;
    event("{\"event\":\"capture_closed\",\"gpu_resource_cleanup_ok\":true,\"publication_cleanup_ok\":" +
          std::string(clean ? "true" : "false") + ",\"registry_unchanged\":true,\"exit_code\":" +
          std::to_string(mode == "inconsistent" ? 1 : code) + "}");
    return code;
}
