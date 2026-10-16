// Bounded, read-only consumer of the pinned MRHAND luma shared-memory layout.
#include "shm_common.hpp"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <string>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace framejacker_shm;

struct Options {
    std::string path, preview;
    unsigned camera = 0, seconds = 10, frames = 0, maxAgeMs = 1000;
};
struct Result {
    std::string status = "error", reason = "not_started";
    uint64_t frames = 0, busy = 0, rejected = 0, unchanged = 0, stale = 0, invalidTime = 0, skipped = 0;
    uint64_t sequence = 0, frameCount = 0, timestamp = 0, elapsedNs = 0;
    uint32_t width = 0, height = 0, cameras = 0, firstPixel = 0;
    bool cleanup = true, previewWritten = false;
    int code = 1;
};

struct Source {
    int fd = -1;
    void* mapping = MAP_FAILED;
    size_t length = 0, frameBytes = 0;
    struct stat identity{};
    MrhandPtHeader initial{};

    void open(const std::string& path) {
        struct stat before{};
        if (lstat(path.c_str(), &before) != 0) throw std::runtime_error(errno == ENOENT ? "source_missing" : "source_stat_failed");
        if (!S_ISREG(before.st_mode)) throw std::runtime_error("source_not_regular");
        fd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) throw std::runtime_error("source_open_failed");
        if (fstat(fd, &identity) != 0 || !S_ISREG(identity.st_mode) ||
            identity.st_dev != before.st_dev || identity.st_ino != before.st_ino)
            throw std::runtime_error("source_changed");
        if (identity.st_size < static_cast<off_t>(sizeof initial) ||
            uint64_t(identity.st_size) > maxFileBytes) throw std::runtime_error("invalid_file_size");
        if (pread(fd, &initial, sizeof initial, 0) != static_cast<ssize_t>(sizeof initial))
            throw std::runtime_error("short_header");
        if (!validHeader(initial)) throw std::runtime_error("invalid_header");
        frameBytes = size_t(initial.width) * initial.height;
        length = sizeof initial + size_t(initial.cams) * frameBytes;
        if (uint64_t(identity.st_size) != length) throw std::runtime_error("invalid_file_size");
        mapping = mmap(nullptr, length, PROT_READ, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) throw std::runtime_error("source_map_failed");
    }
    void validate(const std::string& path) const {
        struct stat current{}, named{};
        if (fstat(fd, &current) != 0 || current.st_size != static_cast<off_t>(length))
            throw std::runtime_error("source_resized");
        if (lstat(path.c_str(), &named) != 0 || !S_ISREG(named.st_mode) ||
            named.st_dev != identity.st_dev || named.st_ino != identity.st_ino)
            throw std::runtime_error("source_replaced");
        uint32_t prefix[4]{};
        if (pread(fd, prefix, sizeof prefix, 0) != static_cast<ssize_t>(sizeof prefix) ||
            prefix[0] != initial.magic || prefix[1] != initial.width ||
            prefix[2] != initial.height || prefix[3] != initial.cams)
            throw std::runtime_error("source_reinitialized");
    }
    bool close() {
        bool okay = true;
        if (mapping != MAP_FAILED) {
            okay = munmap(mapping, length) == 0;
            mapping = MAP_FAILED;
        }
        if (fd >= 0) {
            okay = (::close(fd) == 0) && okay;
            fd = -1;
        }
        return okay;
    }
    ~Source() { close(); }
};

static Options parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 >= argc) throw std::runtime_error("invalid_arguments");
        std::string key = argv[i], value = argv[i + 1];
        if (key == "--read" && options.path.empty()) options.path = value;
        else if (key == "--camera") options.camera = unsigned(number(value, 0, 1));
        else if (key == "--seconds") options.seconds = unsigned(number(value, 1, 10));
        else if (key == "--frames") options.frames = unsigned(number(value, 0, 1000));
        else if (key == "--max-age-ms") options.maxAgeMs = unsigned(number(value, 1, 10000));
        else if (key == "--preview-pgm" && options.preview.empty()) options.preview = value;
        else throw std::runtime_error("invalid_arguments");
    }
    if (options.path.empty()) throw std::runtime_error("invalid_arguments");
    return options;
}

static void preview(const Options& options, const Result& result, const std::vector<uint8_t>& pixels) {
    if (options.preview.empty()) return;
    const unsigned scale = std::max(1u, (std::max(result.width, result.height) + 639) / 640);
    const unsigned width = (result.width + scale - 1) / scale, height = (result.height + scale - 1) / scale;
    std::string header = "P5\n" + std::to_string(width) + " " + std::to_string(height) + "\n255\n";
    std::vector<uint8_t> output(header.begin(), header.end());
    for (unsigned y = 0; y < result.height; y += scale)
        for (unsigned x = 0; x < result.width; x += scale) output.push_back(pixels[size_t(y) * result.width + x]);
    // A new exclusive file is mandatory, including for the wrapper's temporary PGM.
    int fd = ::open(options.preview.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) throw std::runtime_error("preview_open_failed");
    size_t done = 0;
    while (done < output.size()) {
        ssize_t count = write(fd, output.data() + done, output.size() - done);
        if (count <= 0) { ::close(fd); throw std::runtime_error("preview_write_failed"); }
        done += size_t(count);
    }
    if (::close(fd) != 0) throw std::runtime_error("preview_close_failed");
}

static void run(const Options& options, Source& source, Result& result, std::vector<uint8_t>& last) {
    source.open(options.path);
    result.width = source.initial.width; result.height = source.initial.height; result.cameras = source.initial.cams;
    if (options.camera >= source.initial.cams) throw std::runtime_error("camera_slot_absent");
    const auto* header = static_cast<const MrhandPtHeader*>(source.mapping);
    const auto* payload = static_cast<const uint8_t*>(source.mapping) + sizeof(MrhandPtHeader) + options.camera * source.frameBytes;
    const auto* slot = &header->slot[options.camera];
    std::vector<uint8_t> scratch(source.frameBytes);
    uint64_t started = nowNs(), deadline = started + uint64_t(options.seconds) * 1000000000ull, nextCopy = 0;
    while (!stopped && nowNs() < deadline) {
        source.validate(options.path);
        const uint64_t first = __atomic_load_n(&slot->seq, __ATOMIC_ACQUIRE);
        if (!first || (first & 1)) result.busy++;
        else if (first == result.sequence) result.unchanged++;
        else if (nowNs() >= nextCopy) {
            nextCopy = nowNs() + 100000000ull; // At most ten full copies per second.
            MrhandPtSlot meta{};
            std::memcpy(&meta, slot, sizeof meta);
            std::memcpy(scratch.data(), payload, scratch.size());
            // Prevent payload reads moving past the final sequence check on ARM64.
            __atomic_thread_fence(__ATOMIC_SEQ_CST);
            const uint64_t second = __atomic_load_n(&slot->seq, __ATOMIC_ACQUIRE);
            if (first != second || (second & 1) || meta.seq != first) result.rejected++;
            else {
                if (result.sequence && (first < result.sequence || meta.frameCount < result.frameCount || meta.tDetectNs < result.timestamp))
                    throw std::runtime_error("source_restarted");
                const uint64_t now = nowNs();
                if (!meta.tDetectNs || meta.tDetectNs > now + 100000000ull) result.invalidTime++;
                else if (now > meta.tDetectNs && now - meta.tDetectNs > uint64_t(options.maxAgeMs) * 1000000ull) result.stale++;
                else {
                    if (result.sequence && first > result.sequence + 2) result.skipped += (first - result.sequence) / 2 - 1;
                    result.sequence = first; result.frameCount = meta.frameCount; result.timestamp = meta.tDetectNs;
                    result.firstPixel = scratch[0]; result.frames++;
                    if (!options.preview.empty()) last = scratch;
                    if (options.frames && result.frames >= options.frames) break;
                }
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    result.elapsedNs = nowNs() - started;
    if (stopped) { result.status = "cancelled"; result.reason = "signal"; result.code = 128 + stopped; }
    else if (result.timestamp && ageNs(result.timestamp) > uint64_t(options.maxAgeMs) * 1000000ull) {
        result.status = "stale_input"; result.reason = "last_observation_stale";
    } else if (result.frames) { result.status = "captured"; result.reason = "bounded_read_complete"; result.code = 0; }
    else { result.status = result.stale ? "stale_input" : "no_frames";
           result.reason = result.invalidTime ? "invalid_observation_time" : result.stale ? "observations_too_old" : "no_complete_new_slot"; }
}

static void emit(Result result, const Options& options) {
    if (result.reason.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != std::string::npos)
        result.reason = "native_error";
    uint64_t age = result.timestamp ? ageNs(result.timestamp) : 0;
    std::cout << std::boolalpha
        << "{\"report_kind\":\"framejacker_shm_reader\",\"schema_version\":1,\"status\":\"" << result.status
        << "\",\"reason\":\"" << result.reason << "\",\"exit_code\":" << result.code
        << ",\"width\":" << result.width << ",\"height\":" << result.height << ",\"camera_slots\":" << result.cameras
        << ",\"selected_slot\":" << options.camera << ",\"frames_received\":" << result.frames
        << ",\"busy_sequence_polls\":" << result.busy << ",\"rejected_copies\":" << result.rejected
        << ",\"unchanged_sequence_polls\":" << result.unchanged << ",\"stale_copies\":" << result.stale
        << ",\"invalid_observation_times\":" << result.invalidTime << ",\"publications_skipped_by_reader\":" << result.skipped
        << ",\"last_sequence\":" << result.sequence << ",\"last_frame_count\":" << result.frameCount
        << ",\"last_observation_ns\":" << result.timestamp << ",\"last_observation_age_ms\":" << age / 1000000.0
        << ",\"last_first_pixel\":" << result.firstPixel << ",\"elapsed_ms\":" << result.elapsedNs / 1000000.0
        << ",\"observed_frame_rate_hz\":" << (result.elapsedNs ? result.frames * 1000000000.0 / result.elapsedNs : 0)
        << ",\"cleanup_ok\":" << result.cleanup << ",\"preview_written\":" << result.previewWritten
        << ",\"maximum_copies_per_second\":10,\"pixel_format\":\"gray8\",\"writer_ordering_verified\":false"
        << ",\"camera_frame_integrity_verified\":false,\"source_identity\":\"unverified\"}\n";
}

int main(int argc, char** argv) {
    Options options; Result result;
    if (argc == 2 && std::string(argv[1]) == "--help") {
        std::cout << "Read-only diagnostic: --read REGULAR_FILE [--camera 0|1] [--seconds 1..10] [--frames 0..1000] [--max-age-ms 1..10000] [--preview-pgm NEW_FILE]\n";
        return 0;
    }
    if (argc == 1) { result.status = "not_run"; result.reason = "read_flag_required"; result.code = 3; emit(result, options); return 3; }
    try { options = parse(argc, argv); }
    catch (...) { result.reason = "invalid_arguments"; result.code = 64; emit(result, options); return 64; }
    Source source;
    std::vector<uint8_t> last;
    try {
        rlimit core{0, 0};
        if (setrlimit(RLIMIT_CORE, &core) != 0) throw std::runtime_error("core_limit_failed");
        const pid_t parent = getppid();
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) throw std::runtime_error("parent_lifetime_changed");
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        run(options, source, result, last);
    } catch (const std::exception& error) { result.status = "error"; result.reason = error.what(); result.code = 1; }
    result.cleanup = source.close();
    if (!result.cleanup) { result.status = "error"; result.reason = "cleanup_failed"; result.code = 1; }
    if (result.code == 0 && !options.preview.empty()) {
        try { preview(options, result, last); result.previewWritten = true; }
        catch (const std::exception& error) { result.status = "error"; result.reason = error.what(); result.code = 1; }
    }
    emit(result, options);
    return result.code;
}
