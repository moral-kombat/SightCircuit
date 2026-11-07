// Overlay bridge lifecycle helpers; no OpenVR or device access.
//
// Protocol output isolation and the SIGALRM watchdog handler are adapted from
// JakeGreen2145/frame-testbench native/observe.cpp at commit
// ea7f5109c05e56974f3ccc2e0b4af8fbf232579b (L22-L28 and L522-L586):
//   MIT License, Copyright (c) 2026 Donn Green.
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction, including without limitation the rights
//   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//   copies of the Software, and to permit persons to whom the Software is
//   furnished to do so, subject to the following conditions:
//   The above copyright notice and this permission notice shall be included in all
//   copies or substantial portions of the Software.
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//   SOFTWARE.
// Changes: the message is a caller-built JSON report swapped in with SIGALRM
// blocked, a torn protocol line is terminated first, the alarm is re-armed per
// phase and loop step, and the exit code is kWatchdogExitCode.
// See THIRD_PARTY.md. The connect sequence below is original SightCircuit code.
#pragma once
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <vector>

namespace sightcircuit_lifecycle {
constexpr int kWatchdogExitCode = 124;
constexpr std::size_t kWatchdogMessageBytes = 2048;

inline int protocol_fd = STDOUT_FILENO;
inline volatile std::sig_atomic_t emitting = 0;
inline char watchdog_message[kWatchdogMessageBytes];
inline volatile std::sig_atomic_t watchdog_length = 0;

// Async-signal-safe: only write() and _exit().
inline void write_all_raw(const char* data, std::size_t size) noexcept {
    while (size) {
        const auto count = ::write(protocol_fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return;
        data += count; size -= static_cast<std::size_t>(count);
    }
}
inline void watchdog_handler(int) {
    // Never run destructors or SDK shutdown from here: a hung call stays hung.
    // A protocol write blocked on a full pipe is one such call: make the
    // descriptor non-blocking first, so this handler's own writes cannot hang
    // and _exit() is reached even if the message goes out truncated.
    const int flags = ::fcntl(protocol_fd, F_GETFL);
    if (flags >= 0) ::fcntl(protocol_fd, F_SETFL, flags | O_NONBLOCK);
    if (emitting) write_all_raw("\n", 1);
    write_all_raw(watchdog_message, static_cast<std::size_t>(watchdog_length));
    _exit(kWatchdogExitCode);
}

// Keep the real stdout for the protocol and point fd 1 at stderr, so SDK
// chatter (dlopen constructors, VR_Init, VR_Shutdown) cannot corrupt reports.
inline bool isolate_stdout() noexcept {
    const int saved = ::fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3);
    if (saved < 0) return false;
    if (::dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
        // No usable stderr (launched with 2>&-): send the chatter nowhere rather than refuse.
        const int sink = ::open("/dev/null", O_WRONLY | O_CLOEXEC);
        if (sink < 0 || ::dup2(sink, STDOUT_FILENO) < 0) {
            if (sink >= 0) ::close(sink);
            ::close(saved);
            return false;
        }
        ::close(sink);
    }
    protocol_fd = saved;
    return true;
}
// One whole protocol line; SIGALRM stays deliverable so a blocked write cannot
// disable the watchdog. Returns false on a write failure.
inline bool write_protocol(const std::string& line) noexcept {
    emitting = 1;
    const char* data = line.data();
    std::size_t size = line.size();
    bool ok = true;
    while (size) {
        const auto count = ::write(protocol_fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { ok = false; break; }
        data += count; size -= static_cast<std::size_t>(count);
    }
    emitting = 0;
    return ok;
}
inline bool install_watchdog() noexcept {
    struct sigaction action{};
    action.sa_handler = watchdog_handler;
    sigemptyset(&action.sa_mask);
    return ::sigaction(SIGALRM, &action, nullptr) == 0;
}
// Replace the message (one JSON line, newline included) and arm the alarm.
// SIGALRM is blocked while the buffer changes, so the handler never sees a
// half-written message.
inline bool set_watchdog(const std::string& line, unsigned seconds) noexcept {
    if (line.empty() || line.size() > kWatchdogMessageBytes || !seconds) return false;
    sigset_t block, previous;
    sigemptyset(&block); sigaddset(&block, SIGALRM);
    if (::sigprocmask(SIG_BLOCK, &block, &previous)) return false;
    ::alarm(seconds);  // Cancels the previous phase's timer before its alarm can be mistaken for this one.
    sigset_t pending;
    if (::sigpending(&pending) == 0 && sigismember(&pending, SIGALRM)) {
        // The previous phase's alarm already fired while blocked here; it must not be
        // delivered under this phase's message.
        const timespec zero{0, 0};
        ::sigtimedwait(&block, nullptr, &zero);
    }
    std::memcpy(watchdog_message, line.data(), line.size());
    watchdog_length = static_cast<std::sig_atomic_t>(line.size());
    ::sigprocmask(SIG_SETMASK, &previous, nullptr);
    return true;
}
inline void rearm_watchdog(unsigned seconds) noexcept { if (seconds) ::alarm(seconds); }
inline void disarm_watchdog() noexcept { ::alarm(0); }

// Background first: a Background VR_Init never starts a vrserver. Only when it
// succeeds is the probe shut down and the Overlay connect attempted, with a
// bounded retry; each retry re-probes first. A server that exits between the
// probe and the Overlay VR_Init can still be started by that call: the window
// is narrowed, not closed.
struct ConnectResult {
    bool connected = false, stopped = false, server_running = false;
    bool background_only = false;        // connected through the probe itself; no Overlay connect
    bool overlay_connect_attempted = false;
    unsigned background_probes = 0, overlay_attempts = 0;
    std::string background_probe_error;  // last probe result symbol
    std::vector<std::string> overlay_errors;
    std::string error;                   // empty when connected or stopped
};
constexpr unsigned kOverlayConnectAttempts = 3;
constexpr unsigned kOverlayRetryDelayMs[kOverlayConnectAttempts - 1] = {250, 500};

// probe() and connect() return an empty string on success, else an error
// symbol. shutdown_probe() releases a successful probe. wait(ms) sleeps.
// stopping() is checked before each step. With background_only, a successful
// probe is kept as the connection and the Overlay connect is never made: an
// opt-in for the device check of whether a Background client may own overlays.
template <class Probe, class ShutdownProbe, class Connect, class Wait, class Stopping>
ConnectResult connect_overlay(Probe probe, ShutdownProbe shutdown_probe, Connect connect,
                              Wait wait, Stopping stopping, bool background_only = false) {
    ConnectResult result;
    result.background_only = background_only;
    for (unsigned attempt = 0; attempt < kOverlayConnectAttempts; ++attempt) {
        if (stopping()) { result.stopped = true; return result; }
        ++result.background_probes;
        const std::string probe_error = probe();
        result.background_probe_error = probe_error.empty() ? "VRInitError_None" : probe_error;
        if (!probe_error.empty()) {
            result.server_running = false;
            result.error = std::string(attempt ? "vrserver_lost_before_overlay_connect:" :
                                                 "vrserver_not_running:") + probe_error;
            return result;
        }
        result.server_running = true;
        if (background_only) { result.connected = true; return result; }
        shutdown_probe();
        if (stopping()) { result.stopped = true; return result; }
        result.overlay_connect_attempted = true;
        ++result.overlay_attempts;
        const std::string connect_error = connect();
        if (connect_error.empty()) { result.connected = true; return result; }
        result.overlay_errors.push_back(connect_error);
        if (attempt + 1 < kOverlayConnectAttempts) wait(kOverlayRetryDelayMs[attempt]);
    }
    result.error = "overlay_connect_failed:" + result.overlay_errors.back();
    return result;
}
} // namespace sightcircuit_lifecycle
