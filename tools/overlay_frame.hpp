// Shared packet decoding and teardown sequencing; no OpenVR or device access.
// Packet layout and limits come from the generated hud_contract.hpp.
#pragma once
#include "hud_contract.hpp"
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace framejacker_overlay {
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
    namespace contract = framejacker_contract;
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
    if (!frame.expiry || frame.expiry > now + framejacker_contract::kMaxDisplayAgeNs)
        throw std::runtime_error("invalid_frame_expiry");
    return frame.expiry > now;
}
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
} // namespace framejacker_overlay
