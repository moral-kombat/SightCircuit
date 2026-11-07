#pragma once
#include "handcam_proto.h"
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <csignal>
#include <ctime>
#include <stdexcept>
#include <string>

static_assert(sizeof(MrhandPtSlot) == 32 && sizeof(MrhandPtHeader) == 96,
              "Unsupported native shared-memory layout");
static_assert(offsetof(MrhandPtHeader, slot) == 16 && offsetof(MrhandPtSlot, frameCount) == 24,
              "Unsupported slot offsets");
static_assert(__atomic_always_lock_free(sizeof(uint64_t), nullptr),
              "This tool requires lock-free aligned 64-bit sequence access");

namespace sightcircuit_shm {
constexpr uint64_t maxFrameBytes = 16 * 1024 * 1024;
constexpr uint64_t maxFileBytes = sizeof(MrhandPtHeader) + 2 * maxFrameBytes;
inline volatile std::sig_atomic_t stopped = 0;
inline void stop(int value) { stopped = value; }
inline uint64_t nowNs() {
    timespec value{};
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) throw std::runtime_error("clock_failed");
    return uint64_t(value.tv_sec) * 1000000000ull + uint64_t(value.tv_nsec);
}
inline uint64_t ageNs(uint64_t timestamp) {
    const uint64_t now = nowNs();
    return now > timestamp ? now - timestamp : 0;
}
inline uint64_t number(const std::string& text, uint64_t minimum, uint64_t maximum) {
    size_t used = 0;
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("invalid_arguments");
    auto value = std::stoull(text, &used);
    if (used != text.size() || value < minimum || value > maximum)
        throw std::runtime_error("invalid_arguments");
    return value;
}
inline bool validHeader(const MrhandPtHeader& header) {
    return header.magic == MRHAND_PT_MAGIC && header.width && header.height &&
           header.width <= 8192 && header.height <= 8192 &&
           uint64_t(header.width) * header.height <= maxFrameBytes &&
           header.cams >= 1 && header.cams <= 2;
}
} // namespace sightcircuit_shm
