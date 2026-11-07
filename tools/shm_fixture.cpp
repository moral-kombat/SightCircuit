// Explicitly synthetic, bounded producer for the MRHAND luma-layout reader.
#include "shm_common.hpp"
#include <algorithm>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <string>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

using namespace sightcircuit_shm;
struct Options {
    std::string path, mode = "live";
    unsigned seconds = 3, width = 64, height = 48, fps = 20;
};
struct Fixture {
    int fd = -1;
    void* mapping = MAP_FAILED;
    size_t length = 0;
    struct stat identity{};
    void create(const Options& options) {
        if (std::filesystem::weakly_canonical(options.path) == "/dev/shm/mrhand-pt")
            throw std::runtime_error("reserved_upstream_path");
        fd = open(options.path.c_str(), O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd < 0) throw std::runtime_error("fixture_create_failed");
        length = sizeof(MrhandPtHeader) + size_t(options.width) * options.height * 2;
        if (fstat(fd, &identity) != 0 || !S_ISREG(identity.st_mode) || ftruncate(fd, off_t(length)) != 0)
            throw std::runtime_error("fixture_resize_failed");
        mapping = mmap(nullptr, length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) throw std::runtime_error("fixture_map_failed");
        std::memset(mapping, 0, length);
        auto* header = static_cast<MrhandPtHeader*>(mapping);
        header->magic = MRHAND_PT_MAGIC; header->width = options.width; header->height = options.height; header->cams = 2;
    }
    bool close() {
        bool okay = true;
        if (mapping != MAP_FAILED) { okay = munmap(mapping, length) == 0; mapping = MAP_FAILED; }
        if (fd >= 0) { okay = (::close(fd) == 0) && okay; fd = -1; }
        return okay;
    }
    ~Fixture() { close(); }
};

static Options parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 >= argc) throw std::runtime_error("invalid_arguments");
        std::string key = argv[i], value = argv[i + 1];
        if (key == "--create" && options.path.empty()) options.path = value;
        else if (key == "--seconds") options.seconds = unsigned(number(value, 1, 10));
        else if (key == "--width") options.width = unsigned(number(value, 2, 512));
        else if (key == "--height") options.height = unsigned(number(value, 2, 512));
        else if (key == "--fps") options.fps = unsigned(number(value, 1, 60));
        else if (key == "--mode") options.mode = value;
        else throw std::runtime_error("invalid_arguments");
    }
    const std::string modes = " live split odd stale one truncate replace restart ";
    if (options.path.empty() || modes.find(" " + options.mode + " ") == std::string::npos)
        throw std::runtime_error("invalid_arguments");
    return options;
}

static void publish(const Options& options, Fixture& fixture, uint64_t count) {
    auto* header = static_cast<MrhandPtHeader*>(fixture.mapping);
    const size_t frame = size_t(options.width) * options.height;
    auto* pixels = static_cast<uint8_t*>(fixture.mapping) + sizeof(MrhandPtHeader);
    for (unsigned camera = 0; camera < 2; camera++) {
        auto& slot = header->slot[camera];
        const uint64_t previous = __atomic_load_n(&slot.seq, __ATOMIC_RELAXED);
        __atomic_store_n(&slot.seq, previous | 1ull, __ATOMIC_RELAXED);
        // The odd marker must be visible before ANY payload store on ARM64.
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        const uint8_t value = uint8_t((count + camera * 17) % 256);
        std::memset(pixels + camera * frame, value, frame / 2);
        if (options.mode == "split") std::this_thread::sleep_for(std::chrono::milliseconds(4));
        std::memset(pixels + camera * frame + frame / 2, value, frame - frame / 2);
        slot.tDetectNs = nowNs() - (options.mode == "stale" ? 5000000000ull : 0);
        slot.bufferId = 100 + camera; slot.frameCount = count;
        if (options.mode != "odd") __atomic_store_n(&slot.seq, (previous & ~1ull) + 2, __ATOMIC_RELEASE);
    }
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--help") {
        std::cout << "SYNTHETIC ONLY: --create NEW_FILE [--seconds 1..10] [--width 2..512] [--height 2..512] [--fps 1..60] [--mode live|split|odd|stale|one|truncate|replace|restart]\n";
        return 0;
    }
    if (argc == 1) { std::cout << "{\"status\":\"not_run\",\"synthetic\":true,\"exit_code\":3}\n"; return 3; }
    Options options;
    try { options = parse(argc, argv); }
    catch (...) { std::cout << "{\"status\":\"invalid_arguments\",\"synthetic\":true,\"exit_code\":64}\n"; return 64; }
    Fixture fixture;
    uint64_t count = 0;
    std::string reason = "bounded_fixture_complete";
    int code = 0;
    try {
        const pid_t parent = getppid();
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) throw std::runtime_error("parent_lifetime_changed");
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        fixture.create(options);
        std::cout << "{\"event\":\"fixture_ready\",\"synthetic\":true}\n" << std::flush;
        const uint64_t started = nowNs(), deadline = started + uint64_t(options.seconds) * 1000000000ull;
        bool mutated = false;
        while (!stopped && nowNs() < deadline) {
            if (!mutated && nowNs() - started >= 500000000ull) {
                mutated = true;
                if (options.mode == "truncate") {
                    if (ftruncate(fixture.fd, 0) != 0) throw std::runtime_error("fixture_truncate_failed");
                    break;
                }
                if (options.mode == "replace") {
                    struct stat current{};
                    if (lstat(options.path.c_str(), &current) != 0 || current.st_dev != fixture.identity.st_dev ||
                        current.st_ino != fixture.identity.st_ino) throw std::runtime_error("fixture_path_changed");
                    if (unlink(options.path.c_str()) != 0) throw std::runtime_error("fixture_unlink_failed");
                    Fixture replacement;
                    replacement.create(options);
                    publish(options, replacement, 1);
                    if (!replacement.close()) throw std::runtime_error("fixture_cleanup_failed");
                    break;
                }
                if (options.mode == "restart") {
                    auto* header = static_cast<MrhandPtHeader*>(fixture.mapping);
                    for (auto& slot : header->slot) __atomic_store_n(&slot.seq, 0ull, __ATOMIC_SEQ_CST);
                    __atomic_thread_fence(__ATOMIC_SEQ_CST);
                    count = 0;
                }
            }
            if (options.mode != "one" || !count) publish(options, fixture, ++count);
            std::this_thread::sleep_for(std::chrono::milliseconds(1000 / options.fps));
        }
        if (stopped) code = 128 + stopped;
    } catch (const std::exception& error) { reason = error.what(); code = 1; }
    bool okay = fixture.close();
    if (!okay) { reason = "fixture_cleanup_failed"; code = 1; }
    if (reason.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != std::string::npos)
        reason = "native_error";
    std::cout << std::boolalpha << "{\"event\":\"fixture_closed\",\"synthetic\":true,\"reason\":\"" << reason
              << "\",\"frames_per_slot\":" << count << ",\"cleanup_ok\":" << okay << ",\"exit_code\":" << code << "}\n";
    return code;
}
