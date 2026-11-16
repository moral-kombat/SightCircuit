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
    // What each stream mode takes, decided on the packet header. Only validation and the fixed HUD take a frame
    // with a deadline; the replay stream refuses both layouts; the fixed HUD takes the observed frame alone.
    namespace contract = sightcircuit_contract;
    constexpr std::uint32_t pixels = contract::kHudWidthPx*contract::kHudHeightPx*contract::kRgbaBytes;
    const auto refusal = [&](StreamMode mode, std::uint32_t kind, std::uint32_t size) {
        const char* name = packet_refusal(mode, kind, size);
        return std::string(name ? name : "");
    };
    assert(!frame_expiry_supported(StreamMode::Replay));
    assert(frame_expiry_supported(StreamMode::Validation) && frame_expiry_supported(StreamMode::FixedHud));
    for (auto mode : {StreamMode::Replay, StreamMode::Validation, StreamMode::FixedHud}) {
        const bool fixed = mode == StreamMode::FixedHud, replay = mode == StreamMode::Replay;
        assert(refusal(mode, contract::kOpcodeStop, 0).empty());
        assert(refusal(mode, contract::kOpcodeVisibility, contract::kVisibilityBytes).empty());
        assert(refusal(mode, contract::kOpcodeObservedFrame, contract::kObservedFramePrefixBytes + pixels) ==
               (replay ? "observed_frames_fixed_hud_or_validation_only" : ""));
        assert(refusal(mode, contract::kOpcodeExpiringFrame, contract::kExpiringFramePrefixBytes + pixels) ==
               (replay ? "expiring_frames_validation_only" : fixed ? "fixed_hud_requires_observed_frame" : ""));
        assert(refusal(mode, contract::kOpcodeSettings, contract::kSettingsBytes) ==
               (fixed ? "fixed_hud_refuses_settings" : ""));
        assert(refusal(mode, contract::kOpcodeFrame, contract::kFramePrefixBytes + 4) ==
               (fixed ? "fixed_hud_requires_observed_frame" : ""));
        // Malformed headers keep their names in every mode, ahead of the mode's own refusals.
        assert(refusal(mode, 0, 0) == "invalid_packet_header" && refusal(mode, 7, 0) == "invalid_packet_header");
        assert(refusal(mode, contract::kOpcodeObservedFrame, kMaxPayloadBytes + 1) == "invalid_packet_header");
        assert(refusal(mode, contract::kOpcodeSettings, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeFrame, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeStop, 1) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeVisibility, 4) == "invalid_packet_size");
        assert(refusal(mode, contract::kOpcodeExpiringFrame, 8) ==
               (fixed ? "fixed_hud_requires_observed_frame" : "expiring_frames_validation_only"));
        assert(refusal(mode, contract::kOpcodeObservedFrame, 8) ==
               (replay ? "observed_frames_fixed_hud_or_validation_only" : "invalid_packet_size"));
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
    // The observed frame's prefix decodes field by field.
    std::vector<std::uint8_t> observed_payload;
    const auto append64 = [&](std::uint64_t n) { append32(observed_payload, n & 0xffffffff); append32(observed_payload, n >> 32); };
    append64(0x1122334455667788ULL); append64(now - 5); append64(deadline);
    append32(observed_payload, 1); append32(observed_payload, 1);
    for (unsigned i = 0; i < 4; ++i) observed_payload.push_back(i);
    const auto decoded = decode_frame(contract::kOpcodeObservedFrame, observed_payload);
    assert(decoded.observed && decoded.frame_id == 0x1122334455667788ULL && decoded.observed_ns == now - 5);
    assert(decoded.expiry == deadline && decoded.width == 1 && decoded.rgba == observed_payload.data() + 32);
    assert(!frame.observed && !plain.observed);

    // The validation layout: judged against arrival, committed only when presented, due once and not early.
    FrameDeadline shown;
    assert(shown.due(now) == 0);
    assert(shown.judge(tile, true, now) == Arrival::Present && shown.expiry == 0);  // judging commits nothing
    shown.commit(tile, true);
    assert(shown.expiry == deadline && !shown.identified);
    assert(shown.due(deadline - 1) == 0 && shown.expiry == deadline);
    assert(shown.judge(tile, true, deadline - 1) == Arrival::Present);  // the same packet again: the same deadline
    shown.commit(tile, true);
    assert(shown.due(deadline) == deadline && shown.expiry == 0 && shown.due(deadline + 1) == 0);
    assert(shown.judge(tile, true, deadline) == Arrival::Expired && shown.expiry == 0);
    for (auto expiry : {std::uint64_t{0}, deadline + cap + 1}) {
        rejected = false;
        try { shown.judge({contract::kHudWidthPx, contract::kHudHeightPx, expiry, nullptr}, true, deadline); }
        catch (const std::runtime_error& error) { rejected = std::string(error.what()) == "invalid_frame_expiry"; }
        assert(rejected);
    }
    // The replay opcode carries no deadline: committed with none.
    assert(shown.judge(plain, false, now) == Arrival::Present);
    shown.commit(plain, false);
    assert(shown.expiry == 0 && shown.due(~std::uint64_t{0}) == 0);

    // Observed frames: identity, observation time and deadline together.
    const auto observed = [&](std::uint64_t id, std::uint64_t seen, std::uint64_t expiry) {
        FrameView view{contract::kHudWidthPx, contract::kHudHeightPx, expiry, nullptr};
        view.observed = true; view.frame_id = id; view.observed_ns = seen;
        return view;
    };
    const auto thrown = [&](const FrameDeadline& state, const FrameView& view, std::uint64_t at) {
        try { state.judge(view, true, at); } catch (const std::runtime_error& error) { return std::string(error.what()); }
        return std::string();
    };
    constexpr auto tolerance = contract::kObservationFutureToleranceNs;
    FrameDeadline hud;
    // The deadline is bounded by the observation, not by the arrival: observed + cap exactly, never a nanosecond more,
    // however recently the frame arrived; and never at or before the observation itself.
    assert(hud.judge(observed(1, now, now + cap), true, now) == Arrival::Present);
    assert(thrown(hud, observed(1, now, now + cap + 1), now) == "invalid_frame_expiry");
    assert(thrown(hud, observed(1, now - cap, now + 1), now) == "invalid_frame_expiry");  // an old observation, restamped
    assert(thrown(hud, observed(1, now, now), now) == "invalid_frame_expiry");
    assert(thrown(hud, observed(1, now, 0), now) == "invalid_frame_expiry");
    // An observation in the future is refused beyond the tolerance, accepted within it.
    assert(hud.judge(observed(1, now + tolerance, now + tolerance + 5), true, now) == Arrival::Present);
    assert(thrown(hud, observed(1, now + tolerance + 1, now + tolerance + 5), now) == "frame_observed_in_future");
    // An old observation whose deadline has passed is expired on arrival.
    assert(hud.judge(observed(1, now - cap, now - 1), true, now) == Arrival::Expired && !hud.identified);
    const auto first = observed(10, now, now + 300);
    hud.commit(first, true);
    assert(hud.identified && hud.expiry == now + 300 && hud.frame_id == 10 && hud.observed_ns == now);
    assert(hud.names(now + 300) && !hud.names(now + 301));
    // The same frame again is a retransmission, before its deadline and after it; it never moves the deadline.
    assert(hud.judge(first, true, now + 100) == Arrival::Retransmission && hud.expiry == now + 300);
    // The same identity with a later deadline or another observation is a contradiction, not a renewal.
    assert(thrown(hud, observed(10, now, now + 900), now + 100) == "frame_identity_changed");
    assert(thrown(hud, observed(10, now + 50, now + 300), now + 100) == "frame_identity_changed");
    assert(hud.due(now + 300) == now + 300 && hud.expiry == 0);
    assert(hud.judge(first, true, now + 400) == Arrival::Retransmission);
    // A lower identity or an earlier observation is stale, whatever its deadline.
    assert(hud.judge(observed(9, now + 10, now + 900), true, now + 100) == Arrival::Stale);
    assert(hud.judge(observed(11, now - 1, now + 900), true, now + 100) == Arrival::Stale);
    // A renewed deadline needs a newer identity, and reaches no further than its own observation allows.
    assert(thrown(hud, observed(11, now, now + cap + 200), now + 200) == "invalid_frame_expiry");
    const auto second = observed(11, now + 200, now + 200 + cap);
    assert(hud.judge(second, true, now + 250) == Arrival::Present);
    hud.commit(second, true);
    assert(hud.expiry == now + 200 + cap && hud.frame_id == 11);
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
