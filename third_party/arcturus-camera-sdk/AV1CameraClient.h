// Copyright (c) 2026 Arcturus Industries LLC. All rights reserved.
//
// Licensed under the Arcturus Vision Camera Preview SDK Developer
// License Agreement (AV1CameraSDK-LICENSE.md).
// Use and distribution are subject to that agreement.

#ifndef AV1_CAMERA_CLIENT_H
#define AV1_CAMERA_CLIENT_H

#include "AV1CameraIPCProtocol.h"

// C++11 Linux client. Link against libAV1CameraClient.a.
// No exceptions or RTTI are required. Enable Developer camera access in Camera Switch.
class AV1CameraClient
{
    struct Session;
    struct Fds
    {
        int values[av1_ipc::kMaxCameras];
        uint32_t count = 0;
        Fds() noexcept = default;
        ~Fds() noexcept;
        Fds(const Fds&) = delete;
        Fds& operator=(const Fds&) = delete;
        bool add(int fd) noexcept;
        void clear() noexcept;
        void take(Fds& other) noexcept;
    };

public:
    struct Status
    {
        enum class Error
        {
            None,
            Server,
            NotConnected,
            InvalidArgument,
            Transport,
            Timeout,
            Protocol,
            OutOfMemory
        };
        Error error;
        av1_ipc::Result result;
        int systemError;
        Status(Error error = Error::None, av1_ipc::Result result = av1_ipc::Result_Success, int systemError = 0) noexcept
            : error(error), result(result), systemError(systemError)
        {
        }
        explicit operator bool() const noexcept { return error == Error::None; }
        bool isPermissionDenied() const noexcept
        {
            return error == Error::Server && result == av1_ipc::Result_PermissionDenied;
        }
        const char* message() const noexcept;
    };

    // Owns one dma-buf file descriptor per camera and keeps the camera buffers
    // reserved until release. Finish GPU/CPU use before release or destruction.
    // Release may wait for other commands, then use the command timeout for I/O.
    // Synchronize access to an individual FrameSet externally.
    class FrameSet
    {
    public:
        FrameSet() noexcept = default;
        ~FrameSet() noexcept;
        FrameSet(const FrameSet&) = delete;
        FrameSet& operator=(const FrameSet&) = delete;
        FrameSet(FrameSet&& other) noexcept;
        FrameSet& operator=(FrameSet&& other) noexcept;
        explicit operator bool() const noexcept { return m_session != nullptr; }
        const av1_ipc::AcquireFrameSetReply& metadata() const noexcept { return m_metadata; }
        // Borrowed descriptor; all planes of a camera share it. Do not close it.
        int cameraFd(uint32_t camera) const noexcept { return camera < m_fds.count ? m_fds.values[camera] : -1; }
        Status release() noexcept;

    private:
        friend class AV1CameraClient;
        void take(FrameSet& other) noexcept;
        Session* m_session = nullptr;
        Fds m_fds;
        av1_ipc::AcquireFrameSetReply m_metadata{};
    };

    AV1CameraClient() noexcept = default;
    ~AV1CameraClient() noexcept;
    AV1CameraClient(const AV1CameraClient&) = delete;
    AV1CameraClient& operator=(const AV1CameraClient&) = delete;
    AV1CameraClient(AV1CameraClient&& other) noexcept;
    AV1CameraClient& operator=(AV1CameraClient&& other) noexcept;

    // Connect and complete the Hello handshake within timeoutMs (must be > 0).
    // Later commands use the same I/O timeout. No automatic retries or reconnects.
    Status connect(uint32_t timeoutMs = 2000) noexcept;
    // Outstanding frames retain their original session; release them first to
    // immediately make the API available to another client.
    void disconnect() noexcept;
    bool isConnected() const noexcept;
    const av1_ipc::HelloReply& hello() const noexcept { return m_hello; }
    Status getControlState(av1_ipc::ControlState& controls) noexcept;
    Status setControls(const av1_ipc::ControlsRequest& controls) noexcept;
    // active enables this client's frame access. While active, changing recording
    // from false to true requests exclusive camera-control access. Ending recording
    // or deactivating the reader releases that access. This flag does not record
    // images or enable the red LED. A successful response does not guarantee
    // exclusive control access.
    Status setReaderState(bool active, bool recording = false) noexcept;
    // output must not already hold a frame set. The socket timeout is the command
    // timeout plus frameTimeoutMs. A server frame timeout leaves the session open;
    // a socket timeout closes it.
    Status acquireFrameSet(FrameSet& output, uint32_t frameTimeoutMs = 1000) noexcept;

    // Commands and frame releases are serialized. Synchronize client connection,
    // disconnection and moves externally. Transport/protocol failures close the
    // affected session and invalidate all its frame sets: stop using their buffers.
private:
    Status command(uint32_t type, const void* payload, uint32_t payloadSize, void* output, uint32_t outputSize) noexcept;
    Session* m_session = nullptr;
    av1_ipc::HelloReply m_hello{};
};

#endif // AV1_CAMERA_CLIENT_H
