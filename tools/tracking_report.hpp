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
// payloads, infinities, signed zeros and subnormals are digested by their bits. In Python, with
// raw_bits and seated_bits the 12 row-major uint32 bit patterns of each matrix:
//   hashlib.sha256(struct.pack("<24I", *raw_bits, *seated_bits)).hexdigest()
// struct.pack("<24f", ...) over the 24 flattened values gives the same bytes except for a
// signaling NaN, which CPython's float32 pack may quiet (3.12 packs 0x7f800001 as 0x7fc00001).
// See docs/hud-qualification.md, "Session tokens for the mirror source".
//
// The rest of the bridge's tracking glue is here too, so the fixture covers it: the option
// decision (parse_options), the only place a Reporter is made (start_if_requested), the runtime
// event pass that feeds it (drain_runtime_events) and the reporter-only loop (run_reporter_only).
#pragma once
#include "hud_contract.hpp"
#include "overlay_frame.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>

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

// The bridge's tracking options, decided from argv alone before any OpenVR call; main() uses
// nothing else to turn the reporter on (owner decision 2: off by default). The reporter runs only
// for exactly `--stdin --report-tracking` or `--report-tracking-only --seconds N`. Any other use
// of --report-tracking, and a malformed reporter-only command line, is a usage error (exit 64).
// Other modes and their own usage errors are main()'s.
struct Options {
    bool flag = false;            // --report-tracking appears anywhere
    bool stdin_tracking = false;  // --stdin --report-tracking
    bool reporter_only = false;   // --report-tracking-only (valid only with --seconds N and no error)
    int seconds = 0;              // reporter_only: contract::kTrackingReportMinSeconds .. Max
    const char* error = nullptr;  // the usage error, or nullptr
    bool enabled() const { return !error && (stdin_tracking || reporter_only); }
};
inline Options parse_options(int argc, const char* const* argv) {
    Options result;
    if (argc < 2) return result;
    const std::string mode(argv[1]);
    for (int i = 1; i < argc; ++i) result.flag = result.flag || std::string(argv[i]) == "--report-tracking";
    result.stdin_tracking = mode == "--stdin" && argc == 3 && result.flag;
    result.reporter_only = mode == "--report-tracking-only";
    if (result.flag && !result.stdin_tracking) {
        result.error = "report_tracking_requires_stdin_mode";
    } else if (result.reporter_only) {
        if (argc != 4 || std::string(argv[2]) != "--seconds") {
            result.error = "tracking_report_requires_seconds";
            return result;
        }
        // 1 to 9 ASCII digits (no sign, no space), then the contract bounds.
        const std::string text(argv[3]);
        bool digits = !text.empty() && text.size() <= 9;
        long value = 0;
        for (const char c : text) {
            digits = digits && c >= '0' && c <= '9';
            if (digits) value = value * 10 + (c - '0');
        }
        if (!digits || value < contract::kTrackingReportMinSeconds || value > contract::kTrackingReportMaxSeconds)
            result.error = "seconds_out_of_range";
        else
            result.seconds = static_cast<int>(value);
    }
    return result;
}
// The reporter-only mode's lifetime deadline on CLOCK_MONOTONIC.
inline std::uint64_t lifetime_deadline_ns(std::uint64_t start_ns, int seconds) {
    return start_ns + std::uint64_t(seconds) * 1000000000ULL;
}

// The only place a Reporter is made. Without enabled options it reads, emits and hooks nothing.
// Otherwise it emits the first state (or tracking_unavailable) and sets hook, the runtime event
// callback (Overlay::on_system_event), to report each event through the new reporter. The caller
// calls it after the ready line and before any packet; emit must outlive hook.
template <class System, class Emit, class Hook>
bool start_if_requested(const Options& options, std::optional<Reporter<System>>& reporter, System& system,
                        Emit& emit, Hook& hook) {
    if (!options.enabled()) return false;
    reporter.emplace(system);
    reporter->start(emit);
    auto* const made = &reporter;
    auto* const sink = &emit;
    hook = [made, sink](std::uint32_t type) { (*made)->event(type, *sink); };
    return true;
}

// One pass over the runtime's event queue (Overlay::events). poll(type) returns false once the
// queue is empty. Each event but quit_type goes to on_event, when it is set, in order. quit_type
// ends the pass at once, before any later event is read, and returns false; the bridge then
// acknowledges the quit and stops. Returns true when the queue is empty.
template <class Poll, class OnEvent>
bool drain_runtime_events(Poll&& poll, std::uint32_t quit_type, const OnEvent& on_event) {
    std::uint32_t type = 0;
    while (poll(type)) {
        if (type == quit_type) return false;
        if (on_event) on_event(type);
    }
    return true;
}

// --report-tracking-only after its first state: runtime events until stdin EOF or one stop packet
// (closed), a signal (stopped; VREvent_Quit throws from io.events() in the bridge) or the
// lifetime (lifetime_elapsed). Any other input is an error. The bridge supplies Io; the fixture
// fakes it:
//   bool stop_requested();           // SIGTERM/SIGINT, or the parent is gone
//   void events();                   // one runtime event pass
//   std::uint64_t now_ns();          // CLOCK_MONOTONIC
//   int wait_input(int timeout_ms);  // poll(): > 0 input or EOF pending, 0 none yet (EINTR too), < 0 failed
//   bool read_header(std::uint8_t* data, std::size_t size, const Expire& expire);
//       // read_exact: runs expire() before every read pass, the first included; false at EOF
//       // before any byte
enum class LoopEnd { closed, lifetime_elapsed, stopped };
template <class Io> LoopEnd run_reporter_only(Io& io, std::uint64_t deadline_ns) {
    // wait_input() has just reported input, so read_header's first pass reads it whatever the
    // time: EOF or a whole stop packet that lands just after the deadline is still a clean stop.
    // A packet still incomplete after that pass waits as in --stdin, but only until the lifetime.
    bool input_reported = false;
    const auto lifetime = [&] {
        if (std::exchange(input_reported, false)) return;
        if (io.now_ns() >= deadline_ns) throw std::runtime_error("tracking_lifetime_elapsed_during_input");
    };
    std::array<std::uint8_t, contract::kPacketHeaderBytes> header{};
    for (;;) {
        if (io.stop_requested()) return LoopEnd::stopped;
        io.events();
        if (io.now_ns() >= deadline_ns) return LoopEnd::lifetime_elapsed;
        const int available = io.wait_input(20);
        if (available < 0) throw std::runtime_error("stdin_poll_failed");
        if (!available) continue;
        input_reported = true;
        if (io.read_header(header.data(), header.size(), lifetime)) {
            if (std::memcmp(header.data() + contract::kPacketHeaderMagicOffset, contract::kPacketMagic,
                            sizeof contract::kPacketMagic - 1))
                throw std::runtime_error("invalid_packet_magic");
            if (framejacker_overlay::read_u32(header.data() + contract::kPacketHeaderOpcodeOffset) !=
                    contract::kOpcodeStop ||
                    framejacker_overlay::read_u32(header.data() + contract::kPacketHeaderPayloadBytesOffset))
                throw std::runtime_error("tracking_reporter_accepts_only_stop");
        }
        return LoopEnd::closed;
    }
}
} // namespace framejacker_tracking
