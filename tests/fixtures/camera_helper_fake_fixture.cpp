// Drives the camera helper's fake backend through the Backend interface with
// scripted scenarios (tests/test_camera_helper.py writes the scripts). No SDK:
// the fixture links tools/camera_helper_fake.cpp and the reporting unit only.
// Usage: camera-helper-fake-fixture SCENARIO SCRIPT_PATH
#include "../../tools/camera_helper.hpp"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>

using namespace sightcircuit_camera;
namespace {
std::unique_ptr<Backend> open(const std::string& script) {
    std::string error;
    BackendOptions options;
    options.fake_script = script;
    auto backend = make_backend(options, error);
    if (!backend) { std::fprintf(stderr, "make_backend failed: %s\n", error.c_str()); std::exit(2); }
    assert(std::strcmp(backend->name(), "fake") == 0 && std::strcmp(backend_name(), "fake") == 0);
    assert(!backend->connected() && !backend->frame_set_held());
    return backend;
}
void rules_before_connect(Backend& backend) {
    ControlState controls;
    FrameSetMetadata frames;
    assert(backend.control_state(controls).error == CallError::NotConnected);
    assert(backend.set_reader_active(true).error == CallError::NotConnected);
    assert(backend.acquire_frame_set(frames, 100).error == CallError::NotConnected);
    assert(backend.release_frame_set().error == CallError::NotConnected);
    assert(backend.connect(0).error == CallError::InvalidArgument);  // the SDK requires timeoutMs > 0
}

int success(const std::string& script) {
    auto backend = open(script);
    rules_before_connect(*backend);
    assert(backend->connect(2000).ok() && backend->connected());
    assert(backend->connect(2000).error == CallError::InvalidArgument);  // already connected
    const Hello& hello = backend->hello();
    assert(hello.protocol_version == 1 && hello.camera_count == 2 && hello.controls.current_fps == 72.0f);
    ControlState controls;
    assert(backend->control_state(controls).ok() && controls.current_fps == 72.0f && controls.red_recording_led_enabled == 0);
    FrameSetMetadata frames;
    const CallStatus not_ready = backend->acquire_frame_set(frames, 100);  // reader not enabled yet
    assert(not_ready.error == CallError::Server && not_ready.result == kResultNotReady && !backend->frame_set_held());
    assert(backend->set_reader_active(true).ok());
    assert(backend->acquire_frame_set(frames, 100).ok() && backend->frame_set_held());
    assert(frames.sequence == 1 && frames.camera_count == 2 && frames.cameras[0].width == 2464 && frames.cameras[1].height == 2464);
    assert(frames.cameras[0].pixel_format == kPixelNv12Ubwc && frames.cameras[0].drm_modifier == 0x0500000000000001ULL);
    assert(frames.cameras[0].plane_count == 2 && frames.cameras[0].planes[1].offset == 2464u * 2464u);
    assert(frames.cameras[0].planes[1].size == 2464u * 2464u / 2 && frames.cameras[0].planes[0].stride == 2464);
    assert(frames.cameras[0].frame_id + 1000000ULL == frames.cameras[1].frame_id);
    assert(frames.cameras[0].first_camera_from_camera_valid == 1 && frames.pose_valid == 1);
    assert(std::strcmp(pixel_format_name(frames.cameras[0].pixel_format), "PixelFormat_NV12_UBWC") == 0);
    assert(backend->acquire_frame_set(frames, 100).error == CallError::InvalidArgument);  // one outstanding at most
    assert(backend->release_frame_set().ok() && !backend->frame_set_held());
    assert(backend->release_frame_set().error == CallError::InvalidArgument);  // nothing held
    assert(backend->acquire_frame_set(frames, 100).ok() && frames.sequence == 2 && backend->release_frame_set().ok());
    // The script gave two frames (repeat=2); the third acquire finds none: the server's frame timeout, session open.
    const CallStatus empty = backend->acquire_frame_set(frames, 100);
    assert(empty.error == CallError::Server && empty.result == kResultTimeout && backend->connected());
    assert(backend->set_reader_active(false).ok());
    backend->disconnect();
    assert(!backend->connected() && !backend->frame_set_held());
    assert(backend->control_state(controls).error == CallError::NotConnected);
    return 0;
}
int permission_denied(const std::string& script) {
    auto backend = open(script);
    const CallStatus status = backend->connect(2000);
    assert(!status.ok() && status.permission_denied() && !status.closes_session() && !backend->connected());
    assert(std::strcmp(result_name(status.result), "Result_PermissionDenied") == 0);
    assert(std::strcmp(call_error_name(status.error), "Server") == 0);
    // A second scripted outcome: connected, then the reader is refused by policy; the session stays open.
    assert(backend->connect(2000).ok());
    const CallStatus reader = backend->set_reader_active(true);
    assert(reader.permission_denied() && backend->connected());
    FrameSetMetadata frames;
    const CallStatus acquire = backend->acquire_frame_set(frames, 100);
    assert(acquire.error == CallError::Server && acquire.result == kResultNotReady);  // the reader never became active
    return 0;
}
int already_connected(const std::string& script) {
    auto backend = open(script);
    const CallStatus status = backend->connect(2000);
    assert(status.error == CallError::Server && status.result == kResultClientAlreadyConnected && !backend->connected());
    assert(std::strcmp(result_name(status.result), "Result_ClientAlreadyConnected") == 0);
    assert(!status.closes_session());
    return 0;
}
int protocol_error(const std::string& script) {
    auto backend = open(script);
    assert(backend->connect(2000).ok() && backend->set_reader_active(true).ok());
    FrameSetMetadata frames;
    assert(backend->acquire_frame_set(frames, 100).ok() && backend->frame_set_held());
    // The scripted release fails with a protocol error: the session closes and the frame set is invalid.
    const CallStatus status = backend->release_frame_set();
    assert(status.error == CallError::Protocol && status.closes_session());
    assert(!backend->connected() && !backend->frame_set_held());
    assert(backend->acquire_frame_set(frames, 100).error == CallError::NotConnected);
    // Reconnect: a transport failure with an errno on the next acquire closes the session too.
    assert(backend->connect(2000).ok() && backend->set_reader_active(true).ok());
    const CallStatus transport = backend->acquire_frame_set(frames, 100);
    assert(transport.error == CallError::Transport && transport.system_error == 104 && !backend->connected());
    // Reconnect once more: a socket timeout closes the session, a server timeout does not.
    assert(backend->connect(2000).ok() && backend->set_reader_active(true).ok());
    const CallStatus server_timeout = backend->acquire_frame_set(frames, 100);
    assert(server_timeout.error == CallError::Server && server_timeout.result == kResultTimeout && backend->connected());
    const CallStatus socket_timeout = backend->acquire_frame_set(frames, 100);
    assert(socket_timeout.error == CallError::Timeout && !backend->connected());
    return 0;
}
int json(const std::string&) {
    Json value = Json::object();
    value.set("text", "a\"b\\c\n\x01"); value.set("flag", true); value.set("count", 7U);
    value.set("big", static_cast<std::uint64_t>(0x0500000000000001ULL)); value.set("negative", static_cast<std::int64_t>(-3));
    value.set("real", 0.25); value.set("nan", 0.0 / 0.0); value.set("null", Json());
    Json list = Json::array(); list.push(1); list.push("x");
    value.set("list", list); value.set("flag", false);  // set replaces in place
    const std::string expected = "{\"text\":\"a\\\"b\\\\c\\n\\u0001\",\"flag\":false,\"count\":7,\"big\":360287970189639681,"
                                 "\"negative\":-3,\"real\":0.25,\"nan\":null,\"null\":null,\"list\":[1,\"x\"]}";
    if (value.dump() != expected) { std::fprintf(stderr, "json: %s\n", value.dump().c_str()); return 1; }
    assert(value.get("count").text() == "7" && value.get("missing").kind() == Json::Kind::Null);
    return 0;
}
} // namespace

int main(int argc, char* argv[]) {
    if (argc != 3) { std::fprintf(stderr, "usage: SCENARIO SCRIPT_PATH\n"); return 2; }
    const std::string scenario = argv[1], script = argv[2];
    int result = 2;
    if (scenario == "success") result = success(script);
    else if (scenario == "permission-denied") result = permission_denied(script);
    else if (scenario == "already-connected") result = already_connected(script);
    else if (scenario == "protocol-error") result = protocol_error(script);
    else if (scenario == "json") result = json(script);
    else { std::fprintf(stderr, "unknown scenario\n"); return 2; }
    if (result == 0) std::printf("%s checks passed\n", scenario.c_str());
    return result;
}
