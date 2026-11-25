// Shared packet decoding and teardown sequencing; no OpenVR or device access.
// Packet layout and limits come from the generated hud_contract.hpp.
#pragma once
#include "hud_contract.hpp"
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace sightcircuit_overlay {
inline std::uint32_t read_u32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
inline std::uint64_t read_u64(const std::uint8_t* p) {
    return std::uint64_t(read_u32(p)) | (std::uint64_t(read_u32(p + 4)) << 32);
}
struct FrameView {
    std::uint32_t width, height;
    std::uint64_t expiry;
    const std::uint8_t* rgba;
    // An observed frame (kOpcodeObservedFrame) also says which frame it is and when its source was observed.
    bool observed = false;
    std::uint64_t frame_id = 0, observed_ns = 0;
};
inline FrameView decode_frame(std::uint32_t kind, const std::vector<std::uint8_t>& payload) {
    namespace contract = sightcircuit_contract;
    if (kind != contract::kOpcodeFrame && kind != contract::kOpcodeExpiringFrame &&
            kind != contract::kOpcodeObservedFrame)
        throw std::runtime_error("invalid_frame_kind");
    const bool expiring = kind == contract::kOpcodeExpiringFrame, observed = kind == contract::kOpcodeObservedFrame;
    const unsigned pixels = observed ? contract::kObservedFramePrefixBytes :
                            expiring ? contract::kExpiringFramePrefixBytes : contract::kFramePrefixBytes;
    if (payload.size() < pixels) throw std::runtime_error("invalid_rgba_frame");
    const auto w = read_u32(payload.data() +
        (observed ? contract::kObservedFramePrefixWidthOffset :
         expiring ? contract::kExpiringFramePrefixWidthOffset : contract::kFramePrefixWidthOffset));
    const auto h = read_u32(payload.data() +
        (observed ? contract::kObservedFramePrefixHeightOffset :
         expiring ? contract::kExpiringFramePrefixHeightOffset : contract::kFramePrefixHeightOffset));
    if (!w || !h || w > contract::kMaxSide || h > contract::kMaxSide ||
            payload.size() != pixels + w*h*contract::kRgbaBytes)
        throw std::runtime_error("invalid_rgba_frame");
    FrameView frame{w, h, 0, payload.data() + pixels};
    if (expiring) frame.expiry = read_u64(payload.data() + contract::kExpiringFramePrefixExpiresNsOffset);
    if (observed) {
        frame.observed = true;
        frame.frame_id = read_u64(payload.data() + contract::kObservedFramePrefixFrameIdOffset);
        frame.observed_ns = read_u64(payload.data() + contract::kObservedFramePrefixObservedNsOffset);
        frame.expiry = read_u64(payload.data() + contract::kObservedFramePrefixExpiresNsOffset);
    }
    return frame;
}
inline bool deadline_current(const FrameView& frame, std::uint64_t now) {
    if (!frame.expiry || frame.expiry > now + sightcircuit_contract::kMaxDisplayAgeNs)
        throw std::runtime_error("invalid_frame_expiry");
    return frame.expiry > now;
}
// The three modes that read the stream protocol, and what each accepts. A deadline is honoured only where
// the bridge itself clears the tile at it: validation, which presents nothing, and the fixed HUD, the one
// real-output mode with expiry. The replay stream (--stdin) keeps refusing every frame with a deadline.
enum class StreamMode { Replay, Validation, FixedHud };
inline bool frame_expiry_supported(StreamMode mode) { return mode != StreamMode::Replay; }
// The largest payload any packet may announce: the longest frame prefix and the largest frame.
constexpr std::uint32_t kMaxPayloadBytes = sightcircuit_contract::kObservedFramePrefixBytes +
    sightcircuit_contract::kMaxSide*sightcircuit_contract::kMaxSide*sightcircuit_contract::kRgbaBytes;
static_assert(sightcircuit_contract::kObservedFramePrefixBytes >= sightcircuit_contract::kExpiringFramePrefixBytes &&
              sightcircuit_contract::kObservedFramePrefixBytes >= sightcircuit_contract::kFramePrefixBytes);
// The named refusal of a packet header in this mode, or nullptr when the packet is taken. The fixed HUD's
// placement is compiled in and each of its frames must carry its observation time, identity and deadline, so
// it takes the observed frame alone: no settings packet, no frame without a deadline, and no expiring frame
// of the validation layout, whose deadline the bridge could not tie to an observation.
inline const char* packet_refusal(StreamMode mode, std::uint32_t kind, std::uint32_t size) {
    namespace contract = sightcircuit_contract;
    if (size > kMaxPayloadBytes || (kind != contract::kOpcodeFrame && kind != contract::kOpcodeSettings &&
                                    kind != contract::kOpcodeStop && kind != contract::kOpcodeExpiringFrame &&
                                    kind != contract::kOpcodeVisibility && kind != contract::kOpcodeObservedFrame))
        return "invalid_packet_header";
    if ((kind == contract::kOpcodeSettings && size != contract::kSettingsBytes) ||
            (kind == contract::kOpcodeStop && size) ||
            (kind == contract::kOpcodeVisibility && size != contract::kVisibilityBytes) ||
            (kind == contract::kOpcodeFrame && size < contract::kFramePrefixBytes))
        return "invalid_packet_size";
    if (mode == StreamMode::FixedHud) {
        if (kind == contract::kOpcodeSettings) return "fixed_hud_refuses_settings";
        if (kind == contract::kOpcodeFrame || kind == contract::kOpcodeExpiringFrame)
            return "fixed_hud_requires_observed_frame";
    }
    // The validation stream keeps the name it has always given a short expiring packet.
    if (kind == contract::kOpcodeExpiringFrame &&
            (!frame_expiry_supported(mode) || size < contract::kExpiringFramePrefixBytes))
        return "expiring_frames_validation_only";
    if (kind == contract::kOpcodeObservedFrame) {
        if (!frame_expiry_supported(mode)) return "observed_frames_fixed_hud_or_validation_only";
        if (size < contract::kObservedFramePrefixBytes) return "invalid_packet_size";
    }
    return nullptr;
}
// The fixed geometries a run can be pinned to (work-order item B3): each a tile size and a head-fixed placement
// from the generated contract, the strip first. A layout changes what size of frame is taken, where the overlay
// is placed and what the report names; the packet rules, the deadline, the acknowledgements, the hide and the
// watchdogs are the same code for every layout and never read it.
struct FixedLayout {
    const char* id;
    const char* digest;
    std::uint32_t width, height;
    float panel_width_m, distance_m, x_m, y_m, opacity;
};
constexpr FixedLayout kFixedLayouts[] = {
    {sightcircuit_contract::kHudLayoutId, sightcircuit_contract::kHudConfigDigest,
     sightcircuit_contract::kHudWidthPx, sightcircuit_contract::kHudHeightPx,
     sightcircuit_contract::kHudPanelWidthM, sightcircuit_contract::kHudDistanceM,
     sightcircuit_contract::kHudXM, sightcircuit_contract::kHudYM, sightcircuit_contract::kHudOpacity},
    {sightcircuit_contract::kBoxPanelLayoutId, sightcircuit_contract::kBoxPanelConfigDigest,
     sightcircuit_contract::kBoxPanelWidthPx, sightcircuit_contract::kBoxPanelHeightPx,
     sightcircuit_contract::kBoxPanelPanelWidthM, sightcircuit_contract::kBoxPanelDistanceM,
     sightcircuit_contract::kBoxPanelXM, sightcircuit_contract::kBoxPanelYM, sightcircuit_contract::kBoxPanelOpacity},
};
constexpr int kFixedLayoutCount = int(sizeof kFixedLayouts / sizeof kFixedLayouts[0]);
constexpr int kStripLayout = 0, kNoLayout = -1;
constexpr bool layout_within_limits(const FixedLayout& layout) {
    namespace contract = sightcircuit_contract;
    return layout.width >= 1 && layout.width <= contract::kMaxSide &&
           layout.height >= 1 && layout.height <= contract::kMaxSide &&
           layout.panel_width_m >= contract::kPanelWidthMin && layout.panel_width_m <= contract::kPanelWidthMax &&
           layout.distance_m >= contract::kPanelDistanceMin && layout.distance_m <= contract::kPanelDistanceMax &&
           layout.x_m >= contract::kPanelXMin && layout.x_m <= contract::kPanelXMax &&
           layout.y_m >= contract::kPanelYMin && layout.y_m <= contract::kPanelYMax &&
           layout.opacity >= contract::kPanelOpacityMin && layout.opacity <= contract::kPanelOpacityMax;
}
constexpr bool every_layout_within_limits() {
    for (const FixedLayout& layout : kFixedLayouts)
        if (!layout_within_limits(layout)) return false;
    return true;
}
// A table with any layout outside the limits every settings packet is held to does not compile.
static_assert(every_layout_within_limits());
// The index of the layout named `id`, or kNoLayout.
inline int layout_index(const char* id) {
    for (int index = 0; index < kFixedLayoutCount; ++index)
        if (!std::strcmp(id, kFixedLayouts[index].id)) return index;
    return kNoLayout;
}
// The layout this process was launched with ("--layout ID"): written once by the entry point, before the first
// packet is read, and only read afterwards. kNoLayout when none was named.
inline int pinned_layout = kNoLayout;
// The layout whose size rule applies: the named one; without a name the strip in the fixed HUD and none in the
// other streams, as before layouts existed.
inline const FixedLayout* layout_in_force(StreamMode mode) {
    if (pinned_layout != kNoLayout) return &kFixedLayouts[pinned_layout];
    return mode == StreamMode::FixedHud ? &kFixedLayouts[kStripLayout] : nullptr;
}
// A run pinned to a layout presents that layout's tile and nothing else. A run that named its layout also says
// when a refused frame has exactly the size of another layout: its sender drew for the wrong one.
inline void check_frame_size(StreamMode mode, const FrameView& frame) {
    const FixedLayout* layout = layout_in_force(mode);
    if (!layout || (frame.width == layout->width && frame.height == layout->height)) return;
    if (pinned_layout != kNoLayout)
        for (const FixedLayout& other : kFixedLayouts)
            if (frame.width == other.width && frame.height == other.height)
                throw std::runtime_error("frame_for_other_layout");
    throw std::runtime_error("fixed_hud_frame_size");
}
// What becomes of an arriving frame.
enum class Arrival {
    Present,         // may be presented; its deadline is committed once its pixels are
    Expired,         // its deadline had passed when the bridge observed it: refused, never presented
    Stale,           // older than the frame already taken (a lower identity or an earlier observation): refused
    Retransmission,  // the frame already taken, sent again unchanged: ignored, its deadline stays where it was
};
// The deadline of the presented frame, and the identity and observation time of the last frame taken.
//
// A frame is judged when the bridge observes it and committed only once its pixels are presented, so a
// slow upload can never leave a deadline standing for pixels that are not up, nor hide a tile's expiry
// behind its successor's deadline.
//
// An observed frame carries its identity and the time its source was observed. Its deadline may not be later
// than that observation plus the contract's maximum display age, its observation may not lie in the future
// (beyond a small tolerance) and neither its identity nor its observation may go backwards. A deadline
// is therefore renewed only by a frame with a newer identity, and never beyond one maximum display age after
// what that frame says it observed. The same identity again is a retransmission and changes nothing; the
// same identity with another observation or deadline is a contradiction and ends the run.
//
// The validation layout (kOpcodeExpiringFrame) carries a deadline alone; the bound there is one maximum
// display age from arrival, as it has always been.
struct FrameDeadline {
    std::uint64_t expiry = 0;  // of the presented frame; zero while nothing with a deadline is presented
    bool identified = false;   // an observed frame has been taken; the three values below are its own
    std::uint64_t frame_id = 0, observed_ns = 0, frame_expiry = 0;

    Arrival judge(const FrameView& frame, bool expiring, std::uint64_t now) const {
        namespace contract = sightcircuit_contract;
        if (!expiring) return Arrival::Present;
        if (!frame.observed) return deadline_current(frame, now) ? Arrival::Present : Arrival::Expired;
        if (!frame.expiry || frame.expiry <= frame.observed_ns ||
                frame.expiry - frame.observed_ns > contract::kMaxDisplayAgeNs)
            throw std::runtime_error("invalid_frame_expiry");
        if (frame.observed_ns > now && frame.observed_ns - now > contract::kObservationFutureToleranceNs)
            throw std::runtime_error("frame_observed_in_future");
        if (identified && frame.frame_id == frame_id) {
            if (frame.observed_ns != observed_ns || frame.expiry != frame_expiry)
                throw std::runtime_error("frame_identity_changed");
            return Arrival::Retransmission;
        }
        if (identified && (frame.frame_id < frame_id || frame.observed_ns < observed_ns)) return Arrival::Stale;
        return frame.expiry > now ? Arrival::Present : Arrival::Expired;
    }
    // The frame's pixels are presented: its deadline now stands. A frame without a deadline (the replay
    // opcode) clears it.
    void commit(const FrameView& frame, bool expiring) {
        expiry = expiring ? frame.expiry : 0;
        if (frame.observed) {
            identified = true;
            frame_id = frame.frame_id; observed_ns = frame.observed_ns; frame_expiry = frame.expiry;
        }
    }
    // The deadline that has passed at `now`, once (it is cleared), or zero.
    std::uint64_t due(std::uint64_t now) {
        if (!expiry || now < expiry) return 0;
        const auto passed = expiry;
        expiry = 0;
        return passed;
    }
    // Whether `passed` (a value due() returned) was the deadline of the identified frame.
    bool names(std::uint64_t passed) const { return identified && passed == frame_expiry; }
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
