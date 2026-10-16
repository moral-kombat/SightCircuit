// Public OpenVR overlay bridge. Never requests a tracked-camera interface.
#include <openvr.h>
#include "json/json.h"
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::uint32_t kMaxSide = 512, kMaxPayload = 8 + kMaxSide*kMaxSide*4;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
struct Stopped {};
void emit(Json::Value value) {
    value["report_kind"] = "framejacker_overlay_bridge";
    value["schema_version"] = 1;
    std::cout << Json::FastWriter().write(value) << std::flush;
}
std::uint32_t u32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1])<<8) |
           (std::uint32_t(p[2])<<16) | (std::uint32_t(p[3])<<24);
}
float f32(const std::uint8_t* p) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
    const auto bits = u32(p);
    float result;
    std::memcpy(&result, &bits, 4);
    return result;
}
struct Settings {
    float width = .8f, distance = 1.5f, x = 0, y = -.2f, alpha = .95f;
    bool visible = true;
};
void bounded(float value, float low, float high) {
    if (!std::isfinite(value) || value < low || value > high)
        throw std::runtime_error("panel_setting_out_of_range");
}
Settings settings(const std::vector<std::uint8_t>& b) {
    if (b.size() != 24) throw std::runtime_error("invalid_settings_size");
    Settings s{f32(b.data()), f32(b.data()+4), f32(b.data()+8),
               f32(b.data()+12), f32(b.data()+16), u32(b.data()+20) != 0};
    bounded(s.width,.25f,1.5f); bounded(s.distance,.5f,3.f);
    bounded(s.x,-1.f,1.f); bounded(s.y,-1.f,1.f); bounded(s.alpha,.2f,1.f);
    if (u32(b.data()+20) > 1) throw std::runtime_error("invalid_visibility");
    return s;
}
class Overlay {
public:
    vr::IVRSystem* system = nullptr;
    vr::IVROverlay* api = nullptr;
    vr::VROverlayHandle_t handle = vr::k_ulOverlayHandleInvalid;
    bool active = false, has_image = false;
    Settings config;
    ~Overlay() { close(); }
    void check(vr::EVROverlayError e, const char* op) {
        if (e != vr::VROverlayError_None)
            throw std::runtime_error(std::string(op)+":"+api->GetOverlayErrorNameFromEnum(e));
    }
    void init() {
        vr::EVRInitError error = vr::VRInitError_None;
        system = vr::VR_Init(&error, vr::VRApplication_Overlay);
        active = error == vr::VRInitError_None && system;
        if (!active) throw std::runtime_error(std::string("VR_Init:")+vr::VR_GetVRInitErrorAsSymbol(error));
        api = vr::VROverlay();
        if (!api) throw std::runtime_error("IVROverlay_028_unavailable");
        if (!system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd))
            throw std::runtime_error("headset_not_connected");
        const auto key = std::string("framejacker.replay.")+std::to_string(getpid());
        check(api->CreateOverlay(key.c_str(), "Framejacker replay", &handle), "CreateOverlay");
        check(api->SetOverlayInputMethod(handle, vr::VROverlayInputMethod_None), "SetOverlayInputMethod");
        check(api->SetOverlayTextureColorSpace(handle, vr::ColorSpace_Gamma), "SetOverlayTextureColorSpace");
        configure(config);
    }
    void configure(const Settings& s) {
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
    void visibility() {
        if (config.visible && has_image) check(api->ShowOverlay(handle), "ShowOverlay");
        else check(api->HideOverlay(handle), "HideOverlay");
    }
    void frame(std::uint32_t w, std::uint32_t h, void* rgba) {
        check(api->SetOverlayRaw(handle,rgba,w,h,4), "SetOverlayRaw");
        has_image = true;
        visibility();
    }
    void events() {
        if (!system) return;
        vr::VREvent_t e{};
        while (system->PollNextEvent(&e,sizeof(e))) {
            if (e.eventType == vr::VREvent_Quit) {
                system->AcknowledgeQuit_Exiting(); throw Stopped{};
            }
        }
        while (api->PollNextOverlayEvent(handle,&e,sizeof(e))) {
            if (e.eventType == vr::VREvent_Quit || e.eventType == vr::VREvent_OverlayClosed) throw Stopped{};
            if (e.eventType == vr::VREvent_ImageFailed) throw std::runtime_error("overlay_image_failed");
        }
    }
    bool close() noexcept {
        bool ok = true;
        if (api && handle != vr::k_ulOverlayHandleInvalid) {
            ok = api->HideOverlay(handle) == vr::VROverlayError_None;
            ok = (api->DestroyOverlay(handle) == vr::VROverlayError_None) && ok;
            handle = vr::k_ulOverlayHandleInvalid;
        }
        if (active) { vr::VR_Shutdown(); active = false; }
        system = nullptr; api = nullptr;
        return ok;
    }
};
bool read_exact(std::uint8_t* data, std::size_t size, bool eof_allowed, Overlay& overlay) {
    const auto deadline = Clock::now()+std::chrono::seconds(eof_allowed ? 10 : 3);
    std::size_t received = 0;
    while (received < size) {
        if (stopping) throw Stopped{};
        overlay.events();
        if (Clock::now() >= deadline) throw std::runtime_error("stdin_timeout");
        pollfd input{STDIN_FILENO,POLLIN,0};
        const auto available = poll(&input,1,20);
        if (available < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("stdin_poll_failed");
        }
        if (!available) continue;
        const auto count = read(STDIN_FILENO,data+received,size-received);
        if (count < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("stdin_read_failed");
        }
        if (!count) {
            if (eof_allowed && !received) return false;
            throw std::runtime_error("truncated_packet");
        }
        received += static_cast<std::size_t>(count);
    }
    return true;
}
void stream(Overlay& overlay, bool validate, Json::Value& report) {
    std::array<std::uint8_t,12> header{};
    while (read_exact(header.data(),header.size(),true,overlay)) {
        if (std::memcmp(header.data(),"FJO1",4)) throw std::runtime_error("invalid_packet_magic");
        const auto kind = u32(header.data()+4), size = u32(header.data()+8);
        if (size > kMaxPayload || kind < 1 || kind > 3) throw std::runtime_error("invalid_packet_header");
        if ((kind == 2 && size != 24) || (kind == 3 && size) || (kind == 1 && size < 8))
            throw std::runtime_error("invalid_packet_size");
        std::vector<std::uint8_t> payload(size);
        if (size) read_exact(payload.data(),size,false,overlay);
        if (kind == 3) break;
        if (kind == 2) {
            const auto s = settings(payload);
            if (!validate) overlay.configure(s);
            report["settings_received"] = report["settings_received"].asUInt()+1;
        } else {
            const auto w = u32(payload.data()), h = u32(payload.data()+4);
            if (!w || !h || w > kMaxSide || h > kMaxSide || size != 8+w*h*4)
                throw std::runtime_error("invalid_rgba_frame");
            report["frames_received"] = report["frames_received"].asUInt()+1;
            if (!validate) overlay.frame(w,h,payload.data()+8);
            if (!validate) report["frames_submitted"] = report["frames_received"];
            if (report["frames_received"].asUInt() == 1) {
                Json::Value first;
                first["event"] = validate ? "frame_validated" : "frame_submitted";
                first["width"] = w; first["height"] = h;
                emit(first);
            }
        }
    }
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
} // namespace

int main(int argc, char* argv[]) {
    const std::string mode=argc>1 ? argv[1] : "";
    if (mode=="--help" && argc==2) {
        std::cout << "Usage: framejacker-overlay-bridge --test-panel [--seconds 1..30]\n"
                     "       framejacker-overlay-bridge --stdin\n"
                     "       framejacker-overlay-bridge --validate-stdin (no OpenVR calls)\n";
        return 0;
    }
    Json::Value report;
    report["event"]="closed"; report["runtime_initialized"]=false;
    report["frames_received"]=0U; report["frames_submitted"]=0U; report["settings_received"]=0U;
    report["mode"]=mode;
    int seconds=10;
    try {
        if (mode=="--test-panel" && argc==4 && std::string(argv[2])=="--seconds") {
            std::size_t used=0;
            seconds=std::stoi(argv[3],&used);
            if (used!=std::strlen(argv[3]) || seconds<1 || seconds>30) throw std::runtime_error("seconds_out_of_range");
        } else if (!(argc==2 && (mode=="--test-panel" || mode=="--stdin" || mode=="--validate-stdin"))) {
            throw std::runtime_error("Choose --test-panel or --stdin; --help shows usage");
        }
    } catch (const std::exception& error) {
        report["event"]=argc==1 ? "not_run" : "error";
        report["error"]=error.what(); report["exit_code"]=argc==1 ? 3 : 64;
        emit(report); return argc==1 ? 3 : 64;
    }
    std::signal(SIGINT,stop_signal); std::signal(SIGTERM,stop_signal); std::signal(SIGPIPE,SIG_IGN);
    const auto parent=getppid();
    if (prctl(PR_SET_PDEATHSIG,SIGTERM)!=0) {
        report["event"]="error"; report["error"]="parent_death_signal_unavailable";
        report["exit_code"]=1; emit(report); return 1;
    }
    if (getppid()!=parent || parent==1) stopping=1;
    Overlay overlay;
    int exit_code=0;
    try {
        if (stopping) throw Stopped{};
        const bool validate=mode=="--validate-stdin";
        if (!validate) overlay.init();
        Json::Value ready;
        ready["event"]="ready"; ready["validation_only"]=validate;
        ready["runtime_initialized"]=!validate; ready["overlay_interface"]=vr::IVROverlay_Version;
        emit(ready);
        if (mode=="--test-panel") {
            auto pixels=pattern();
            overlay.frame(256,128,pixels.data());
            report["frames_submitted"]=1U;
            Json::Value shown;
            shown["event"]="test_panel_submitted"; shown["seconds"]=seconds;
            shown["api_visible"]=overlay.api->IsOverlayVisible(overlay.handle);
            emit(shown);
            const auto deadline=Clock::now()+std::chrono::seconds(seconds);
            while (Clock::now()<deadline) {
                if (stopping) throw Stopped{};
                overlay.events(); std::this_thread::sleep_for(std::chrono::milliseconds(16));
            }
        } else stream(overlay,validate,report);
        report["status"]=validate ? "validated" : "closed";
    } catch (const Stopped&) {
        report["status"]="stopped";
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error";
        report["error"]=error.what(); exit_code=1;
    }
    report["runtime_initialized"]=overlay.active;
    const bool ok=overlay.close();
    report["cleanup_ok"]=ok;
    if (!ok) { report["status"]="cleanup_failure"; exit_code=1; }
    report["exit_code"]=exit_code;
    emit(report);
    return exit_code;
}
