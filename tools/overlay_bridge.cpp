// Public OpenVR overlay bridge: the entry point. Argument parsing and the
// connect, run, cleanup, report sequence live here; the lifecycle, rendering,
// reporting and sensing units (overlay_bridge_*.cpp, declared in
// overlay_bridge.hpp) hold the rest. Never requests a tracked-camera interface.
#include "overlay_bridge.hpp"
#include <chrono>
#include <csignal>
#include <cstring>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <thread>
#include <unistd.h>

using namespace sightcircuit_bridge;

int main(int argc, char* argv[]) {
    // Before anything can load the SDK: keep the real stdout for the protocol.
    if (!lifecycle::isolate_stdout()) {
        Json::Value failure;
        failure["event"]="error"; failure["status"]="error"; failure["error"]="stdout_isolation_failed";
        failure["runtime_initialized"]=false; failure["exit_code"]=1; emit(failure); return 1;
    }
    const std::string mode=argc>1 ? argv[1] : "";
    if (mode=="--help" && argc==2) return lifecycle::write_protocol(usage_text()) ? 0 : 1;
    if (mode=="--render-calibration-marker") return render_marker(argc,argv);
    Json::Value report=closed_report(mode);
    const bool marker=mode=="--calibration-marker" || mode=="--validate-calibration-marker";
    const bool validate=mode=="--validate-stdin" || mode=="--validate-calibration-marker";
    const bool view_record=mode=="--headset-view-record";
    const bool runtime_mode=mode=="--test-panel" || mode=="--stdin" || mode=="--calibration-marker";
    bool background_only=false;
    int seconds=10;
    try {
        // A trailing "--connect-as overlay|background" is read only for the runtime modes.
        if (argc>=4 && std::string(argv[argc-2])=="--connect-as") {
            const std::string how=argv[argc-1];
            if (!runtime_mode || (how!="overlay" && how!="background"))
                throw std::runtime_error("connect_as_requires_runtime_mode_and_overlay_or_background");
            background_only=how=="background";
            argc-=2;
        }
        if (view_record) {
            if (argc!=3 || std::string(argv[2])!="--allow-runtime-query")
                throw std::runtime_error("view_record_requires_explicit_opt_in");
        } else if (marker) {
            if (argc!=(validate ? 4 : 5) || std::string(argv[2])!="--seconds" ||
                    (!validate && std::string(argv[4])!="--allow-calibration-output"))
                throw std::runtime_error("calibration_requires_separate_explicit_opt_in");
            std::size_t used=0;
            seconds=std::stoi(argv[3],&used);
            if (used!=std::strlen(argv[3]) || seconds<contract::kMarkerMinSeconds || seconds>contract::kMarkerMaxSeconds)
                throw std::runtime_error("seconds_out_of_range");
        } else if (mode=="--test-panel" && argc==4 && std::string(argv[2])=="--seconds") {
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
    if (!lifecycle::install_watchdog()) {
        report["event"]="error"; report["error"]="watchdog_unavailable";
        report["exit_code"]=1; emit(report); return 1;
    }
    const auto parent=getppid();
    if (prctl(PR_SET_PDEATHSIG,SIGTERM)!=0) {
        report["event"]="error"; report["error"]="parent_death_signal_unavailable";
        report["exit_code"]=1; emit(report); return 1;
    }
    if (getppid()!=parent || parent==1) stopping=1;
    Overlay overlay;
    int exit_code=0;
    const auto marker_start=marker ? monotonic_ns() : 0;
    const auto marker_deadline=marker_start+std::uint64_t(seconds)*1000000000ULL;
    if (marker) {
        overlay.config=marker_settings();
        overlay.calibration_show_deadline_ns=marker_deadline;
        overlay.validation_only=validate;
        describe_marker_run(report,seconds,marker_start,marker_deadline);
    }
    describe_connect(report,view_record,validate,background_only);
    try {
        watchdog(mode,"connect",kWatchdogConnectSeconds);
        if (stopping) throw Stopped{};
        if (view_record) {
            overlay.view_record(report["connect"],report["view_record"]);
            report["status"]="view_recorded";
            throw Stopped{};  // Nothing to stream: go straight to cleanup.
        }
        if (!validate) overlay.init(marker,background_only,report["connect"]);
        watchdog(mode,"run",kWatchdogStallSeconds);
        if (marker && monotonic_ns()>=marker_deadline)
            throw std::runtime_error("calibration_deadline_before_marker");
        if (stopping) throw Stopped{};
        emit_ready(mode,validate,marker,marker_deadline);
        if (marker) {
            auto pixels=calibration_marker(report["marker"],overlay.config);
            report["generated_marker_frames"]=1U;
            if (stopping) throw Stopped{};
            if (monotonic_ns()>=marker_deadline) throw std::runtime_error("calibration_deadline_before_marker");
            report["marker_request_ns"]=Json::UInt64(monotonic_ns());
            if (!validate) {
                overlay.frame(contract::kHudWidthPx,contract::kHudHeightPx,pixels.data());
                report["frames_submitted"]=1U;
            }
            report["marker_submit_returned_ns"]=Json::UInt64(monotonic_ns());
            if (monotonic_ns()>=marker_deadline) throw std::runtime_error("calibration_submit_overran_deadline");
            if (!validate) overlay.calibration_visibility(report["visibility_after_submit"]);
            // The display timer stays the authority. Beside it, stdin is polled for the visibility requests
            // of fallback cycles (F3a); a run that receives none is the marker run the observer has always had.
            MarkerInput input;
            while (monotonic_ns()<marker_deadline) {
                lifecycle::rearm_watchdog(kWatchdogStallSeconds);
                if (stopping) throw Stopped{};
                overlay.events();
                overlay.settle_visibility(monotonic_ns());
                report["visibility_events_missing"]=overlay.visibility_events_missing;
                if (monotonic_ns()>=marker_deadline) break;
                input.poll_input(overlay,validate,report);
            }
            // The timer ended with a request still pending: it is reported missing, fail closed.
            overlay.settle_visibility(monotonic_ns(), true);
            report["visibility_events_missing"]=overlay.visibility_events_missing;
        } else if (mode=="--test-panel") {
            auto pixels=pattern();
            overlay.frame(256,128,pixels.data());
            report["frames_submitted"]=1U;
            emit_test_panel_submitted(seconds,overlay.api->IsOverlayVisible(overlay.handle));
            const auto deadline=Clock::now()+std::chrono::seconds(seconds);
            while (Clock::now()<deadline) {
                lifecycle::rearm_watchdog(kWatchdogStallSeconds);
                if (stopping) throw Stopped{};
                overlay.events(); std::this_thread::sleep_for(std::chrono::milliseconds(16));
            }
        } else stream(overlay,validate,report);
        report["status"]=marker ? (validate ? "validated_marker" : "marker_complete") : (validate ? "validated" : "closed");
    } catch (const Stopped&) {
        if (!view_record || report["status"]!="view_recorded") report["status"]="stopped";
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error";
        report["error"]=error.what(); exit_code=1;
    }
    record_run_end(report,overlay,marker,marker_deadline);
    bool watchdog_armed=true;
    try { watchdog(mode,"cleanup",kWatchdogCleanupSeconds); }
    catch (const std::exception&) { watchdog_armed=false; }
    const bool ok=overlay.close();
    lifecycle::disarm_watchdog();
    exit_code=record_cleanup(report,overlay,marker,watchdog_armed,ok,exit_code);
    // The final write is bounded too: a parent that stopped draining the pipe
    // would otherwise hold the bridge here after cleanup, with the alarm cleared.
    try { watchdog(mode,"report",kWatchdogCleanupSeconds); } catch (const std::exception&) {}
    emit(report);
    lifecycle::disarm_watchdog();
    return exit_code;
}
