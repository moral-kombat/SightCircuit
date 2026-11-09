// Full-range YCbCr to BGR conversion in fixed point, shared by the CPU pixel
// path, the fake backend's expectations and (as the same integer formula, in
// GLSL) the GPU pixel path's compute shader. The camera frames are full-range
// YCbCr for every pixel format and encoding (AV1CameraIPCProtocol.h,
// CameraFrame::yCbCrEncoding), so there is no limited-range variant.
//
// For a sample maximum M (255 for 8-bit, 1023 for 10-bit) and half H = (M+1)/2:
//   R = Y + cr_r * (Cr - H),  G = Y + cb_g * (Cb - H) + cr_g * (Cr - H),
//   B = Y + cb_b * (Cb - H),
// with the coefficients in Q16 (2(1-Kr), -2(1-Kb)Kb/Kg, -2(1-Kr)Kr/Kg, 2(1-Kb),
// each multiplied by 65536 and rounded to the nearest integer), the sum rounded
// to the nearest integer (+32768 then an arithmetic shift of a non-negative
// value) and clamped to [0, M]. 10-bit results become 8-bit as
// (v * 255 + 511) / 1023. Chroma is taken at (x / 2, y / 2): no interpolation.
// C++11; no SDK types.
#pragma once
#include <cstdint>
#include <string>

namespace sightcircuit_camera {

const uint32_t kDrmFourccNv12 = 0x3231564eu;  // 'NV12'
const uint32_t kDrmFourccP010 = 0x30313050u;  // 'P010'
const uint64_t kDrmModifierLinear = 0;

enum class YcbcrEncoding { bt601, bt709, bt2020 };

struct YcbcrCoefficients {
    int32_t cr_r;
    int32_t cb_g;
    int32_t cr_g;
    int32_t cb_b;
};

// Kr, Kb: BT.601 0.299, 0.114; BT.709 0.2126, 0.0722; BT.2020 0.2627, 0.0593.
inline YcbcrCoefficients ycbcr_coefficients(YcbcrEncoding encoding) {
    YcbcrCoefficients c;
    switch (encoding) {
        case YcbcrEncoding::bt601: c.cr_r = 91881; c.cb_g = -22553; c.cr_g = -46802; c.cb_b = 116130; return c;
        case YcbcrEncoding::bt709: c.cr_r = 103206; c.cb_g = -12276; c.cr_g = -30679; c.cb_b = 121609; return c;
        case YcbcrEncoding::bt2020: c.cr_r = 96639; c.cb_g = -10784; c.cr_g = -37444; c.cb_b = 123299; return c;
    }
    c.cr_r = 103206; c.cb_g = -12276; c.cr_g = -30679; c.cb_b = 121609;
    return c;
}

inline const char* ycbcr_encoding_name(YcbcrEncoding encoding) {
    switch (encoding) {
        case YcbcrEncoding::bt601: return "bt601";
        case YcbcrEncoding::bt709: return "bt709";
        case YcbcrEncoding::bt2020: return "bt2020";
    }
    return "bt709";
}

// The helper's names for the SDK's YCbCrEncodingValue enumerators; "unknown" is refused.
inline bool parse_ycbcr_encoding(const std::string& name, YcbcrEncoding& encoding) {
    if (name == "bt601") { encoding = YcbcrEncoding::bt601; return true; }
    if (name == "bt709") { encoding = YcbcrEncoding::bt709; return true; }
    if (name == "bt2020") { encoding = YcbcrEncoding::bt2020; return true; }
    return false;
}

inline uint8_t ycbcr_channel(int64_t accumulator, uint32_t sample_max) {
    accumulator += 32768;
    if (accumulator < 0) return 0;
    uint32_t value = static_cast<uint32_t>(accumulator >> 16);
    if (value > sample_max) value = sample_max;
    if (sample_max == 255u) return static_cast<uint8_t>(value);
    return static_cast<uint8_t>((value * 255u + 511u) / 1023u);
}

// y, cb and cr are samples in [0, sample_max]; out receives B, G, R.
inline void ycbcr_to_bgr(uint32_t y, uint32_t cb, uint32_t cr, uint32_t sample_max, const YcbcrCoefficients& c,
                         uint8_t out[3]) {
    const int64_t half = (static_cast<int64_t>(sample_max) + 1) / 2;
    const int64_t d_cb = static_cast<int64_t>(cb) - half;
    const int64_t d_cr = static_cast<int64_t>(cr) - half;
    const int64_t luma = static_cast<int64_t>(y) << 16;
    out[0] = ycbcr_channel(luma + c.cb_b * d_cb, sample_max);
    out[1] = ycbcr_channel(luma + c.cb_g * d_cb + c.cr_g * d_cr, sample_max);
    out[2] = ycbcr_channel(luma + c.cr_r * d_cr, sample_max);
}

} // namespace sightcircuit_camera
