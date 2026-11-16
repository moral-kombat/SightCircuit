// Fake dispatch covers the exact decoder/retirement helpers used by OpenVR.
#include "../../tools/overlay_frame.hpp"
#include <cassert>
#include <string>

using namespace sightcircuit_overlay;
struct FakeOverlay {
    unsigned calls = 0;
    void frame(std::uint32_t w, std::uint32_t h, const void* data) {
        const auto* pixels = static_cast<const std::uint8_t*>(data);
        assert(w == 2 && h == 1);
        for (unsigned i = 0; i < 8; ++i) assert(pixels[i] == i + 101);
        ++calls;
    }
};
void append32(std::vector<std::uint8_t>& bytes, std::uint32_t n) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back((n >> shift) & 255);
}
int main() {
    constexpr std::uint64_t now = 0x123456789ULL;
    constexpr std::uint64_t deadline = now + 100000000;
    std::vector<std::uint8_t> payload;
    append32(payload, deadline & 0xffffffff); append32(payload, deadline >> 32);
    append32(payload, 2); append32(payload, 1);
    for (unsigned i = 0; i < 8; ++i) payload.push_back(i + 101);
    const auto frame = decode_frame(4, payload);
    assert(frame.expiry == deadline && frame.rgba == payload.data() + 16);
    assert(deadline_current(frame, now));
    assert(!deadline_current(frame, deadline));
    assert(!deadline_current(frame, deadline + 1));
    FakeOverlay fake;
    dispatch_frame(fake, frame);
    payload.erase(payload.begin(), payload.begin() + 8);
    const auto plain = decode_frame(1, payload);
    assert(plain.expiry == 0 && plain.rgba == payload.data() + 8);
    dispatch_frame(fake, plain);
    assert(fake.calls == 2);
    payload.pop_back();
    bool rejected = false;
    try { decode_frame(1, payload); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    // The generated display-age cap is accepted exactly and rejected one nanosecond later.
    constexpr auto cap = sightcircuit_contract::kMaxDisplayAgeNs;
    assert(deadline_current({1, 1, now + cap, nullptr}, now));
    for (auto expiry : {std::uint64_t{0}, now + cap + 1}) {
        rejected = false;
        try { deadline_current({1, 1, expiry, nullptr}, now); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
    // What each stream mode takes, decided on the packet header. Only validation and the fixed HUD take the
    // expiring opcode; the replay stream refuses it; the fixed HUD refuses settings and frames without a deadline.
    namespace contract = sightcircuit_contract;
    constexpr std::uint32_t hud_bytes = contract::kExpiringFramePrefixBytes +
        contract::kHudWidthPx*contract::kHudHeightPx*contract::kRgbaBytes;
    constexpr std::uint32_t limit = contract::kExpiringFramePrefixBytes +
        contract::kMaxSide*contract::kMaxSide*contract::kRgbaBytes;
    const auto refusal = [&](StreamMode mode, std::uint32_t kind, std::uint32_t size) {
        const char* name = packet_refusal(mode, kind, size, limit);
        return std::string(name ? name : "");
    };
    assert(!frame_expiry_supported(StreamMode::Replay));
    assert(frame_expiry_supported(StreamMode::Validation) && frame_expiry_supported(StreamMode::FixedHud));
    for (auto mode : {StreamMode::Replay, StreamMode::Validation, StreamMode::FixedHud}) {
        const bool fixed = mode == StreamMode::FixedHud, replay = mode == StreamMode::Replay;
        assert(refusal(mode, contract::kOpcodeStop, 0).empty());
        assert(refusal(mode, contract::kOpcodeVisibility, contract::kVisibilityBytes).empty());
        assert(refusal(mode, contract::kOpcodeExpiringFrame, hud_bytes) ==
               (replay ? "expiring_frames_validation_only" : ""));
        assert(refusal(mode, contract::kOpcodeSettings, contract::kSettingsBytes) ==
               (fixed ? "fixed_hud_refuses_settings" : ""));
        assert(refusal(mode, contract::kOpcodeFrame, contract::kFramePrefixBytes + 4) ==
               (fixed ? "fixed_hud_requires_expiring_frame" : ""));
        // Malformed headers keep their names in every mode, ahead of the mode's own refusals.
        assert(refusal(mode, 0, 0) == "invalid_packet_header" && refusal(mode, 6, 0) == "invalid_packet_header");
        assert(refusal(mode, contract::kOpcodeExpiringFrame, limit + 1) == "invalid_packet_header");
        assert(refusal(mode, contract::kOpcodeSettings, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeFrame, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeStop, 1) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeVisibility, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeExpiringFrame, 8) ==
               (fixed ? "invalid_packet_size" : "expiring_frames_validation_only"));
    }
    // The fixed HUD presents the contract's tile size only; the other modes keep every valid size.
    const FrameView tile{contract::kHudWidthPx, contract::kHudHeightPx, deadline, nullptr};
    check_frame_size(StreamMode::FixedHud, tile);
    for (const FrameView& other : {FrameView{contract::kHudHeightPx, contract::kHudWidthPx, deadline, nullptr},
                                   FrameView{contract::kHudWidthPx, contract::kHudHeightPx + 1, deadline, nullptr},
                                   FrameView{1, 1, deadline, nullptr}}) {
        check_frame_size(StreamMode::Replay, other);
        check_frame_size(StreamMode::Validation, other);
        rejected = false;
        try { check_frame_size(StreamMode::FixedHud, other); }
        catch (const std::runtime_error& error) { rejected = std::string(error.what()) == "fixed_hud_frame_size"; }
        assert(rejected);
    }
    // The presented frame's deadline. It falls due once, at the deadline and not a nanosecond before.
    FrameDeadline shown;
    assert(shown.due(now) == 0);
    assert(shown.admit(tile, true, now) && shown.expiry == deadline);
    assert(shown.due(deadline - 1) == 0 && shown.expiry == deadline);
    // A retransmission of the same frame, however late it is observed, leaves the deadline where it was.
    assert(shown.admit(tile, true, deadline - 1) && shown.expiry == deadline);
    assert(shown.due(deadline) == deadline && shown.expiry == 0);
    assert(shown.due(deadline + 1) == 0);
    // Once expired, the same frame is refused: never presented again, and no deadline is set.
    assert(!shown.admit(tile, true, deadline) && shown.expiry == 0);
    assert(!shown.admit(tile, true, deadline + cap) && shown.expiry == 0);
    // A frame that arrives expired leaves the presented frame's deadline alone.
    const FrameView next{contract::kHudWidthPx, contract::kHudHeightPx, deadline + 5, nullptr};
    assert(shown.admit(next, true, deadline) && shown.expiry == deadline + 5);
    assert(!shown.admit(tile, true, deadline + 1) && shown.expiry == deadline + 5);
    // No deadline further ahead of the observed time than the maximum display age is accepted, and the
    // refusal changes nothing; exactly the maximum is accepted.
    for (auto expiry : {std::uint64_t{0}, deadline + cap + 1}) {
        rejected = false;
        try { shown.admit({contract::kHudWidthPx, contract::kHudHeightPx, expiry, nullptr}, true, deadline); }
        catch (const std::runtime_error& error) { rejected = std::string(error.what()) == "invalid_frame_expiry"; }
        assert(rejected && shown.expiry == deadline + 5);
    }
    assert(shown.admit({contract::kHudWidthPx, contract::kHudHeightPx, deadline + cap, nullptr}, true, deadline));
    assert(shown.expiry == deadline + cap);
    // The replay opcode carries no deadline: admitted with none (modes that take it never expire it).
    assert(shown.admit(plain, false, now) && shown.expiry == 0 && shown.due(~std::uint64_t{0}) == 0);
    // Every failure position still attempts every teardown call, in order.
    for (unsigned fail = 0; fail <= 3; ++fail) {
        std::string calls;
        const auto retired = retire_overlay(
            [&] { calls += "hide "; return fail != 1; },
            [&] { calls += "clear "; return fail != 2; },
            [&] { calls += "destroy"; return fail != 3; });
        assert(calls == "hide clear destroy");
        assert(retired.ok() == (fail == 0));
    }
}
