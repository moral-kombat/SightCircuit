// The one place an AV1CameraClient::Status becomes a SightCircuit Outcome.
//
// It reads only the inline members of Status (error, result, systemError) and
// never calls message(), so CI can compile and run it against the vendored
// headers without the Client Library (tests/fixtures/camera_status_map_fixture.cpp).
// C++11. The order of the rules is the order they are applied.
#pragma once
#include "AV1CameraClient.h"
#include "camera_backend.hpp"

namespace sightcircuit_camera {

enum class SdkCall { connect, control_state, set_reader, acquire, release };

inline Outcome map_status(const AV1CameraClient::Status& status, SdkCall call) {
    typedef AV1CameraClient::Status::Error Error;
    // 1. Success.
    if (status) return Outcome::ok;
    // 2. A failed release is terminal whatever its error or result: the SDK says
    //    a transport or protocol failure invalidates every frame set, and the
    //    helper must not touch the session again.
    if (call == SdkCall::release) return Outcome::transport_lost;
    // 3. A server-reported result. Status defaults result to Result_Success for
    //    every other error, so result is read only under Error::Server.
    if (status.error == Error::Server) {
        switch (status.result) {
            case av1_ipc::Result_Timeout:
                return call == SdkCall::acquire ? Outcome::no_frame : Outcome::helper_error;
            case av1_ipc::Result_NotReady: return Outcome::not_ready;
            case av1_ipc::Result_Unavailable: return Outcome::unavailable;
            case av1_ipc::Result_PermissionDenied: return Outcome::permission_denied;
            case av1_ipc::Result_ClientProtocolTooNew:
            case av1_ipc::Result_ClientProtocolTooOld: return Outcome::protocol_mismatch;
            case av1_ipc::Result_ClientAlreadyConnected:
            case av1_ipc::Result_ResourceDenied: return Outcome::camera_busy;
            case av1_ipc::Result_InternalError:
            case av1_ipc::Result_InvalidArgument:
            case av1_ipc::Result_Success: return Outcome::helper_error;
        }
        return Outcome::helper_error;  // an unlisted result
    }
    // 4. A client-side error.
    switch (status.error) {
        case Error::Timeout:
        case Error::Transport:
        case Error::Protocol:
        case Error::NotConnected:
            return call == SdkCall::connect ? Outcome::provider_unreachable : Outcome::transport_lost;
        case Error::InvalidArgument:
        case Error::OutOfMemory: return Outcome::helper_error;
        case Error::None:
        case Error::Server: break;  // handled above
    }
    return Outcome::helper_error;
}

inline const char* status_error_name(AV1CameraClient::Status::Error error) {
    typedef AV1CameraClient::Status::Error Error;
    switch (error) {
        case Error::None: return "None";
        case Error::Server: return "Server";
        case Error::NotConnected: return "NotConnected";
        case Error::InvalidArgument: return "InvalidArgument";
        case Error::Transport: return "Transport";
        case Error::Timeout: return "Timeout";
        case Error::Protocol: return "Protocol";
        case Error::OutOfMemory: return "OutOfMemory";
    }
    return "unknown_error";
}

inline const char* result_name(av1_ipc::Result result) {
    switch (result) {
        case av1_ipc::Result_Success: return "Result_Success";
        case av1_ipc::Result_Unavailable: return "Result_Unavailable";
        case av1_ipc::Result_InvalidArgument: return "Result_InvalidArgument";
        case av1_ipc::Result_ClientProtocolTooNew: return "Result_ClientProtocolTooNew";
        case av1_ipc::Result_NotReady: return "Result_NotReady";
        case av1_ipc::Result_InternalError: return "Result_InternalError";
        case av1_ipc::Result_Timeout: return "Result_Timeout";
        case av1_ipc::Result_ResourceDenied: return "Result_ResourceDenied";
        case av1_ipc::Result_ClientAlreadyConnected: return "Result_ClientAlreadyConnected";
        case av1_ipc::Result_PermissionDenied: return "Result_PermissionDenied";
        case av1_ipc::Result_ClientProtocolTooOld: return "Result_ClientProtocolTooOld";
    }
    return "unknown_result";
}

} // namespace sightcircuit_camera
