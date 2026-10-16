// Original Framejacker transport helpers; MIT licensed. No camera or Vulkan calls.
#pragma once

#include "handcam_proto.h"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <poll.h>
#include <set>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace framejacker::transport {
static_assert(sizeof(MrhandBufInfo) == 144, "unexpected native registry ABI");
static_assert(offsetof(MrhandBufInfo, size) == 16 &&
              offsetof(MrhandBufInfo, planeOffset) == 80 &&
              offsetof(MrhandBufInfo, planeRowPitch) == 112, "unexpected registry offsets");
constexpr size_t maximum_records = 64;
using Clock = std::chrono::steady_clock;
using Deadline = Clock::time_point;

class Error : public std::runtime_error {
public:
    explicit Error(const char *reason) : std::runtime_error(reason) {}
};

class Fd {
    int value_ = -1;
public:
    explicit Fd(int value = -1) : value_(value) {}
    ~Fd() { if (value_ >= 0) close(value_); }
    Fd(const Fd &) = delete;
    Fd &operator=(const Fd &) = delete;
    Fd(Fd &&other) noexcept : value_(std::exchange(other.value_, -1)) {}
    Fd &operator=(Fd &&other) noexcept {
        if (this != &other) {
            if (value_ >= 0) close(value_);
            value_ = std::exchange(other.value_, -1);
        }
        return *this;
    }
    int get() const { return value_; }
    int release() { return std::exchange(value_, -1); }
};

inline Fd duplicate(int fd) {
    int owned = fcntl(fd, F_DUPFD_CLOEXEC, 0);
    if (owned < 0) throw Error("descriptor_duplication_failed");
    return Fd(owned);
}

struct Record {
    MrhandBufInfo info{};
    Fd fd;
};

inline void wait_ready(int fd, short events, Deadline deadline) {
    for (;;) {
        auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
        if (remaining.count() <= 0) throw Error("socket_deadline_exceeded");
        pollfd item{fd, events, 0};
        int timeout = static_cast<int>(std::min<int64_t>(remaining.count(), std::numeric_limits<int>::max()));
        int result = poll(&item, 1, timeout);
        if (result < 0 && errno == EINTR) continue;
        if (result < 0) throw Error("socket_poll_failed");
        if (result == 0) continue;
        if (item.revents & POLLNVAL) throw Error("socket_invalid");
        // Let send/recv report EOF/errors, including data arriving with POLLHUP.
        if (item.revents & (events | POLLERR | POLLHUP)) return;
    }
}

inline void require_same_user(int socket) {
    ucred credentials{};
    socklen_t size = sizeof(credentials);
    if (getsockopt(socket, SOL_SOCKET, SO_PEERCRED, &credentials, &size) != 0 ||
        size != sizeof(credentials) || credentials.uid != geteuid())
        throw Error("socket_peer_uid_mismatch");
}

inline Fd connect_registry(const char *path, Deadline deadline) {
    sockaddr_un address{};
    if (!path || path[0] != '/' || std::strlen(path) >= sizeof(address.sun_path))
        throw Error("invalid_socket_path");
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path, std::strlen(path) + 1);
    Fd connection(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
    if (connection.get() < 0) throw Error("socket_creation_failed");
    if (connect(connection.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0) {
        if (errno != EINPROGRESS) throw Error("socket_connect_failed");
        wait_ready(connection.get(), POLLOUT, deadline);
        int error = 0;
        socklen_t size = sizeof(error);
        if (getsockopt(connection.get(), SOL_SOCKET, SO_ERROR, &error, &size) != 0 || error)
            throw Error("socket_connect_failed");
    }
    require_same_user(connection.get());
    return connection;
}

inline void send_record(int socket, const MrhandBufInfo &info, int fd, Deadline deadline) {
    size_t offset = 0;
    while (offset != sizeof(info)) {
        wait_ready(socket, POLLOUT, deadline);
        iovec payload{const_cast<char *>(reinterpret_cast<const char *>(&info)) + offset,
                      sizeof(info) - offset};
        alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int))> control{};
        msghdr message{};
        message.msg_iov = &payload;
        message.msg_iovlen = 1;
        // SCM_RIGHTS belongs only to the first successfully sent byte.
        if (offset == 0 && fd >= 0) {
            message.msg_control = control.data();
            message.msg_controllen = control.size();
            auto *header = CMSG_FIRSTHDR(&message);
            header->cmsg_level = SOL_SOCKET;
            header->cmsg_type = SCM_RIGHTS;
            header->cmsg_len = CMSG_LEN(sizeof(int));
            std::memcpy(CMSG_DATA(header), &fd, sizeof(fd));
        }
        ssize_t sent = sendmsg(socket, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (sent < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (sent <= 0) throw Error("socket_send_failed");
        offset += static_cast<size_t>(sent);
    }
}

inline Record receive_record(int socket, Deadline deadline) {
    Record record;
    size_t offset = 0;
    std::vector<Fd> descriptors;
    while (offset != sizeof(record.info)) {
        wait_ready(socket, POLLIN, deadline);
        iovec payload{reinterpret_cast<char *>(&record.info) + offset, sizeof(record.info) - offset};
        alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int) * 4)> control{};
        msghdr message{};
        message.msg_iov = &payload;
        message.msg_iovlen = 1;
        message.msg_control = control.data();
        message.msg_controllen = control.size();
        ssize_t received = recvmsg(socket, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
        if (received < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (received < 0) throw Error("socket_receive_failed");
        if (received == 0) throw Error("socket_peer_closed");
        bool malformed = false;
        for (auto *header = CMSG_FIRSTHDR(&message); header; header = CMSG_NXTHDR(&message, header)) {
            if (header->cmsg_level != SOL_SOCKET || header->cmsg_type != SCM_RIGHTS ||
                header->cmsg_len < CMSG_LEN(0)) {
                malformed = true;
                continue;
            }
            size_t bytes = header->cmsg_len - CMSG_LEN(0);
            if (bytes % sizeof(int)) malformed = true;
            for (size_t i = 0; i + sizeof(int) <= bytes; i += sizeof(int)) {
                int owned;
                std::memcpy(&owned, CMSG_DATA(header) + i, sizeof(owned));
                Fd descriptor(owned);
                descriptors.push_back(std::move(descriptor));
            }
        }
        // Received rights are owned before rejecting a truncated/malformed message.
        if (malformed || (message.msg_flags & (MSG_CTRUNC | MSG_TRUNC)) || descriptors.size() > 1)
            throw Error("invalid_ancillary_data");
        offset += static_cast<size_t>(received);
    }
    if (record.info.magic != MRHAND_MAGIC || record.info.generation == 0)
        throw Error("invalid_registry_header");
    if (record.info.id == 0) {
        if (!descriptors.empty()) throw Error("terminator_has_descriptor");
        return record;
    }
    if (descriptors.size() != 1) throw Error("record_descriptor_missing");
    if (record.info.size == 0 || record.info.size > (1ull << 30) ||
        record.info.hasImage > 1 || record.info.hasModifier > 1 ||
        record.info.planeCount > MRHAND_MAX_PLANES || record.info.width > 8192 ||
        record.info.height > 8192 || record.info.arrayLayers > 2)
        throw Error("registry_metadata_out_of_bounds");
    record.fd = std::move(descriptors.front());
    return record;
}

inline std::vector<Record> receive_registry(int socket, Deadline deadline, uint32_t *snapshot_generation = nullptr) {
    require_same_user(socket);
    std::vector<Record> records;
    std::set<uint32_t> identifiers;
    uint32_t generation = 0;
    for (;;) {
        auto record = receive_record(socket, deadline);
        if (generation && generation != record.info.generation) throw Error("registry_generation_changed");
        generation = record.info.generation;
        if (record.info.id == 0) {
            if (snapshot_generation) *snapshot_generation = generation;
            return records;
        }
        if (records.size() == maximum_records) throw Error("registry_record_limit");
        if (!identifiers.insert(record.info.id).second) throw Error("duplicate_buffer_id");
        records.push_back(std::move(record));
    }
}
} // namespace framejacker::transport
