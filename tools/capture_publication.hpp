// Original Framejacker bounded luma publisher; MIT licensed. No Vulkan calls.
#pragma once
#include "capture_transport.hpp"
#include "shm_common.hpp"
#include <cstdlib>
#include <filesystem>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>

namespace framejacker::publication {
using transport::Error;
using transport::Fd;

inline bool same_file(const struct stat &a, const struct stat &b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}

inline void require_private_directory(const struct stat &info) {
    if (!S_ISDIR(info.st_mode) || info.st_uid != geteuid() || (info.st_mode & 0077))
        throw Error("publication_directory_not_private");
}

inline std::string runtime_source() {
    const char *runtime = std::getenv("XDG_RUNTIME_DIR");
    if (!runtime || runtime[0] != '/') throw Error("runtime_directory_unavailable");
    struct stat info{};
    if (lstat(runtime, &info) != 0) throw Error("runtime_directory_unavailable");
    require_private_directory(info);
    std::string parent = std::string(runtime) + "/framejacker-capture";
    if (mkdir(parent.c_str(), 0700) != 0 && errno != EEXIST)
        throw Error("publication_directory_create_failed");
    if (lstat(parent.c_str(), &info) != 0) throw Error("publication_directory_unavailable");
    require_private_directory(info);
    return parent + "/frames.shm";
}

class Publisher {
    Fd directory_, file_;
    std::string parent_, name_;
    struct stat directory_identity_{}, identity_{};
    uint8_t *mapping_ = nullptr;
    size_t length_ = 0, frame_bytes_ = 0;
    uint32_t width_, height_, cameras_;
    uint64_t last_time_[2]{}, last_count_[2]{};
    bool owns_name_ = false, cleanup_ok_ = true;
public:
    Publisher(const std::string &path, uint32_t width, uint32_t height, uint32_t cameras)
        : width_(width), height_(height), cameras_(cameras) {
        if (!width || !height || width > 8192 || height > 8192 || cameras < 1 || cameras > 2 ||
            uint64_t(width) * height > framejacker_shm::maxFrameBytes)
            throw Error("invalid_publication_dimensions");
        auto selected = std::filesystem::path(path);
        if (!selected.is_absolute() || selected.filename().empty() ||
            selected.filename() == "." || selected.filename() == "..")
            throw Error("invalid_publication_path");
        parent_ = selected.parent_path().string(); name_ = selected.filename().string();
        frame_bytes_ = size_t(width) * height;
        length_ = sizeof(MrhandPtHeader) + cameras * frame_bytes_;
        try {
            directory_ = Fd(open(parent_.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            if (directory_.get() < 0 || fstat(directory_.get(), &directory_identity_) != 0)
                throw Error("publication_directory_unavailable");
            require_private_directory(directory_identity_);
            file_ = Fd(openat(directory_.get(), name_.c_str(),
                             O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
            if (file_.get() < 0) throw Error("publication_create_failed");
            owns_name_ = true;
            if (fstat(file_.get(), &identity_) != 0 || !S_ISREG(identity_.st_mode) ||
                identity_.st_uid != geteuid() || identity_.st_nlink != 1 ||
                ftruncate(file_.get(), static_cast<off_t>(length_)) != 0)
                throw Error("publication_resize_failed");
            void *memory = mmap(nullptr, length_, PROT_READ | PROT_WRITE, MAP_SHARED, file_.get(), 0);
            if (memory == MAP_FAILED) throw Error("publication_map_failed");
            mapping_ = static_cast<uint8_t *>(memory);
            std::memset(mapping_, 0, length_);
            auto *header = reinterpret_cast<MrhandPtHeader *>(mapping_);
            header->width = width; header->height = height; header->cams = cameras;
            __atomic_store_n(&header->magic, MRHAND_PT_MAGIC, __ATOMIC_RELEASE);
        } catch (...) { close(); throw; }
    }
    Publisher(const Publisher &) = delete;
    Publisher &operator=(const Publisher &) = delete;
    ~Publisher() { close(); }
    const struct stat &identity() const { return identity_; }

    void verify() const {
        struct stat opened{}, named{}, parent{};
        if (file_.get() < 0 || !mapping_ || fstat(file_.get(), &opened) != 0 ||
            !same_file(opened, identity_) || opened.st_size != static_cast<off_t>(length_) ||
            opened.st_nlink != 1 || opened.st_uid != geteuid() || (opened.st_mode & 0077) ||
            fstatat(directory_.get(), name_.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
            !same_file(named, identity_) || lstat(parent_.c_str(), &parent) != 0 ||
            !same_file(parent, directory_identity_) || !S_ISDIR(parent.st_mode) ||
            parent.st_uid != geteuid() || (parent.st_mode & 0077)) throw Error("publication_source_changed");
    }

    bool publish(unsigned camera, const uint8_t *pixels, size_t bytes,
                 uint64_t observation_ns, uint32_t buffer_id, uint64_t frame_count) {
        if (camera >= cameras_ || !pixels || bytes != frame_bytes_ || !buffer_id ||
            !frame_count || !observation_ns || observation_ns > framejacker_shm::nowNs())
            throw Error("invalid_publication_metadata");
        verify();
        // A delayed stable-strip result must not replace a newer observation.
        if (observation_ns <= last_time_[camera] ||
            framejacker_shm::ageNs(observation_ns) > 1000000000ull) return false;
        if (frame_count <= last_count_[camera]) throw Error("publication_counter_regressed");
        auto &slot = reinterpret_cast<MrhandPtHeader *>(mapping_)->slot[camera];
        const uint64_t sequence = __atomic_load_n(&slot.seq, __ATOMIC_RELAXED);
        if ((sequence & 1) || sequence > UINT64_MAX - 2) throw Error("publication_sequence_invalid");
        __atomic_store_n(&slot.seq, sequence + 1, __ATOMIC_RELAXED);
        // Publish odd BEFORE any payload/metadata store, including on ARM64.
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        std::memcpy(mapping_ + sizeof(MrhandPtHeader) + camera * frame_bytes_, pixels, frame_bytes_);
        slot.tDetectNs = observation_ns; slot.bufferId = buffer_id; slot.frameCount = frame_count;
        __atomic_store_n(&slot.seq, sequence + 2, __ATOMIC_RELEASE);
        last_time_[camera] = observation_ns; last_count_[camera] = frame_count;
        return true;
    }

    bool close() noexcept {
        if (mapping_) {
            if (munmap(mapping_, length_) != 0) cleanup_ok_ = false;
            mapping_ = nullptr;
        }
        if (owns_name_) {
            struct stat named{};
            if (fstatat(directory_.get(), name_.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0) {
                if (!same_file(named, identity_) || unlinkat(directory_.get(), name_.c_str(), 0) != 0)
                    cleanup_ok_ = false;
            } else if (errno != ENOENT) cleanup_ok_ = false;
            owns_name_ = false;
        }
        int file = file_.release();
        if (file >= 0 && ::close(file) != 0) cleanup_ok_ = false;
        int directory = directory_.release();
        if (directory >= 0 && ::close(directory) != 0) cleanup_ok_ = false;
        return cleanup_ok_;
    }
};

enum class Mode { not_run, help, run };
struct RunOptions { Mode mode = Mode::not_run; unsigned seconds = 0, fps = 5; };
inline RunOptions parse_options(int argc, char **argv) {
    RunOptions options;
    if (argc == 1) return options;
    if (argc == 2 && std::string(argv[1]) == "--help") { options.mode = Mode::help; return options; }
    bool allowed = false, seconds = false, fps = false;
    for (int i = 1; i < argc; ++i) {
        std::string argument(argv[i]);
        if (argument == "--allow-experimental-capture" && !allowed) allowed = true;
        else if (argument == "--seconds" && !seconds && i + 1 < argc) {
            options.seconds = unsigned(framejacker_shm::number(argv[++i], 1, 10)); seconds = true;
        } else if (argument == "--max-fps" && !fps && i + 1 < argc) {
            options.fps = unsigned(framejacker_shm::number(argv[++i], 1, 10)); fps = true;
        } else throw Error("invalid_arguments");
    }
    if (!allowed || !seconds) throw Error("invalid_arguments");
    options.mode = Mode::run;
    return options;
}

class RunLimit {
    struct sigaction old_int_{}, old_term_{};
    int old_parent_signal_ = 0;
    bool has_int_ = false, has_term_ = false, has_parent_ = false;
    transport::Deadline deadline_;
    void restore() noexcept {
        if (has_int_) sigaction(SIGINT, &old_int_, nullptr);
        if (has_term_) sigaction(SIGTERM, &old_term_, nullptr);
        if (has_parent_) prctl(PR_SET_PDEATHSIG, old_parent_signal_);
    }
public:
    explicit RunLimit(unsigned seconds) {
        if (seconds < 1 || seconds > 10) throw Error("invalid_arguments");
        deadline_ = transport::Clock::now() + std::chrono::seconds(seconds);
        const pid_t parent = getppid();
        framejacker_shm::stopped = 0;
        try {
            struct sigaction action{};
            action.sa_handler = framejacker_shm::stop;
            sigemptyset(&action.sa_mask);
            if (sigaction(SIGINT, &action, &old_int_) != 0) throw Error("signal_setup_failed");
            has_int_ = true;
            if (sigaction(SIGTERM, &action, &old_term_) != 0) throw Error("signal_setup_failed");
            has_term_ = true;
            if (prctl(PR_GET_PDEATHSIG, &old_parent_signal_) != 0 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0)
                throw Error("parent_signal_setup_failed");
            has_parent_ = true;
            if (getppid() != parent) throw Error("parent_lifetime_changed");
        } catch (...) { restore(); throw; }
    }
    RunLimit(const RunLimit &) = delete;
    RunLimit &operator=(const RunLimit &) = delete;
    ~RunLimit() { restore(); }
    bool running() const { return !framejacker_shm::stopped && transport::Clock::now() < deadline_; }
    int signal() const { return framejacker_shm::stopped; }
};
} // namespace framejacker::publication
