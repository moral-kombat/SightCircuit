// Overlay bridge, rendering unit: settings decoding and bounds, the test
// pattern and the calibration marker, the SetOverlayRaw upload, show and hide,
// and the validation-only --render-calibration-marker mode. Every size,
// position, gray level, corner id, limit and default comes from the generated
// contract header; this unit keeps no copy of those values.
#include "overlay_bridge.hpp"
#include <array>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <unistd.h>

namespace sightcircuit_bridge {
namespace {
float f32(const std::uint8_t* p) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
    const auto bits = read_u32(p);
    float result;
    std::memcpy(&result, &bits, 4);
    return result;
}
void bounded(float value, float low, float high) {
    if (!std::isfinite(value) || value < low || value > high)
        throw std::runtime_error("panel_setting_out_of_range");
}
void write_new_file(const char* path, const std::vector<std::uint8_t>& bytes) {
    // Exclusive creation: never replaces or follows an existing path.
    const int fd=::open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if (fd<0) throw std::runtime_error("render_output_open_failed");
    std::size_t done=0;
    while (done<bytes.size()) {
        const auto count=::write(fd,bytes.data()+done,bytes.size()-done);
        if (count<0 && errno==EINTR) continue;
        if (count<=0) { ::close(fd); ::unlink(path); throw std::runtime_error("render_output_write_failed"); }
        done+=static_cast<std::size_t>(count);
    }
    if (::close(fd)) { ::unlink(path); throw std::runtime_error("render_output_close_failed"); }
}
} // namespace

Settings settings(const std::vector<std::uint8_t>& b) {
    if (b.size() != contract::kSettingsBytes) throw std::runtime_error("invalid_settings_size");
    const auto visible = read_u32(b.data()+contract::kSettingsVisibleOffset);
    Settings s{f32(b.data()+contract::kSettingsWidthOffset), f32(b.data()+contract::kSettingsDistanceOffset),
               f32(b.data()+contract::kSettingsXOffset), f32(b.data()+contract::kSettingsYOffset),
               f32(b.data()+contract::kSettingsOpacityOffset), visible != 0};
    bounded(s.width, contract::kPanelWidthMin, contract::kPanelWidthMax);
    bounded(s.distance, contract::kPanelDistanceMin, contract::kPanelDistanceMax);
    bounded(s.x, contract::kPanelXMin, contract::kPanelXMax);
    bounded(s.y, contract::kPanelYMin, contract::kPanelYMax);
    bounded(s.alpha, contract::kPanelOpacityMin, contract::kPanelOpacityMax);
    if (visible > 1) throw std::runtime_error("invalid_visibility");
    return s;
}
Settings marker_settings() {
    // The fixed HUD placement; the marker never takes settings from stdin.
    return Settings{contract::kHudPanelWidthM, contract::kHudDistanceM, contract::kHudXM, contract::kHudYM,
                    contract::kHudOpacity, true};
}
void Overlay::configure(const Settings& s) {
    config = s;
    vr::HmdMatrix34_t matrix{};
    matrix.m[0][0] = matrix.m[1][1] = matrix.m[2][2] = 1;
    matrix.m[0][3] = s.x; matrix.m[1][3] = s.y; matrix.m[2][3] = -s.distance;
    check(api->SetOverlayWidthInMeters(handle,s.width), "SetOverlayWidthInMeters");
    check(api->SetOverlayAlpha(handle,s.alpha), "SetOverlayAlpha");
    check(api->SetOverlayTransformTrackedDeviceRelative(handle,vr::k_unTrackedDeviceIndex_Hmd,&matrix),
          "SetOverlayTransformTrackedDeviceRelative");
    visibility();
}
void Overlay::visibility() {
    if (config.visible && has_image) {
        // A late SetOverlayRaw return must not initiate a new ShowOverlay.
        // A call that never returns still requires external containment.
        if (calibration_show_deadline_ns && monotonic_ns()>=calibration_show_deadline_ns)
            throw std::runtime_error("calibration_deadline_before_show");
        check(api->ShowOverlay(handle), "ShowOverlay");
    }
    else check(api->HideOverlay(handle), "HideOverlay");
}
void Overlay::frame(std::uint32_t w, std::uint32_t h, const void* rgba) {
    check(api->SetOverlayRaw(handle,const_cast<void*>(rgba),w,h,4), "SetOverlayRaw");
    has_image = true;
    visibility();
}
std::vector<std::uint8_t> pattern() {
    constexpr unsigned w=256,h=128;
    std::vector<std::uint8_t> image(w*h*4,255);
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) {
        const auto p=(y*w+x)*4;
        image[p]=x<w/2 ? (y<h/2 ? 220 : 40) : (y<h/2 ? 40 : 220);
        image[p+1]=x<w/2 ? 40 : 220;
        image[p+2]=y<h/2 ? 40 : (x<w/2 ? 220 : 40);
        if (x%32==0 || y%32==0) image[p]=image[p+1]=image[p+2]=100;
    }
    const std::array<std::array<unsigned,7>,3> letters{{
        {{31,4,4,4,4,4,4}},{{14,17,17,17,17,17,14}},{{30,17,17,30,16,16,16}}
    }};
    for (unsigned c=0;c<3;++c) for (unsigned y=0;y<7;++y) for (unsigned x=0;x<5;++x)
        if (letters[c][y] & (1U<<(4-x))) for (unsigned dy=0;dy<3;++dy) for (unsigned dx=0;dx<3;++dx) {
            const auto p=(((y*3+8+dy)*w)+(c*18+x*3+102+dx))*4;
            image[p]=image[p+1]=image[p+2]=255;
        }
    return image;
}

std::vector<std::uint8_t> calibration_marker(Json::Value& description, const Settings& applied) {
    // Every size, position, gray level and corner id comes from the generated
    // contract, which sightcircuit.hud.observation.marker_template also uses.
    constexpr unsigned w=contract::kHudWidthPx, h=contract::kHudHeightPx;
    constexpr unsigned border=contract::kMarkerBorderPx, size=contract::kMarkerTagSizePx;
    constexpr unsigned inset=contract::kMarkerTagInsetPx, outline=contract::kMarkerTagOutlinePx;
    constexpr unsigned bit_size=contract::kMarkerBitSizePx, columns=contract::kMarkerBitColumns;
    static_assert(contract::kMarkerBorderValue<=255 && contract::kMarkerBackgroundValue<=255 &&
                  contract::kMarkerTagOutlineValue<=255 && contract::kMarkerTagFillValue<=255 &&
                  contract::kMarkerBitSetValue<=255 && contract::kMarkerBitClearValue<=255);
    std::vector<std::uint8_t> pixels(w*h*contract::kRgbaBytes,255);
    const auto paint = [&](unsigned x, unsigned y, std::uint8_t value) {
        const auto p=(y*w+x)*contract::kRgbaBytes;
        pixels[p]=pixels[p+1]=pixels[p+2]=value;
    };
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x)
        paint(x,y,(x<border || y<border || x>=w-border || y>=h-border) ?
              contract::kMarkerBorderValue : contract::kMarkerBackgroundValue);
    // Four distinct binary corner tags; the outer border marks the full texture.
    // This is a generated marker, not an automatic eye/layout detector.
    for (unsigned tag=0;tag<std::size(contract::kMarkerCornerIds);++tag) {
        const unsigned x0=tag%2 ? w-inset-size : inset, y0=tag/2 ? h-inset-size : inset;
        for (unsigned y=0;y<size;++y) for (unsigned x=0;x<size;++x)
            paint(x0+x,y0+y,(x<outline || y<outline || x>=size-outline || y>=size-outline) ?
                  contract::kMarkerTagOutlineValue : contract::kMarkerTagFillValue);
        unsigned decoded=0;
        for (unsigned bit=0;bit<contract::kMarkerBits;++bit) {
            const unsigned x=x0+contract::kMarkerBitOffsetPx+(bit%columns)*contract::kMarkerBitPitchPx;
            const unsigned y=y0+contract::kMarkerBitOffsetPx+(bit/columns)*contract::kMarkerBitPitchPx;
            const bool set=(contract::kMarkerCornerIds[tag]>>bit)&1U;
            for (unsigned dy=0;dy<bit_size;++dy) for (unsigned dx=0;dx<bit_size;++dx)
                paint(x+dx,y+dy,set ? contract::kMarkerBitSetValue : contract::kMarkerBitClearValue);
            if (pixels[(y*w+x)*contract::kRgbaBytes]==contract::kMarkerBitSetValue) decoded|=1U<<bit;
        }
        description["corner_ids"].append(decoded);
    }
    unsigned opaque=0;
    for (std::size_t i=3;i<pixels.size();i+=contract::kRgbaBytes) opaque+=pixels[i]==255;
    description["width"]=w; description["height"]=h;
    description["opaque_pixels"]=opaque; description["border_pixels"]=border;
    description["tag_size_pixels"]=size; description["tag_inset_pixels"]=inset;
    description["corner_order"]=contract::kMarkerCornerOrder;
    description["config_id"]=contract::kHudLayoutId;
    description["visible_in_dashboard"]=kCalibrationVisibleInDashboard;
    description["panel_width_m"]=applied.width; description["distance_m"]=applied.distance;
    description["x_m"]=applied.x; description["y_m"]=applied.y; description["opacity"]=applied.alpha;
    return pixels;
}
int render_marker(int argc, char* argv[]) {
    // Validation only: draw the marker into a new RGBA file; no OpenVR call.
    Json::Value report;
    report["event"]="closed"; report["mode"]="--render-calibration-marker";
    report["validation_only"]=true; report["runtime_initialized"]=false;
    report["contract_digest"]=contract::kContractDigest; report["config_digest"]=contract::kHudConfigDigest;
    report["physical_display_recovery_verified"]=false;
    if (argc!=3 || !*argv[2]) {
        report["event"]="error"; report["status"]="error";
        report["error"]="render_requires_one_new_output_path"; report["exit_code"]=64;
        emit(report); return 64;
    }
    int exit_code=0;
    try {
        const auto pixels=calibration_marker(report["marker"],marker_settings());
        write_new_file(argv[2],pixels);
        report["status"]="rendered"; report["rgba_bytes"]=Json::UInt64(pixels.size());
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error"; report["error"]=error.what(); exit_code=1;
    }
    report["exit_code"]=exit_code;
    emit(report);
    return exit_code;
}
} // namespace sightcircuit_bridge
