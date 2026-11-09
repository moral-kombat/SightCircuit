// Asserts every row of the camera helper's status mapping against the vendored
// SDK headers, without the Client Library: the mapping reads only the inline
// members of AV1CameraClient::Status. C++11. Exit 0 when every row holds.
#include <cstdio>
#include <cstdlib>

#include "camera_status_map.hpp"

using sightcircuit_camera::Outcome;
using sightcircuit_camera::SdkCall;
using sightcircuit_camera::map_status;
using sightcircuit_camera::outcome_name;
typedef AV1CameraClient::Status Status;
typedef Status::Error Error;

namespace {
int failures = 0;

void expect(const char* label, Status status, SdkCall call, Outcome expected) {
    const Outcome actual = map_status(status, call);
    if (actual != expected) {
        std::fprintf(stderr, "%s: expected %s, got %s\n", label, outcome_name(expected), outcome_name(actual));
        ++failures;
    }
}

const SdkCall kSessionCalls[] = {SdkCall::control_state, SdkCall::set_reader, SdkCall::acquire};
}

int main() {
    // 1. Success, whatever the call.
    expect("default status", Status(), SdkCall::connect, Outcome::ok);
    expect("default status", Status(), SdkCall::acquire, Outcome::ok);
    expect("default status", Status(), SdkCall::release, Outcome::ok);

    // 2. A failed release is transport_lost whatever its error or result.
    expect("release server timeout", Status(Error::Server, av1_ipc::Result_Timeout), SdkCall::release, Outcome::transport_lost);
    expect("release permission", Status(Error::Server, av1_ipc::Result_PermissionDenied), SdkCall::release, Outcome::transport_lost);
    expect("release invalid argument", Status(Error::InvalidArgument), SdkCall::release, Outcome::transport_lost);
    expect("release not connected", Status(Error::NotConnected), SdkCall::release, Outcome::transport_lost);

    // 3. Server results.
    expect("acquire server timeout", Status(Error::Server, av1_ipc::Result_Timeout), SdkCall::acquire, Outcome::no_frame);
    expect("connect server timeout", Status(Error::Server, av1_ipc::Result_Timeout), SdkCall::connect, Outcome::helper_error);
    expect("controls server timeout", Status(Error::Server, av1_ipc::Result_Timeout), SdkCall::control_state, Outcome::helper_error);
    expect("reader server timeout", Status(Error::Server, av1_ipc::Result_Timeout), SdkCall::set_reader, Outcome::helper_error);
    for (unsigned i = 0; i < 3; ++i) {
        expect("not ready", Status(Error::Server, av1_ipc::Result_NotReady), kSessionCalls[i], Outcome::not_ready);
        expect("unavailable", Status(Error::Server, av1_ipc::Result_Unavailable), kSessionCalls[i], Outcome::unavailable);
        expect("permission denied", Status(Error::Server, av1_ipc::Result_PermissionDenied), kSessionCalls[i], Outcome::permission_denied);
        expect("too new", Status(Error::Server, av1_ipc::Result_ClientProtocolTooNew), kSessionCalls[i], Outcome::protocol_mismatch);
        expect("too old", Status(Error::Server, av1_ipc::Result_ClientProtocolTooOld), kSessionCalls[i], Outcome::protocol_mismatch);
        expect("already connected", Status(Error::Server, av1_ipc::Result_ClientAlreadyConnected), kSessionCalls[i], Outcome::camera_busy);
        expect("resource denied", Status(Error::Server, av1_ipc::Result_ResourceDenied), kSessionCalls[i], Outcome::camera_busy);
        expect("internal error", Status(Error::Server, av1_ipc::Result_InternalError), kSessionCalls[i], Outcome::helper_error);
        expect("invalid argument result", Status(Error::Server, av1_ipc::Result_InvalidArgument), kSessionCalls[i], Outcome::helper_error);
        expect("success result under Server", Status(Error::Server, av1_ipc::Result_Success), kSessionCalls[i], Outcome::helper_error);
    }
    expect("connect too new", Status(Error::Server, av1_ipc::Result_ClientProtocolTooNew), SdkCall::connect, Outcome::protocol_mismatch);
    expect("connect too old", Status(Error::Server, av1_ipc::Result_ClientProtocolTooOld), SdkCall::connect, Outcome::protocol_mismatch);
    expect("connect already connected", Status(Error::Server, av1_ipc::Result_ClientAlreadyConnected), SdkCall::connect, Outcome::camera_busy);
    expect("connect permission denied", Status(Error::Server, av1_ipc::Result_PermissionDenied), SdkCall::connect, Outcome::permission_denied);
    const av1_ipc::Result unlisted = static_cast<av1_ipc::Result>(99);
    expect("unlisted server result", Status(Error::Server, unlisted), SdkCall::acquire, Outcome::helper_error);
    if (std::string(sightcircuit_camera::result_name(unlisted)) != "unknown_result") {
        std::fprintf(stderr, "unlisted result is not named unknown_result\n");
        ++failures;
    }

    // 4. Client-side errors; the result defaults to Success and is never read first.
    const Error terminal[] = {Error::Timeout, Error::Transport, Error::Protocol, Error::NotConnected};
    for (unsigned e = 0; e < 4; ++e) {
        expect("terminal from connect", Status(terminal[e]), SdkCall::connect, Outcome::provider_unreachable);
        for (unsigned i = 0; i < 3; ++i) expect("terminal in session", Status(terminal[e]), kSessionCalls[i], Outcome::transport_lost);
    }
    expect("transport with default result", Status(Error::Transport), SdkCall::acquire, Outcome::transport_lost);
    expect("transport with a permission result set", Status(Error::Transport, av1_ipc::Result_PermissionDenied), SdkCall::acquire, Outcome::transport_lost);
    expect("invalid argument", Status(Error::InvalidArgument), SdkCall::acquire, Outcome::helper_error);
    expect("invalid argument from connect", Status(Error::InvalidArgument), SdkCall::connect, Outcome::helper_error);
    expect("out of memory", Status(Error::OutOfMemory), SdkCall::acquire, Outcome::helper_error);
    expect("out of memory from connect", Status(Error::OutOfMemory), SdkCall::connect, Outcome::helper_error);
    const Error unlisted_error = static_cast<Error>(77);
    expect("unlisted error", Status(unlisted_error), SdkCall::acquire, Outcome::helper_error);
    if (std::string(sightcircuit_camera::status_error_name(unlisted_error)) != "unknown_error") {
        std::fprintf(stderr, "unlisted error is not named unknown_error\n");
        ++failures;
    }

    // isPermissionDenied() agrees with the mapping.
    if (!Status(Error::Server, av1_ipc::Result_PermissionDenied).isPermissionDenied() ||
        Status(Error::Transport, av1_ipc::Result_PermissionDenied).isPermissionDenied()) {
        std::fprintf(stderr, "isPermissionDenied disagrees with the mapping\n");
        ++failures;
    }

    if (failures) return 1;
    std::printf("camera status mapping: every row holds\n");
    return 0;
}
