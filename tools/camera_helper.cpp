// Camera helper: the entry point. Argument parsing and the validate, report
// sequence live here; the reporting unit and the one linked backend unit hold
// the rest (camera_helper.hpp). This unit includes no SDK header and reaches
// a camera only through the Backend interface, which has no pixel path.
//
// Revision 1 (lane G, step 1) has the validation mode only. --validate
// initialises nothing: no client object, no socket, no camera; it checks the
// arguments, parses the fake script when one is named, and writes one report.
#include "camera_helper.hpp"
#include <csignal>
#include <cstring>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <unistd.h>

using namespace sightcircuit_camera;

namespace {
constexpr unsigned kWatchdogValidateSeconds = 5;
constexpr unsigned kWatchdogReportSeconds = 5;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
void watchdog(const std::string& mode, const char* phase, unsigned seconds) {
    if (!lifecycle::set_watchdog(watchdog_line(mode, phase, seconds), seconds))
        throw std::runtime_error("watchdog_arm_failed");
}
int fail(Json& report, const char* error, int exit_code) {
    report.set("event", "error"); report.set("status", "error");
    report.set("error", error); report.set("exit_code", exit_code);
    emit(report);
    return exit_code;
}
} // namespace

int main(int argc, char* argv[]) {
    // Before anything else: keep the real stdout for the protocol, fd 1 goes to stderr.
    if (!lifecycle::isolate_stdout()) {
        Json failure = Json::object();
        failure.set("event", "error"); failure.set("status", "error");
        failure.set("error", "stdout_isolation_failed"); failure.set("exit_code", 1);
        emit(failure); return 1;
    }
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "--help" && argc == 2) return lifecycle::write_protocol(usage_text()) ? 0 : 1;
    Json report = base_report(mode);
    BackendOptions options;
    try {
        if (argc == 1) throw std::runtime_error("choose_a_mode");
        if (mode != "--validate") throw std::runtime_error("unknown_mode");
        for (int i = 2; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument == "--fake-script" && i + 1 < argc && options.fake_script.empty() && argv[i + 1][0]) {
                options.fake_script = argv[++i];
            } else throw std::runtime_error("unknown_or_repeated_argument");
        }
    } catch (const std::exception& error) {
        report.set("event", argc == 1 ? "not_run" : "error");
        report.set("status", argc == 1 ? "not_run" : "error");
        report.set("error", error.what()); report.set("exit_code", argc == 1 ? 3 : 64);
        emit(report); return argc == 1 ? 3 : 64;
    }
    std::signal(SIGINT, stop_signal); std::signal(SIGTERM, stop_signal); std::signal(SIGPIPE, SIG_IGN);
    if (!lifecycle::install_watchdog()) return fail(report, "watchdog_unavailable", 1);
    const auto parent = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) return fail(report, "parent_death_signal_unavailable", 1);
    if (getppid() != parent || parent == 1) stopping = 1;
    int exit_code = 0;
    try {
        watchdog(mode, "validate", kWatchdogValidateSeconds);
        if (stopping) throw std::runtime_error("stopped_before_validation");
        std::string error;
        Json checked = Json::object();
        checked.set("fake_script", options.fake_script.empty() ? Json() : Json(options.fake_script));
        if (!validate_backend_options(options, checked, error)) throw std::runtime_error(error);
        report.set("validated", checked);
        report.set("status", "validated");
    } catch (const std::exception& error) {
        report.set("event", "error"); report.set("status", "error");
        report.set("error", error.what()); exit_code = 1;
    }
    report.set("exit_code", exit_code);
    // The final write is bounded too: a parent that stopped draining the pipe
    // would otherwise hold the helper here with the alarm cleared.
    try { watchdog(mode, "report", kWatchdogReportSeconds); } catch (const std::exception&) {}
    emit(report);
    lifecycle::disarm_watchdog();
    return exit_code;
}
