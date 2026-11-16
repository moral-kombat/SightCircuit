// Shared packet decoding and teardown sequencing; no OpenVR or device access.
// Packet layout and limits come from the generated hud_contract.hpp.
#pragma once
#include "hud_contract.hpp"
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace sightcircuit_overlay {
inline std::uint32_t read_u32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
struct FrameView {
    std::uint32_t width, height;
    std::uint64_t expiry;
    const std::uint8_t* rgba;
};
inline FrameView decode_frame(std::uint32_t kind, const std::vector<std::uint8_t>& payload) {
    namespace contract = sightcircuit_contract;
    if (kind != contract::kOpcodeFrame && kind != contract::kOpcodeExpiringFrame)
        throw std::runtime_error("invalid_frame_kind");
    const bool expiring = kind == contract::kOpcodeExpiringFrame;
    const unsigned pixels = expiring ? contract::kExpiringFramePrefixBytes : contract::kFramePrefixBytes;
    if (payload.size() < pixels) throw std::runtime_error("invalid_rgba_frame");
    const auto w = read_u32(payload.data() +
        (expiring ? contract::kExpiringFramePrefixWidthOffset : contract::kFramePrefixWidthOffset));
    const auto h = read_u32(payload.data() +
        (expiring ? contract::kExpiringFramePrefixHeightOffset : contract::kFramePrefixHeightOffset));
    if (!w || !h || w > contract::kMaxSide || h > contract::kMaxSide ||
            payload.size() != pixels + w*h*contract::kRgbaBytes)
        throw std::runtime_error("invalid_rgba_frame");
    const auto* deadline = payload.data() + contract::kExpiringFramePrefixExpiresNsOffset;
    const auto expiry = expiring ? std::uint64_t(read_u32(deadline)) |
        (std::uint64_t(read_u32(deadline + 4)) << 32) : 0;
    return {w, h, expiry, payload.data() + pixels};
}
inline bool deadline_current(const FrameView& frame, std::uint64_t now) {
    if (!frame.expiry || frame.expiry > now + sightcircuit_contract::kMaxDisplayAgeNs)
        throw std::runtime_error("invalid_frame_expiry");
    return frame.expiry > now;
}
// The three modes that read the stream protocol, and what each accepts. A deadline is honoured only where
// the bridge itself clears the tile at it: validation, which presents nothing, and the fixed HUD, the one
// real-output mode with expiry. The replay stream (--stdin) keeps refusing the expiring opcode.
enum class StreamMode { Replay, Validation, FixedHud };
inline bool frame_expiry_supported(StreamMode mode) { return mode != StreamMode::Replay; }
// The named refusal of a packet header in this mode, or nullptr when the packet is taken. The fixed HUD's
// placement is compiled in and every one of its frames carries a deadline, so it takes neither a settings
// packet nor a frame without one.
inline const char* packet_refusal(StreamMode mode, std::uint32_t kind, std::uint32_t size, std::uint32_t max_payload) {
    namespace contract = sightcircuit_contract;
    if (size > max_payload || (kind != contract::kOpcodeFrame && kind != contract::kOpcodeSettings &&
                               kind != contract::kOpcodeStop && kind != contract::kOpcodeExpiringFrame &&
                               kind != contract::kOpcodeVisibility))
        return "invalid_packet_header";
    if ((kind == contract::kOpcodeSettings && size != contract::kSettingsBytes) ||
            (kind == contract::kOpcodeStop && size) ||
            (kind == contract::kOpcodeVisibility && size != contract::kVisibilityBytes) ||
            (kind == contract::kOpcodeFrame && size < contract::kFramePrefixBytes))
        return "invalid_packet_size";
    if (kind == contract::kOpcodeExpiringFrame &&
            (!frame_expiry_supported(mode) || size < contract::kExpiringFramePrefixBytes))
        // The validation stream keeps the name it has always given a short expiring packet.
        return mode == StreamMode::FixedHud ? "invalid_packet_size" : "expiring_frames_validation_only";
    if (mode == StreamMode::FixedHud && kind == contract::kOpcodeSettings) return "fixed_hud_refuses_settings";
    if (mode == StreamMode::FixedHud && kind == contract::kOpcodeFrame) return "fixed_hud_requires_expiring_frame";
    return nullptr;
}
// The fixed HUD presents the contract's tile and nothing else.
inline void check_frame_size(StreamMode mode, const FrameView& frame) {
    if (mode == StreamMode::FixedHud && (frame.width != sightcircuit_contract::kHudWidthPx ||
                                         frame.height != sightcircuit_contract::kHudHeightPx))
        throw std::runtime_error("fixed_hud_frame_size");
}
// The deadline of the presented frame; zero while nothing with a deadline is presented. The deadline is the
// frame's identity on the wire: a retransmission carries the deadline it was first sent with, so admitting it
// again leaves the deadline where it was, and once that deadline has passed the same packet is refused as
// expired. Nothing but a frame with a later deadline of its own moves it, and none may lie further ahead of
// the time the bridge observes the frame than the contract's maximum display age.
struct FrameDeadline {
    std::uint64_t expiry = 0;
    // An arriving frame, observed at `now`: true when it may be presented and its deadline is kept, false when
    // it arrived already expired (refused: never presented, and the presented frame keeps its own deadline).
    // A zero or over-long deadline throws invalid_frame_expiry. A frame without a deadline (the replay
    // opcode) clears it.
    bool admit(const FrameView& frame, bool expiring, std::uint64_t now) {
        if (expiring && !deadline_current(frame, now)) return false;
        expiry = expiring ? frame.expiry : 0;
        return true;
    }
    // The deadline that has passed at `now`, once (it is cleared), or zero.
    std::uint64_t due(std::uint64_t now) {
        if (!expiry || now < expiry) return 0;
        const auto passed = expiry;
        expiry = 0;
        return passed;
    }
};
template <class Target> void dispatch_frame(Target& target, const FrameView& frame) {
    target.frame(frame.width, frame.height, frame.rgba);
}
struct Retirement {
    bool hidden = false, texture_cleared = false, destroyed = false;
    bool ok() const { return hidden && texture_cleared && destroyed; }
};
template <class Hide, class Clear, class Destroy>
Retirement retire_overlay(Hide hide, Clear clear, Destroy destroy) {
    // A failed earlier call must not prevent later cleanup attempts.
    Retirement result;
    result.hidden = hide();
    result.texture_cleared = clear();
    result.destroyed = destroy();
    return result;
}
} // namespace sightcircuit_overlay
