// Internal interface of the overlay bridge: the state and declarations its
// units share. overlay_bridge.cpp holds main() (argument parsing and the
// connect, run, cleanup, report sequence); the four units are
//   overlay_bridge_lifecycle.cpp  the stop flag, the watchdog phases, connect and close
//   overlay_bridge_rendering.cpp  settings decoding, the test and marker images, upload, show and hide
//   overlay_bridge_reporting.cpp  every protocol line: events, the watchdog message, the final report
//   overlay_bridge_sensing.cpp    the clock, the stdin protocol, runtime events and visibility queries
// Each Overlay member below names the unit that defines it. Shared helpers with
// no OpenVR dependency stay in overlay_lifecycle.hpp and overlay_frame.hpp;
// every value of the HUD contract comes from the generated hud_contract.hpp.
// Never requests a tracked-camera interface.
#pragma once
#include <openvr.h>
#include "json/json.h"
#include "hud_contract.hpp"
#include "overlay_frame.hpp"
#include "overlay_lifecycle.hpp"
#include <chrono>
#include <csignal>
#include <cstdint>
#include <string>
#include <vector>

namespace sightcircuit_bridge {
namespace contract = sightcircuit_contract;
namespace lifecycle = sightcircuit_lifecycle;
using sightcircuit_overlay::read_u32;
using Clock = std::chrono::steady_clock;
constexpr std::uint32_t kMaxPayload =
    contract::kExpiringFramePrefixBytes + contract::kMaxSide*contract::kMaxSide*contract::kRgbaBytes;
static_assert(sizeof contract::kPacketMagic - 1 ==
              contract::kPacketHeaderOpcodeOffset - contract::kPacketHeaderMagicOffset);
constexpr bool kCalibrationVisibleInDashboard = true;
extern volatile std::sig_atomic_t stopping;
void stop_signal(int);
struct Stopped {};
// Watchdog budgets beside sightcircuit/runtime/guard.py, which still owns
// escalation. The connect budget comes from the generated contract header, and
// the Python clients derive their ready wait from the same value, so the two
// move together (BRIDGE_READY_WAIT_SECONDS in sightcircuit/hud/contract.py).
constexpr unsigned kWatchdogConnectSeconds = contract::kBridgeConnectBudgetSeconds;
constexpr unsigned kWatchdogStallSeconds = 5;  // any one loop step, re-armed per step
constexpr unsigned kWatchdogCleanupSeconds = 5;

struct Settings {
    float width = contract::kPanelDefaultWidth, distance = contract::kPanelDefaultDistance,
          x = contract::kPanelDefaultX, y = contract::kPanelDefaultY, alpha = contract::kPanelDefaultOpacity;
    bool visible = contract::kPanelDefaultVisible;
};

// The one overlay and its connection. The state is shared; the member
// functions are defined in the unit named beside each group.
class Overlay {
public:
    vr::IVRSystem* system = nullptr;
    vr::IVROverlay* api = nullptr;
    vr::VROverlayHandle_t handle = vr::k_ulOverlayHandleInvalid;
    bool active = false, probe_active = false, has_image = false;
    bool retirement_attempted = false, shutdown_returned = false;
    sightcircuit_overlay::Retirement retirement;
    bool overlay_shown_event_seen = false, overlay_hidden_event_seen = false;
    std::uint64_t calibration_show_deadline_ns = 0;
    Settings config;
    // The one visibility request (stream mode, opcode visibility) whose runtime event is awaited, and
    // whether this run is validation-only (reported on every visibility line). Fallback item F1.
    bool validation_only = false;
    bool visibility_pending = false, visibility_pending_hide = false;
    std::uint32_t visibility_cycle = 0;
    std::uint64_t visibility_request_ns = 0;
    std::uint32_t visibility_events_missing = 0;
    ~Overlay() { close(); }
    // Lifecycle: connect, error names and close.
    void check(vr::EVROverlayError e, const char* op);
    static std::string init_error(vr::EVRInitError error, const void* interface);
    void init(bool calibration, bool background_only, Json::Value& report);
    bool close() noexcept;
    // Rendering: placement, show and hide, upload.
    void configure(const Settings& s);
    void visibility();
    void frame(std::uint32_t w, std::uint32_t h, const void* rgba);
    // Sensing: runtime events, visibility queries and the read-only view record.
    void events();
    void calibration_visibility(Json::Value& sample);
    void view_record(Json::Value& connect, Json::Value& record);
    // Sensing: the awaited visibility event of a cycle. await_visibility() settles a still pending request
    // first; settle_visibility() reports a hide whose hidden event has not been polled within the bound
    // (or at all, with force) as visibility_event_missing, once, and forgets a pending show silently.
    void await_visibility(std::uint32_t cycle, bool hide, std::uint64_t request_ns);
    void settle_visibility(std::uint64_t now, bool force = false);
};

// Lifecycle (overlay_bridge_lifecycle.cpp): arm a watchdog phase with its
// prepared report line.
void watchdog(const std::string& mode, const char* phase, unsigned seconds);

// Rendering (overlay_bridge_rendering.cpp).
Settings settings(const std::vector<std::uint8_t>& b);
Settings marker_settings();
std::vector<std::uint8_t> pattern();
std::vector<std::uint8_t> calibration_marker(Json::Value& description, const Settings& applied);
int render_marker(int argc, char* argv[]);

// Reporting (overlay_bridge_reporting.cpp): the protocol lines and the report
// fields of each phase. Only emit() writes a JSON line.
void emit(Json::Value value);
std::string watchdog_line(const std::string& mode, const char* phase, unsigned seconds);
std::string usage_text();
Json::Value closed_report(const std::string& mode);
void describe_marker_run(Json::Value& report, int seconds, std::uint64_t start_ns, std::uint64_t deadline_ns);
void describe_connect(Json::Value& report, bool view_record, bool validate, bool background_only);
void emit_ready(const std::string& mode, bool validate, bool marker, std::uint64_t marker_deadline_ns);
void emit_test_panel_submitted(int seconds, bool api_visible);
void emit_frame_expired(std::uint64_t deadline_ns, std::uint64_t observed_ns, bool validate);
void emit_first_frame(bool validate, std::uint32_t width, std::uint32_t height);
// The visibility lines of one fallback cycle (hud-fallback.md "Bridge events"): hide_requested or
// show_requested, hide_returned or show_returned, overlay_hidden or overlay_shown, visibility_event_missing.
void emit_visibility_requested(bool hide, std::uint32_t cycle, std::uint64_t request_ns, bool validate);
void emit_visibility_returned(bool hide, std::uint32_t cycle, std::uint64_t returned_ns, bool ok, bool validate);
void emit_visibility_event(bool hide, std::uint32_t cycle, std::uint64_t observed_ns, bool validate);
void emit_visibility_event_missing(std::uint32_t cycle, std::uint64_t waited_ns, bool validate);
void record_run_end(Json::Value& report, const Overlay& overlay, bool marker, std::uint64_t marker_deadline_ns);
int record_cleanup(Json::Value& report, const Overlay& overlay, bool marker, bool watchdog_armed, bool ok,
                   int exit_code);

// Sensing (overlay_bridge_sensing.cpp): the clock every reported timestamp
// reads, and the stdin protocol loop.
std::uint64_t monotonic_ns();
void stream(Overlay& overlay, bool validate, Json::Value& report);
} // namespace sightcircuit_bridge
