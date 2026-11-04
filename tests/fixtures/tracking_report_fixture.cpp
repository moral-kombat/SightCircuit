// Fake dispatch for the bridge's tracking reporter (tools/tracking_report.hpp): a fake
// system stands in for vr::IVRSystem, so no OpenVR header or runtime is involved.
// It asserts the SHA-256 vectors and the event mapping, then prints one JSON line per
// case ({"case": ..., "events": [...]}) for tests/test_overlay_bridge.py to feed to
// a real TrackingObserver and to compare with hashlib. It also asserts the bridge's glue
// from the same header: the option decision, the reporter's start and event hook, the
// runtime event pass and the reporter-only loop (a fake runtime queue, clock and stdin),
// and prints which of those checks ran ("bridge_glue").
#include "../../tools/tracking_report.hpp"
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

using namespace framejacker_tracking;
namespace contract = framejacker_contract;

struct Matrix { float m[3][4]; };

struct FakeSystem {
    std::uint64_t universe = 0;
    std::uint32_t error = 0;
    Matrix raw{}, seated{};
    unsigned property_reads = 0, pose_reads = 0;
    std::uint64_t universe_id(std::uint32_t& out) { ++property_reads; out = error; return universe; }
    Matrix raw_zero_pose() { ++pose_reads; return raw; }
    Matrix seated_zero_pose() { ++pose_reads; return seated; }
};

Matrix rows(std::initializer_list<std::initializer_list<float>> values) {
    Matrix matrix{};
    unsigned r = 0;
    for (const auto& row : values) {
        unsigned c = 0;
        for (const float value : row) matrix.m[r][c++] = value;
        ++r;
    }
    return matrix;
}

Matrix bits(const std::uint32_t (&values)[12]) {
    Matrix matrix{};
    for (unsigned i = 0; i < 12; ++i) std::memcpy(&matrix.m[i / 4][i % 4], &values[i], 4);
    return matrix;
}

std::string quoted(const std::string& text) {
    for (const char c : text) assert(c != '"' && c != '\\' && static_cast<unsigned char>(c) >= 32);
    return "\"" + text + "\"";
}

// The bridge writes the same fields (tools/overlay_bridge.cpp, emit_tracking) through jsoncpp.
std::string json(const Event& event) {
    std::ostringstream out;
    bool first = true;
    fields(event, [&](const char* key, const auto& value) {
        out << (first ? "{" : ",") << quoted(key) << ":";
        if constexpr (std::is_same_v<std::decay_t<decltype(value)>, std::uint64_t>) out << value;
        else out << quoted(value);
        first = false;
    });
    out << "}";
    return out.str();
}

struct Recorder {
    std::vector<Event> events;
    void operator()(const Event& event) { events.push_back(event); }
};

void print(const char* name, const std::vector<Event>& events) {
    std::cout << "{\"case\":" << quoted(name) << ",\"events\":[";
    for (std::size_t i = 0; i < events.size(); ++i) std::cout << (i ? "," : "") << json(events[i]);
    std::cout << "]}\n";
}

std::string digest(const std::string& text) {
    return sha256_hex(reinterpret_cast<const std::uint8_t*>(text.data()), text.size());
}

void sha256_vectors() {
    // FIPS 180-4 examples (NIST CSRC "SHA256.pdf", "SHA2_Additional.pdf").
    assert(digest("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert(digest("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert(digest("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ==
           "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    assert(digest("abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrs"
                  "mnopqrstnopqrstu") == "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
    assert(digest(std::string(1000000, 'a')) ==
           "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    // Split updates (across and within 64-byte blocks) equal one update.
    std::string text;
    for (unsigned i = 0; i < 300; ++i) text.push_back(static_cast<char>((i * 7 + 3) & 255));
    for (const std::size_t split : {std::size_t{0}, std::size_t{1}, std::size_t{55}, std::size_t{56},
                                    std::size_t{63}, std::size_t{64}, std::size_t{65}, std::size_t{299}}) {
        Sha256 hash;
        const auto* data = reinterpret_cast<const std::uint8_t*>(text.data());
        hash.update(data, split).update(data + split, text.size() - split);
        assert(hex(hash.finish()) == digest(text));
    }
    // Every length around the padding boundaries, for the Python side to check with hashlib.
    std::cout << "{\"case\":\"sha256_lengths\",\"digests\":[";
    for (std::size_t length = 0; length <= 200; ++length)
        std::cout << (length ? "," : "") << quoted(digest(text.substr(0, length)));
    std::cout << "]}\n";
}

const Matrix kRaw = rows({{1, 0, 0, .25f}, {0, 1, 0, 1.5f}, {0, 0, 1, -.75f}});
const Matrix kSeated = rows({{0, 0, -1, .5f}, {0, 1, 0, 1.25f}, {1, 0, 0, -2}});
// hashlib.sha256(struct.pack("<24f", *flattened raw values, *flattened seated values)).hexdigest();
// exact here because every value is finite (tools/tracking_report.hpp has the bit-pattern form).
constexpr char kKnownDigest[] = "89f843370bf3cd06a4ab10d278d1571ca7e7823674cefabbe1c396f2b5caa6cf";

using Hook = std::function<void(std::uint32_t)>;

Options parse(const std::vector<const char*>& arguments) {
    std::vector<const char*> argv{"framejacker-overlay-bridge"};
    argv.insert(argv.end(), arguments.begin(), arguments.end());
    return parse_options(static_cast<int>(argv.size()), argv.data());
}

// The bridge's option decision (main): the reporter is off unless argv asks for it, and then
// start_if_requested reads, emits and hooks nothing at all (owner decision 2).
void option_cases() {
    // Every mode without the flag, including the approved E47 (--stdin) and G3 (marker) runs.
    const std::vector<std::vector<const char*>> off = {
        {}, {"--stdin"}, {"--test-panel"}, {"--test-panel", "--seconds", "10"}, {"--validate-stdin"},
        {"--calibration-marker", "--seconds", "20", "--allow-calibration-output"},
        {"--validate-calibration-marker", "--seconds", "20"}, {"--render-calibration-marker", "out.rgba"},
        {"--stdin", "--report-tracking-only"}, {"--validate-stdin", "--report-tracking-only", "--seconds", "5"},
        {"--help"}, {"--unknown"}};
    for (const auto& arguments : off) {
        const auto chosen = parse(arguments);
        assert(!chosen.flag && !chosen.stdin_tracking && !chosen.reporter_only && !chosen.error);
        assert(!chosen.enabled());
        FakeSystem system;
        system.universe = 7;
        std::optional<Reporter<FakeSystem>> reporter;
        Recorder lines;
        Hook hook;
        assert(!start_if_requested(chosen, reporter, system, lines, hook));
        assert(!reporter && !hook && lines.events.empty());
        assert(system.property_reads == 0 && system.pose_reads == 0);
    }
    // The two accepted forms; --seconds takes both contract bounds and anything between.
    const auto low = contract::kTrackingReportMinSeconds, high = contract::kTrackingReportMaxSeconds;
    const std::string low_text = std::to_string(low), high_text = std::to_string(high);
    const auto on = parse({"--stdin", "--report-tracking"});
    assert(on.flag && on.stdin_tracking && !on.reporter_only && !on.error && on.enabled());
    const std::pair<const char*, int> accepted[] = {{low_text.c_str(), low}, {high_text.c_str(), high},
                                                    {"37", 37}, {"000000005", 5}};
    for (const auto& [text, seconds] : accepted) {
        const auto only = parse({"--report-tracking-only", "--seconds", text});
        assert(!only.flag && only.reporter_only && !only.stdin_tracking && !only.error && only.enabled());
        assert(only.seconds == seconds);
    }
    assert(lifetime_deadline_ns(7, low) == 7 + std::uint64_t(low) * 1000000000ULL);
    assert(lifetime_deadline_ns(7, high) == 7 + std::uint64_t(high) * 1000000000ULL);
    // An accepted form makes the reporter, emits the first state and hooks runtime events to it.
    for (const auto& chosen : {on, parse({"--report-tracking-only", "--seconds", "5"})}) {
        FakeSystem system;
        system.universe = 7;
        system.raw = kRaw;
        system.seated = kSeated;
        std::optional<Reporter<FakeSystem>> reporter;
        Recorder lines;
        Hook hook;
        assert(start_if_requested(chosen, reporter, system, lines, hook));
        assert(reporter && hook && system.property_reads == 1 && system.pose_reads == 2);
        assert(lines.events.size() == 1 && lines.events[0].kind == EventKind::state);
        assert(lines.events[0].zero_pose_sha256 == kKnownDigest);
        hook(100);  // Not a tracking change: nothing.
        assert(lines.events.size() == 1 && system.property_reads == 1);
        hook(contract::kTrackingChangeEventIds[3]);
        assert(lines.events.size() == 3 && lines.events[1].kind == EventKind::changed);
        assert(std::string(lines.events[1].change) == contract::kTrackingChangeNames[3]);
        assert(lines.events[2].kind == EventKind::state && reporter->changes == 1 && reporter->states == 2);
    }
    // Every refusal of the built bridge's option matrix, decided here; nothing is read.
    const char* flag = "report_tracking_requires_stdin_mode";
    const char* form = "tracking_report_requires_seconds";
    const char* range = "seconds_out_of_range";
    const std::string below = std::to_string(low - 1), above = std::to_string(high + 1);
    const std::vector<std::pair<std::vector<const char*>, const char*>> refused = {
        {{"--validate-stdin", "--report-tracking"}, flag},
        {{"--test-panel", "--report-tracking"}, flag},
        {{"--test-panel", "--seconds", "1", "--report-tracking"}, flag},
        {{"--validate-calibration-marker", "--seconds", "1", "--report-tracking"}, flag},
        {{"--calibration-marker", "--seconds", "1", "--allow-calibration-output", "--report-tracking"}, flag},
        {{"--render-calibration-marker", "out.rgba", "--report-tracking"}, flag},
        {{"--report-tracking"}, flag},
        {{"--report-tracking", "--stdin"}, flag},
        {{"--stdin", "--report-tracking", "--report-tracking"}, flag},
        {{"--stdin", "--report-tracking", "--seconds", "5"}, flag},
        {{"--report-tracking-only", "--seconds", "5", "--report-tracking"}, flag},
        {{"--report-tracking-only"}, form},
        {{"--report-tracking-only", "--seconds"}, form},
        {{"--report-tracking-only", "--seconds", "5", "--seconds", "5"}, form},
        {{"--report-tracking-only", "5"}, form},
        {{"--report-tracking-only", "--seconds", below.c_str()}, range},
        {{"--report-tracking-only", "--seconds", above.c_str()}, range},
        {{"--report-tracking-only", "--seconds", ""}, range},
        {{"--report-tracking-only", "--seconds", "5x"}, range},
        {{"--report-tracking-only", "--seconds", "-5"}, range},
        {{"--report-tracking-only", "--seconds", "+5"}, range},
        {{"--report-tracking-only", "--seconds", " 5"}, range},
        {{"--report-tracking-only", "--seconds", "1000000000"}, range},
        {{"--report-tracking-only", "--seconds", "99999999999999999999"}, range}};
    for (const auto& [arguments, error] : refused) {
        const auto chosen = parse(arguments);
        assert(chosen.error && std::string(chosen.error) == error && !chosen.enabled());
        FakeSystem system;
        system.universe = 7;
        std::optional<Reporter<FakeSystem>> reporter;
        Recorder lines;
        Hook hook;
        assert(!start_if_requested(chosen, reporter, system, lines, hook));
        assert(!reporter && !hook && lines.events.empty() && system.property_reads == 0);
    }
}

// Overlay::events: a fake runtime queue through drain_runtime_events and the reporter's hook.
void runtime_event_cases() {
    constexpr std::uint32_t quit = 700;  // VREvent_Quit in the pinned openvr.h.
    const std::uint32_t standing = contract::kTrackingChangeEventIds[2], seated = contract::kTrackingChangeEventIds[1];
    const auto queue_poll = [](const std::vector<std::uint32_t>& queue, std::size_t& next) {
        return [items = &queue, at = &next](std::uint32_t& type) {
            if (*at == items->size()) return false;
            type = (*items)[(*at)++];
            return true;
        };
    };
    FakeSystem system;
    system.universe = 9;
    system.raw = kRaw;
    system.seated = kSeated;
    std::optional<Reporter<FakeSystem>> reporter;
    Recorder lines;
    Hook hook;
    assert(start_if_requested(parse({"--stdin", "--report-tracking"}), reporter, system, lines, hook));
    // A change reaches the reporter in order; Quit ends the pass before any later event is read.
    const std::vector<std::uint32_t> queue = {1, standing, 100, quit, seated};
    std::size_t next = 0;
    assert(!drain_runtime_events(queue_poll(queue, next), quit, hook));
    assert(next == 4 && lines.events.size() == 3);
    assert(lines.events[1].kind == EventKind::changed && std::string(lines.events[1].change) ==
           contract::kTrackingChangeNames[2]);
    assert(lines.events[2].kind == EventKind::state && system.property_reads == 2);
    // The next pass reads on from there and empties the queue.
    assert(drain_runtime_events(queue_poll(queue, next), quit, hook));
    assert(next == queue.size() && lines.events.size() == 5);
    assert(std::string(lines.events[3].change) == contract::kTrackingChangeNames[1]);
    // Without the flag nothing is hooked: changes are drained and nothing is read or emitted.
    FakeSystem idle;
    idle.universe = 9;
    std::optional<Reporter<FakeSystem>> absent;
    Recorder quiet;
    Hook none;
    assert(!start_if_requested(parse({"--stdin"}), absent, idle, quiet, none));
    const std::vector<std::uint32_t> changes(std::begin(contract::kTrackingChangeEventIds),
                                             std::end(contract::kTrackingChangeEventIds));
    std::size_t drained = 0;
    assert(drain_runtime_events(queue_poll(changes, drained), quit, none));
    assert(drained == changes.size() && quiet.events.empty() && idle.property_reads == 0 && idle.pose_reads == 0);
    // Quit first: nothing else is read.
    const std::vector<std::uint32_t> quit_first = {quit, standing};
    std::size_t stopped = 0;
    assert(!drain_runtime_events(queue_poll(quit_first, stopped), quit, none) && stopped == 1);
}

// The reporter-only loop's input, faked: a clock that waiting and stalled reads advance, what
// poll() reports and when, and what stdin delivers then (one chunk per read pass, then EOF or a
// stall). read_header models read_exact in tools/overlay_bridge.cpp: expire() before every read
// pass, the first included.
struct FakeIo {
    std::uint64_t now = 0;
    bool stop = false;
    std::uint64_t input_at = 0;  // 0: no input ever; otherwise when poll() reports it
    int input_poll = 1;          // what wait_input returns then (-1: poll failed)
    std::vector<std::vector<std::uint8_t>> chunks;
    bool eof = true;             // after the chunks: EOF, or nothing more (a stalled packet)
    unsigned event_passes = 0, waits = 0, header_reads = 0;
    bool stop_requested() const { return stop; }
    void events() { ++event_passes; }
    std::uint64_t now_ns() const { return now; }
    int wait_input(int timeout_ms) {
        assert(++waits < 100000);  // A loop that ignores its lifetime fails here instead of hanging.
        const std::uint64_t timeout = std::uint64_t(timeout_ms) * 1000000;
        if (input_at && now + timeout >= input_at) {
            if (now < input_at) now = input_at;
            return input_poll;
        }
        now += timeout;
        return 0;
    }
    template <class Expire> bool read_header(std::uint8_t* data, std::size_t size, const Expire& expire) {
        ++header_reads;
        std::size_t received = 0, chunk = 0;
        for (unsigned pass = 0; received < size; ++pass) {
            assert(pass < 100000);  // A read that ignores the lifetime fails here instead of hanging.
            expire();
            if (chunk < chunks.size()) {
                const auto& bytes = chunks[chunk++];
                assert(received + bytes.size() <= size);
                std::memcpy(data + received, bytes.data(), bytes.size());
                received += bytes.size();
            } else if (eof) {
                if (!received) return false;
                throw std::runtime_error("truncated_packet");
            } else {
                now += 1000000;  // Nothing arrives; read_exact polls again.
            }
        }
        return true;
    }
};

std::vector<std::uint8_t> packet_header(std::uint32_t opcode, std::uint32_t payload, bool magic = true) {
    std::vector<std::uint8_t> header(contract::kPacketHeaderBytes);
    if (magic)
        std::memcpy(header.data() + contract::kPacketHeaderMagicOffset, contract::kPacketMagic,
                    sizeof contract::kPacketMagic - 1);
    for (unsigned i = 0; i < 4; ++i) {
        header[contract::kPacketHeaderOpcodeOffset + i] = std::uint8_t(opcode >> (8 * i));
        header[contract::kPacketHeaderPayloadBytesOffset + i] = std::uint8_t(payload >> (8 * i));
    }
    return header;
}

template <class Run> std::string error_of(Run&& run) {
    try {
        run();
    } catch (const std::runtime_error& error) {
        return error.what();
    }
    return "";
}

void reporter_only_loop_cases() {
    constexpr std::uint64_t ms = 1000000, deadline = 50 * ms;
    const auto stop = packet_header(contract::kOpcodeStop, 0);
    const auto run = [&](FakeIo& io) { return run_reporter_only(io, deadline); };
    {   // No input: the lifetime ends it, with a runtime event pass before every wait.
        FakeIo io;
        assert(run(io) == LoopEnd::lifetime_elapsed);
        assert(io.now >= deadline && io.header_reads == 0 && io.waits > 1 && io.event_passes == io.waits + 1);
    }
    {   // A signal stops it before any runtime event pass.
        FakeIo io;
        io.stop = true;
        assert(run(io) == LoopEnd::stopped && io.event_passes == 0 && io.waits == 0);
    }
    for (const std::uint64_t at : {10 * ms, deadline + 5 * ms}) {
        // stdin EOF, or one stop packet (whole or in pieces), closes it. Input that poll()
        // reported just after the deadline is read, not cut off: it is a clean stop.
        FakeIo eof;
        eof.input_at = at;
        assert(run(eof) == LoopEnd::closed && eof.header_reads == 1);
        FakeIo whole;
        whole.input_at = at;
        whole.chunks = {stop};
        assert(run(whole) == LoopEnd::closed && whole.header_reads == 1);
        FakeIo pieces;
        pieces.input_at = at;
        pieces.chunks = {{stop.begin(), stop.begin() + 4}, {stop.begin() + 4, stop.end()}};
        // In pieces after the deadline the second pass is cut off, so only before it is this clean.
        if (at < deadline) assert(run(pieces) == LoopEnd::closed);
        else assert(error_of([&] { run(pieces); }) == "tracking_lifetime_elapsed_during_input");
    }
    {   // A packet still incomplete when the lifetime ends is an error, whenever it started.
        for (const std::uint64_t at : {10 * ms, deadline + 5 * ms}) {
            FakeIo stalled;
            stalled.input_at = at;
            stalled.chunks = {{stop.begin(), stop.begin() + 4}};
            stalled.eof = false;
            assert(error_of([&] { run(stalled); }) == "tracking_lifetime_elapsed_during_input");
            assert(stalled.now >= deadline);
        }
    }
    // Any other input is an error.
    const struct { std::uint32_t opcode, payload; bool magic; const char* error; } refused[] = {
        {contract::kOpcodeFrame, contract::kFramePrefixBytes, true, "tracking_reporter_accepts_only_stop"},
        {contract::kOpcodeSettings, contract::kSettingsBytes, true, "tracking_reporter_accepts_only_stop"},
        {contract::kOpcodeExpiringFrame, contract::kExpiringFramePrefixBytes, true,
         "tracking_reporter_accepts_only_stop"},
        {contract::kOpcodeStop, 4, true, "tracking_reporter_accepts_only_stop"},
        {0, 0, true, "tracking_reporter_accepts_only_stop"},
        {contract::kOpcodeStop, 0, false, "invalid_packet_magic"},
    };
    for (const auto& input : refused) {
        FakeIo io;
        io.input_at = 10 * ms;
        io.chunks = {packet_header(input.opcode, input.payload, input.magic)};
        assert(error_of([&] { run(io); }) == input.error);
    }
    FakeIo failing;
    failing.input_at = 10 * ms;
    failing.input_poll = -1;
    assert(error_of([&] { run(failing); }) == "stdin_poll_failed");
}

void bridge_glue_cases() {
    option_cases();
    runtime_event_cases();
    reporter_only_loop_cases();
    std::cout << "{\"case\":\"bridge_glue\",\"checks\":[\"options\",\"start_and_hook\",\"runtime_events\","
                 "\"reporter_only_loop\"]}\n";
}

int main() {
    static_assert(kZeroPoseBytes == 96, "two 3x4 float matrices");
    sha256_vectors();

    // The first state: universe id and the digest of both zero poses; nothing else is read.
    FakeSystem system;
    system.universe = 7;
    system.raw = kRaw;
    system.seated = kSeated;
    Recorder start;
    Reporter<FakeSystem> reporter(system);
    reporter.start(start);
    assert(start.events.size() == 1 && start.events[0].kind == EventKind::state);
    assert(start.events[0].universe_id == 7);
    assert(system.property_reads == 1 && system.pose_reads == 2);
    const auto bytes = zero_pose_bytes(kRaw, kSeated);
    assert(bytes.size() == kZeroPoseBytes);
    // Raw standing first, row-major, each float's bits little-endian: m[0][3] = 0.25f = 0x3e800000.
    assert(bytes[12] == 0x00 && bytes[13] == 0x00 && bytes[14] == 0x80 && bytes[15] == 0x3e);
    // Seated second: its m[0][2] = -1.0f = 0xbf800000 at byte 48 + 8.
    assert(bytes[56] == 0x00 && bytes[58] == 0x80 && bytes[59] == 0xbf);
    assert(start.events[0].zero_pose_sha256 == sha256_hex(bytes.data(), bytes.size()));
    assert(start.events[0].zero_pose_sha256 == kKnownDigest);
    assert(reporter.states == 1 && reporter.changes == 0 && reporter.unavailable == 0);
    print("start", start.events);

    // Each generated change id maps to its name, followed by a fresh state read after it.
    Recorder changes;
    const auto count = std::size(contract::kTrackingChangeEventIds);
    assert(count == std::size(contract::kTrackingChangeNames) && count == 4);
    for (std::size_t i = 0; i < count; ++i) {
        system.raw.m[1][3] += 1;  // The runtime moved its origin: the next state differs.
        const auto reads = system.property_reads;
        assert(reporter.event(contract::kTrackingChangeEventIds[i], changes));
        assert(system.property_reads == reads + 1);
        const auto& changed = changes.events[changes.events.size() - 2];
        const auto& state = changes.events.back();
        assert(changed.kind == EventKind::changed);
        assert(std::string(changed.change) == contract::kTrackingChangeNames[i]);
        assert(state.kind == EventKind::state && state.universe_id == 7);
        const auto moved = zero_pose_bytes(system.raw, system.seated);
        assert(state.zero_pose_sha256 == sha256_hex(moved.data(), moved.size()));
        assert(state.zero_pose_sha256 != kKnownDigest);
    }
    assert(changes.events.size() == 2 * count);
    assert(reporter.changes == count && reporter.states == 1 + count);
    print("changes", changes.events);

    // Any other event id emits nothing and reads nothing.
    Recorder ignored;
    const auto reads = system.property_reads, poses = system.pose_reads;
    for (const std::uint32_t id : {0U, 1U, 100U, 700U, 800U, 802U, 803U, 805U, 806U, 809U, 1000U,
                                   std::numeric_limits<std::uint32_t>::max()})
        assert(!reporter.event(id, ignored));
    assert(ignored.events.empty() && system.property_reads == reads && system.pose_reads == poses);
    print("ignored", ignored.events);

    // A property error, or universe id 0, is tracking_unavailable with a bounded reason; no pose is read.
    struct Failure { const char* name; std::uint64_t universe; std::uint32_t error; const char* reason; };
    const Failure failures[] = {{"property_error", 5, 7, kUniverseIdPropertyError},
                                {"property_error_and_zero", 0, 9, kUniverseIdPropertyError},
                                {"universe_zero", 0, 0, kUniverseIdZero}};
    for (const auto& [name, universe, error, reason] : failures) {
        FakeSystem failing;
        failing.universe = universe;
        failing.error = error;
        Recorder recorder;
        Reporter<FakeSystem> unavailable(failing);
        unavailable.start(recorder);
        assert(recorder.events.size() == 1 && recorder.events[0].kind == EventKind::unavailable);
        assert(std::string(recorder.events[0].reason) == reason);
        assert(recorder.events[0].property_error == error);
        assert(failing.pose_reads == 0 && unavailable.unavailable == 1 && unavailable.states == 0);
        // A later change still reports, and still reports unavailable.
        assert(unavailable.event(contract::kTrackingChangeEventIds[0], recorder));
        assert(recorder.events.size() == 3 && recorder.events[2].kind == EventKind::unavailable);
        print(name, recorder.events);
    }

    // Extremes: the largest universe id is a valid state; non-finite floats, signed zeros and
    // subnormals are digested by their bits, deterministically.
    FakeSystem extreme;
    extreme.universe = std::numeric_limits<std::uint64_t>::max();
    // 0x7f800001 and 0x7fa00000 are signaling NaNs, which CPython's float32 pack may quiet: the
    // digest keeps their bits, so the Python side checks it with struct.pack("<24I").
    constexpr std::uint32_t raw_bits[12] = {0x7fc00001, 0x7f800000, 0xff800000, 0x80000000, 0x00000001, 0x7f7fffff,
                                            0xff7fffff, 0x00800000, 0x3f800000, 0x7f800001, 0, 0xffffffff};
    constexpr std::uint32_t seated_bits[12] = {0x7fc00000, 0x7fa00000, 0, 0, 0, 0x3f800000, 0, 0, 0, 0, 0x3f800000, 0};
    extreme.raw = bits(raw_bits);
    extreme.seated = bits(seated_bits);
    const auto extreme_bytes = zero_pose_bytes(extreme.raw, extreme.seated);
    for (unsigned i = 0; i < 12; ++i)
        for (unsigned shift = 0; shift < 32; shift += 8) {
            assert(extreme_bytes[4 * i + shift / 8] == std::uint8_t(raw_bits[i] >> shift));
            assert(extreme_bytes[48 + 4 * i + shift / 8] == std::uint8_t(seated_bits[i] >> shift));
        }
    Recorder extremes;
    Reporter<FakeSystem> extreme_reporter(extreme);
    extreme_reporter.start(extremes);
    extreme_reporter.start(extremes);
    assert(extremes.events.size() == 2 && extremes.events[0].kind == EventKind::state);
    assert(extremes.events[0].universe_id == std::numeric_limits<std::uint64_t>::max());
    assert(extremes.events[0].zero_pose_sha256 == extremes.events[1].zero_pose_sha256);
    // Another NaN payload, or +0 for -0, is another digest.
    auto other = extreme;
    std::uint32_t quiet = 0x7fc00000, zero = 0;
    std::memcpy(&other.raw.m[0][0], &quiet, 4);
    std::memcpy(&other.raw.m[0][3], &zero, 4);
    const auto a = zero_pose_bytes(extreme.raw, extreme.seated), b = zero_pose_bytes(other.raw, extreme.seated);
    const auto c = zero_pose_bytes(extreme.raw, other.raw);
    assert(sha256_hex(a.data(), a.size()) != sha256_hex(b.data(), b.size()));
    assert(sha256_hex(a.data(), a.size()) != sha256_hex(c.data(), c.size()));
    extremes.events.pop_back();
    print("extremes", extremes.events);
    bridge_glue_cases();
    std::cout << std::flush;
    return std::cout ? 0 : 1;
}
