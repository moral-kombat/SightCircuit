// Fake lifecycle checks for the exact helpers the overlay bridge uses: stdout
// isolation, the SIGALRM watchdog and the Background-then-Overlay connect order.
// No OpenVR: probe and connect are scripted lambdas.
#include "../../tools/overlay_lifecycle.hpp"
#include <cassert>
#include <cstdio>
#include <string>
#include <utility>
#include <vector>

using namespace framejacker_lifecycle;
namespace {
struct Script {
    std::vector<std::string> probes, connects;  // results consumed in order
    unsigned stop_after_calls = 1000;
    std::vector<std::string> calls{};
    std::vector<unsigned> waits{};
    Script(std::vector<std::string> p, std::vector<std::string> c, unsigned stop = 1000)
        : probes(std::move(p)), connects(std::move(c)), stop_after_calls(stop) {}
    ConnectResult run() {
        std::size_t p = 0, c = 0;
        return connect_overlay(
            [&] { calls.push_back("probe"); return probes.at(p++); },
            [&] { calls.push_back("shutdown_probe"); },
            [&] { calls.push_back("overlay"); return connects.at(c++); },
            [&](unsigned ms) { waits.push_back(ms); },
            [&] { return calls.size() >= stop_after_calls; });
    }
};
using Calls = std::vector<std::string>;

int connect_checks() {
    {   // No server: one probe, never an Overlay connect.
        Script s{{"VRInitError_Init_NoServerForBackgroundApp"}, {}};
        const auto r = s.run();
        assert(!r.connected && !r.server_running && !r.overlay_connect_attempted);
        assert(r.overlay_attempts == 0 && r.background_probes == 1);
        assert(r.error == "vrserver_not_running:VRInitError_Init_NoServerForBackgroundApp");
        assert((s.calls == Calls{"probe"}));
    }
    {   // Running server: the probe is shut down before the Overlay connect.
        Script s{{""}, {""}};
        const auto r = s.run();
        assert(r.connected && r.server_running && r.overlay_connect_attempted && r.error.empty());
        assert((s.calls == Calls{"probe", "shutdown_probe", "overlay"}));
        assert(r.background_probe_error == "VRInitError_None" && s.waits.empty());
    }
    {   // Bounded retry, each retry re-probing first, with the fixed backoff.
        Script s{{"", "", ""}, {"VRInitError_IPC_ConnectFailed", "VRInitError_IPC_ConnectFailed", ""}};
        const auto r = s.run();
        assert(r.connected && r.overlay_attempts == 3 && r.background_probes == 3);
        assert((s.waits == std::vector<unsigned>{250, 500}));
        assert((s.calls == Calls{"probe", "shutdown_probe", "overlay", "probe", "shutdown_probe", "overlay",
                                 "probe", "shutdown_probe", "overlay"}));
    }
    {   // Every attempt fails: stops at the limit with the last error named.
        Script s{{"", "", "", ""}, {"A", "B", "C", "never"}};
        const auto r = s.run();
        assert(!r.connected && r.overlay_attempts == kOverlayConnectAttempts);
        assert(r.error == "overlay_connect_failed:C" && r.overlay_errors.size() == 3);
        assert(s.waits.size() == kOverlayConnectAttempts - 1);
    }
    {   // The server goes away before a retry: no further Overlay connect.
        Script s{{"", "VRInitError_Init_NoServerForBackgroundApp"}, {"VRInitError_IPC_ConnectFailed"}};
        const auto r = s.run();
        assert(!r.connected && r.overlay_attempts == 1 && !r.server_running);
        assert(r.error == "vrserver_lost_before_overlay_connect:VRInitError_Init_NoServerForBackgroundApp");
        assert((s.calls == Calls{"probe", "shutdown_probe", "overlay", "probe"}));
    }
    {   // A stop request before the first probe, and between probe and connect.
        Script before{{""}, {""}, 0};
        auto r = before.run();
        assert(r.stopped && before.calls.empty() && r.error.empty());
        Script between{{""}, {""}, 2};
        r = between.run();
        assert(r.stopped && !r.overlay_connect_attempted);
        assert((between.calls == Calls{"probe", "shutdown_probe"}));
    }
    {   // Background-only opt-in: a running server is kept as the connection; no Overlay connect.
        std::size_t p = 0;
        std::vector<std::string> calls;
        const auto r = connect_overlay(
            [&] { calls.push_back("probe"); return std::vector<std::string>{""}.at(p++); },
            [&] { calls.push_back("shutdown_probe"); },
            [&] { calls.push_back("overlay"); return std::string(); },
            [&](unsigned) { calls.push_back("wait"); },
            [&] { return false; }, true);
        assert(r.connected && r.background_only && r.server_running && !r.overlay_connect_attempted);
        assert(r.overlay_attempts == 0 && r.background_probes == 1 && r.error.empty());
        assert((calls == Calls{"probe"}));
        // Without a server the opt-in refuses the same way.
        std::size_t q = 0;
        const auto none = connect_overlay(
            [&] { return std::vector<std::string>{"VRInitError_Init_NoServerForBackgroundApp"}.at(q++); },
            [] {}, [] { return std::string(); }, [](unsigned) {}, [] { return false; }, true);
        assert(!none.connected && none.background_only && !none.overlay_connect_attempted);
        assert(none.error == "vrserver_not_running:VRInitError_Init_NoServerForBackgroundApp");
    }
    std::puts("connect checks passed");
    return 0;
}
} // namespace

int main(int argc, char* argv[]) {
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "connect") return connect_checks();
    if (!isolate_stdout()) return 2;
    if (mode == "isolate") {
        std::printf("sdk chatter on fd 1\n");
        std::fflush(stdout);
        (void)!::write(STDOUT_FILENO, "raw chatter\n", 12);
        return write_protocol("{\"event\":\"protocol\"}\n") ? 0 : 3;
    }
    if (!install_watchdog()) return 4;
    if (mode == "watchdog" || mode == "watchdog-torn") {
        if (!set_watchdog("{\"event\":\"error\",\"error\":\"watchdog_expired:run\"}\n", 1)) return 5;
        if (mode == "watchdog-torn") {
            // A protocol write interrupted mid-line: the handler ends the torn line first.
            emitting = 1;
            (void)!::write(protocol_fd, "{\"partial\":", 11);
        }
        for (;;) ::pause();  // a hung call; only the alarm can end this
    }
    if (mode == "watchdog-rearm") {
        // Re-arming within the budget keeps a healthy loop alive past it.
        if (!set_watchdog("{\"event\":\"error\",\"error\":\"watchdog_expired:run\"}\n", 1)) return 5;
        for (int i = 0; i < 15; ++i) { rearm_watchdog(1); ::usleep(100000); }
        disarm_watchdog();
        return write_protocol("{\"event\":\"closed\"}\n") ? 0 : 3;
    }
    if (mode == "watchdog-oversize")
        return set_watchdog(std::string(kWatchdogMessageBytes + 1, 'x'), 1) || set_watchdog("x\n", 0) ? 6 : 0;
    return 64;
}
