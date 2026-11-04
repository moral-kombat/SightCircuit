// Tracking reporter logic for the overlay bridge; no OpenVR header and no device access.
// tools/overlay_bridge.cpp drives it with vr::IVRSystem through a thin adapter, and
// tests/fixtures/tracking_report_fixture.cpp with a fake system. Event and field names and
// the change table come from the generated hud_contract.hpp (framejacker/hud/contract.py),
// whose lines sources.session.TrackingObserver reads.
//
// A System provides exactly these reads:
//   std::uint64_t universe_id(std::uint32_t& error);  // HMD Prop_CurrentUniverseId_Uint64; error 0 = success
//   Matrix raw_zero_pose();     // GetRawZeroPoseToStandingAbsoluteTrackingPose
//   Matrix seated_zero_pose();  // GetSeatedZeroPoseToStandingAbsoluteTrackingPose
// where Matrix has a member `float m[3][4]` (vr::HmdMatrix34_t).
//
// zero_pose_sha256 is SHA-256 (FIPS 180-4) of 96 bytes: the raw standing zero pose, then the
// seated one, each row-major (m[0][0], m[0][1], ..., m[2][3]), each float as its exact
// IEEE-754 binary32 bit pattern, little-endian. Values are not normalized or judged: NaN
// payloads, infinities, signed zeros and subnormals are digested by their bits. In Python:
//   hashlib.sha256(struct.pack("<24f", *raw_rows, *seated_rows)).hexdigest()
// (docs/hud-qualification.md, "Session tokens for the mirror source").
#pragma once
#include "hud_contract.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <limits>
#include <string>
#include <type_traits>

namespace framejacker_tracking {
namespace contract = framejacker_contract;

class Sha256 {
public:
    Sha256& update(const std::uint8_t* data, std::size_t size) {
        for (std::size_t i = 0; i < size; ++i) {
            block_[used_++] = data[i];
            if (used_ == block_.size()) { compress(); used_ = 0; }
        }
        bytes_ += size;
        return *this;
    }
    std::array<std::uint8_t, 32> finish() {
        const std::uint64_t bits = bytes_ * 8;
        const std::uint8_t one = 0x80, zero = 0;
        update(&one, 1);
        while (used_ != 56) update(&zero, 1);
        std::uint8_t length[8];
        for (unsigned i = 0; i < 8; ++i) length[i] = std::uint8_t(bits >> (56 - 8 * i));
        update(length, 8);
        std::array<std::uint8_t, 32> digest{};
        for (unsigned i = 0; i < 32; ++i) digest[i] = std::uint8_t(state_[i / 4] >> (24 - 8 * (i % 4)));
        return digest;
    }

private:
    static std::uint32_t rotate(std::uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }
    void compress() {
        static constexpr std::uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
        std::uint32_t w[64];
        for (unsigned i = 0; i < 16; ++i)
            w[i] = std::uint32_t(block_[4 * i]) << 24 | std::uint32_t(block_[4 * i + 1]) << 16 |
                   std::uint32_t(block_[4 * i + 2]) << 8 | std::uint32_t(block_[4 * i + 3]);
        for (unsigned i = 16; i < 64; ++i) {
            const auto s0 = rotate(w[i - 15], 7) ^ rotate(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const auto s1 = rotate(w[i - 2], 17) ^ rotate(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        auto a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        auto e = state_[4], f = state_[5], g = state_[6], h = state_[7];
        for (unsigned i = 0; i < 64; ++i) {
            const auto t1 = h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            const auto t2 = (rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }
    std::array<std::uint32_t, 8> state_{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    std::array<std::uint8_t, 64> block_{};
    std::size_t used_ = 0;
    std::uint64_t bytes_ = 0;
};

inline std::string hex(const std::array<std::uint8_t, 32>& digest) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string text;
    for (const auto byte : digest) { text += digits[byte >> 4]; text += digits[byte & 15]; }
    return text;
}
inline std::string sha256_hex(const std::uint8_t* data, std::size_t size) {
    return hex(Sha256().update(data, size).finish());
}

constexpr std::size_t kZeroPoseBytes = 2 * 3 * 4 * sizeof(std::uint32_t);
template <class Matrix> void append_matrix(std::array<std::uint8_t, kZeroPoseBytes>& bytes, std::size_t& at,
                                           const Matrix& matrix) {
    static_assert(std::is_same_v<decltype(Matrix::m), float[3][4]>, "zero poses are 3x4 float matrices");
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "IEEE-754 binary32 floats");
    for (unsigned row = 0; row < 3; ++row)
        for (unsigned column = 0; column < 4; ++column) {
            std::uint32_t bits;
            std::memcpy(&bits, &matrix.m[row][column], sizeof bits);
            for (unsigned shift = 0; shift < 32; shift += 8) bytes[at++] = std::uint8_t(bits >> shift);
        }
}
// The digested bytes: raw standing zero pose first, then seated (see the top of this file).
template <class Matrix> std::array<std::uint8_t, kZeroPoseBytes> zero_pose_bytes(const Matrix& raw,
                                                                                 const Matrix& seated) {
    std::array<std::uint8_t, kZeroPoseBytes> bytes{};
    std::size_t at = 0;
    append_matrix(bytes, at, raw);
    append_matrix(bytes, at, seated);
    return bytes;
}

// The change table is generated; every id and name must be distinct and present.
constexpr std::size_t kTrackingChanges = std::size(contract::kTrackingChangeEventIds);
static_assert(kTrackingChanges == std::size(contract::kTrackingChangeNames) &&
              kTrackingChanges == std::size(contract::kTrackingChangeOpenvrEvents), "one name per change id");
constexpr bool change_ids_distinct() {
    for (std::size_t i = 0; i < kTrackingChanges; ++i)
        for (std::size_t j = i + 1; j < kTrackingChanges; ++j)
            if (contract::kTrackingChangeEventIds[i] == contract::kTrackingChangeEventIds[j]) return false;
    return true;
}
static_assert(change_ids_distinct(), "each OpenVR event maps to one change");

// The change name an OpenVR event id reports, or nullptr for any other event.
inline const char* change_name(std::uint32_t event_type) {
    for (std::size_t i = 0; i < kTrackingChanges; ++i)
        if (contract::kTrackingChangeEventIds[i] == event_type) return contract::kTrackingChangeNames[i];
    return nullptr;
}

// Bounded reasons of tracking_unavailable: no state could be read, so none is emitted.
constexpr char kUniverseIdPropertyError[] = "universe_id_property_error";  // With the property error code.
constexpr char kUniverseIdZero[] = "universe_id_zero";  // OpenVR's error value; never a state.

enum class EventKind { state, changed, unavailable };
struct Event {
    EventKind kind = EventKind::unavailable;
    std::uint64_t universe_id = 0;     // state: 1 .. 2**64-1
    std::string zero_pose_sha256;      // state: 64 lowercase hex
    const char* change = nullptr;      // changed: a contract::kTrackingChangeNames entry
    const char* reason = nullptr;      // unavailable: kUniverseIdPropertyError or kUniverseIdZero
    std::uint32_t property_error = 0;  // unavailable after a property error: its nonzero code
};

// Every field of one line, in order, as put(key, std::string) or put(key, std::uint64_t); the
// bridge writes them with jsoncpp (plus its report_kind and schema_version), the fixture as text.
template <class Put> void fields(const Event& event, Put&& put) {
    switch (event.kind) {
    case EventKind::state:
        put("event", std::string(contract::kTrackingStateEvent));
        put(contract::kTrackingUniverseField, event.universe_id);
        put(contract::kTrackingZeroPoseField, event.zero_pose_sha256);
        return;
    case EventKind::changed:
        put("event", std::string(contract::kTrackingChangedEvent));
        put(contract::kTrackingChangeField, std::string(event.change));
        return;
    case EventKind::unavailable:
        put("event", std::string(contract::kTrackingUnavailableEvent));
        put(contract::kTrackingReasonField, std::string(event.reason));
        if (event.property_error) put(contract::kTrackingPropertyErrorField, std::uint64_t{event.property_error});
        return;
    }
}

// The current state: universe id, then (only when it is valid) both zero poses.
template <class System> Event read_state(System& system) {
    Event event;
    std::uint32_t error = 0;
    const std::uint64_t universe = system.universe_id(error);
    if (error) {
        event.reason = kUniverseIdPropertyError;
        event.property_error = error;
        return event;
    }
    if (!universe) {
        event.reason = kUniverseIdZero;
        return event;
    }
    const auto raw = system.raw_zero_pose();
    const auto seated = system.seated_zero_pose();
    const auto bytes = zero_pose_bytes(raw, seated);
    event.kind = EventKind::state;
    event.universe_id = universe;
    event.zero_pose_sha256 = sha256_hex(bytes.data(), bytes.size());
    return event;
}

template <class System> class Reporter {
public:
    explicit Reporter(System& system) : system_(system) {}
    // The first line: the current state, or tracking_unavailable.
    template <class Emit> void start(Emit&& emit) { report(read_state(system_), emit); }
    // One runtime event. A tracking change emits tracking_changed, then a fresh state read after it;
    // any other event emits and reads nothing. Returns whether it was a tracking change.
    template <class Emit> bool event(std::uint32_t event_type, Emit&& emit) {
        const char* name = change_name(event_type);
        if (!name) return false;
        Event changed;
        changed.kind = EventKind::changed;
        changed.change = name;
        report(changed, emit);
        report(read_state(system_), emit);
        return true;
    }
    std::uint64_t states = 0, changes = 0, unavailable = 0;

private:
    template <class Emit> void report(const Event& event, Emit& emit) {
        ++(event.kind == EventKind::state ? states : event.kind == EventKind::changed ? changes : unavailable);
        emit(event);
    }
    System& system_;
};
} // namespace framejacker_tracking
