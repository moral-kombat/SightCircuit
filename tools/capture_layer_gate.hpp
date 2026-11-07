// Original opt-in and crash-loop guard; private runtime state only. MIT.
#pragma once
#include "capture_provider.hpp"
#include <cstdlib>
#include <time.h>
namespace sightcircuit::layer_gate {
inline bool claim(const std::string &runtime) {
    struct stat info{};
    if (runtime.empty() || runtime[0] != '/' || lstat(runtime.c_str(), &info)) return false;
    provider::private_directory(info);
    const std::string directory_path = runtime + "/sightcircuit-layer-guard";
    if (mkdir(directory_path.c_str(), 0700) && errno != EEXIST) return false;
    transport::Fd directory(open(directory_path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    if (directory.get() < 0 || fstat(directory.get(), &info)) return false;
    provider::private_directory(info);
    transport::Fd file(openat(directory.get(), "starts", O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0600));
    if (file.get() < 0 || fstat(file.get(), &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
        info.st_nlink != 1 || (info.st_mode & 0077) || flock(file.get(), LOCK_EX | LOCK_NB)) return false;
    uint64_t history[3]{};
    if (info.st_size != 0 && info.st_size != sizeof history) return false;
    if (info.st_size && pread(file.get(), history, sizeof history, 0) != sizeof history) return false;
    timespec now{}; if (clock_gettime(CLOCK_BOOTTIME, &now)) return false;
    const uint64_t seconds = static_cast<uint64_t>(now.tv_sec);
    uint64_t next[3]{}; unsigned count = 0;
    for (uint64_t stamp : history) if (stamp && stamp <= seconds && seconds - stamp < 120) next[count++] = stamp;
    if (count >= 2) return false;
    next[count] = seconds ? seconds : 1;
    return pwrite(file.get(), next, sizeof next, 0) == sizeof next && ftruncate(file.get(), sizeof next) == 0 && fsync(file.get()) == 0;
}
inline bool allowed() noexcept {
    static const bool decided = [] {
        try {
            const char *opt_in = std::getenv("SIGHTCIRCUIT_CAPTURE_LAYER");
            const char *runtime = std::getenv("XDG_RUNTIME_DIR");
            if (!opt_in || std::string(opt_in) != "1" || !runtime) return false;
            char executable[4096]{};
            const ssize_t bytes = readlink("/proc/self/exe", executable, sizeof executable - 1);
            if (bytes <= 0 || bytes == sizeof executable - 1) return false;
            const char *base = std::strrchr(executable, '/');
            return base && std::strcmp(base + 1, "vrcompositor") == 0 && claim(runtime);
        } catch (...) { return false; }
    }();
    return decided;
}
} // namespace sightcircuit::layer_gate
