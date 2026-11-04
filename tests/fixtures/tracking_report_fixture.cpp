// Fake dispatch for the bridge's tracking reporter (tools/tracking_report.hpp): a fake
// system stands in for vr::IVRSystem, so no OpenVR header or runtime is involved.
// It asserts the SHA-256 vectors and the event mapping, then prints one JSON line per
// case ({"case": ..., "events": [...]}) for tests/test_overlay_bridge.py to feed to
// a real TrackingObserver and to compare with hashlib.
#include "../../tools/tracking_report.hpp"
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <string>
#include <type_traits>
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
// hashlib.sha256(struct.pack("<24f", *raw rows, *seated rows)).hexdigest()
constexpr char kKnownDigest[] = "89f843370bf3cd06a4ab10d278d1571ca7e7823674cefabbe1c396f2b5caa6cf";

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
    constexpr std::uint32_t raw_bits[12] = {0x7fc00001, 0x7f800000, 0xff800000, 0x80000000, 0x00000001, 0x7f7fffff,
                                            0xff7fffff, 0x00800000, 0x3f800000, 0, 0, 0xffffffff};
    constexpr std::uint32_t seated_bits[12] = {0x7fc00000, 0, 0, 0, 0, 0x3f800000, 0, 0, 0, 0, 0x3f800000, 0};
    extreme.raw = bits(raw_bits);
    extreme.seated = bits(seated_bits);
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
    std::cout << std::flush;
    return std::cout ? 0 : 1;
}
