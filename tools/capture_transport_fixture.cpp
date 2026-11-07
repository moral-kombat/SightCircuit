// Original synthetic Unix-socket tests. No Vulkan, camera or runtime access.
#include "capture_transport.hpp"
#include <cstdlib>
#include <dirent.h>
#include <future>
#include <iostream>
#include <sys/mman.h>
#include <thread>

using namespace sightcircuit::transport;
using namespace std::chrono_literals;

static void require(bool condition) { if (!condition) throw Error("fixture_assertion_failed"); }
struct Pair {
    Fd a, b;
    Pair() {
        int sockets[2];
        require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0, sockets) == 0);
        a = Fd(sockets[0]); b = Fd(sockets[1]);
    }
};
static MrhandBufInfo metadata(uint32_t id = 1, uint32_t generation = 7) {
    MrhandBufInfo info{};
    info.magic = MRHAND_MAGIC; info.id = id; info.generation = generation;
    info.size = 3; info.hasImage = 1; info.width = info.height = 2;
    return info;
}
static Fd payload() {
    Fd fd(memfd_create("sightcircuit-synthetic-transport", MFD_CLOEXEC));
    require(fd.get() >= 0 && write(fd.get(), "abc", 3) == 3);
    return fd;
}
static void send_prefix(int socket, const MrhandBufInfo &info, int fd, size_t length) {
    iovec bytes{const_cast<MrhandBufInfo *>(&info), length};
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int))]{};
    msghdr message{};
    message.msg_iov = &bytes; message.msg_iovlen = 1;
    message.msg_control = control; message.msg_controllen = sizeof(control);
    auto *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int));
    std::memcpy(CMSG_DATA(header), &fd, sizeof(fd));
    require(sendmsg(socket, &message, MSG_NOSIGNAL) == static_cast<ssize_t>(length));
}
template <typename F> static void rejects(const char *reason, F action) {
    try { action(); } catch (const Error &error) { require(std::strcmp(error.what(), reason) == 0); return; }
    throw Error("fixture_expected_rejection");
}
static size_t descriptors_open() {
    DIR *directory = opendir("/proc/self/fd");
    require(directory != nullptr);
    size_t count = 0;
    while (readdir(directory)) ++count;
    closedir(directory);
    return count;
}
static void snapshot_lifetime() {
    Pair sockets;
    auto original = payload();
    auto snapshot = duplicate(original.get());
    original = Fd();
    auto deadline = Clock::now() + 1s;
    send_record(sockets.a.get(), metadata(), snapshot.get(), deadline);
    snapshot = Fd();
    send_record(sockets.a.get(), metadata(0), -1, deadline);
    auto records = receive_registry(sockets.b.get(), deadline);
    require(records.size() == 1);
    require(fcntl(records[0].fd.get(), F_GETFD) & FD_CLOEXEC);
    char text[3];
    require(pread(records[0].fd.get(), text, 3, 0) == 3 && std::memcmp(text, "abc", 3) == 0);
}
static void fragmented_record() {
    Pair sockets;
    auto descriptor = payload();
    auto info = metadata();
    auto sender = std::async(std::launch::async, [&] {
        send_prefix(sockets.a.get(), info, descriptor.get(), 1);
        for (size_t offset = 1; offset < sizeof(info);) {
            std::this_thread::sleep_for(2ms);
            size_t length = std::min<size_t>(7, sizeof(info) - offset);
            require(send(sockets.a.get(), reinterpret_cast<char *>(&info) + offset,
                         length, MSG_NOSIGNAL) == static_cast<ssize_t>(length));
            offset += length;
        }
        send_record(sockets.a.get(), metadata(0), -1, Clock::now() + 1s);
    });
    auto records = receive_registry(sockets.b.get(), Clock::now() + 2s);
    sender.get();
    require(records.size() == 1 && records[0].info.generation == 7);
}
static void extra_rights(int count) {
    Pair sockets;
    auto descriptor = payload();
    auto info = metadata();
    iovec bytes{&info, sizeof(info)};
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int) * 8)]{};
    msghdr message{};
    message.msg_iov = &bytes; message.msg_iovlen = 1;
    message.msg_control = control; message.msg_controllen = CMSG_SPACE(sizeof(int) * count);
    auto *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int) * count);
    for (int i = 0; i < count; ++i) {
        int owned = descriptor.get();
        std::memcpy(CMSG_DATA(header) + sizeof(int) * i, &owned, sizeof(owned));
    }
    require(sendmsg(sockets.a.get(), &message, MSG_NOSIGNAL) == static_cast<ssize_t>(sizeof(info)));
    rejects("invalid_ancillary_data", [&] { receive_registry(sockets.b.get(), Clock::now() + 1s); });
}
static void ancillary_validation() {
    size_t before = descriptors_open();
    for (int attempt = 0; attempt < 20; ++attempt) { extra_rights(2); extra_rights(8); }
    require(descriptors_open() == before);
    for (int eof = 0; eof != 2; ++eof) {
        Pair partial;
        auto descriptor = payload();
        size_t count = descriptors_open();
        send_prefix(partial.a.get(), metadata(), descriptor.get(), 1);
        if (eof) require(shutdown(partial.a.get(), SHUT_WR) == 0);
        rejects(eof ? "socket_peer_closed" : "socket_deadline_exceeded", [&] {
            receive_registry(partial.b.get(), Clock::now() + 50ms);
        });
        require(descriptors_open() == count);
    }
    Pair sockets;
    send_record(sockets.a.get(), metadata(), -1, Clock::now() + 1s);
    rejects("record_descriptor_missing", [&] { receive_registry(sockets.b.get(), Clock::now() + 1s); });
}
static void registry_validation() {
    auto descriptor = payload();
    for (int mode = 0; mode != 8; ++mode) {
        Pair sockets;
        auto deadline = Clock::now() + 1s;
        auto info = metadata();
        const char *reason = nullptr;
        if (mode == 0) { info.planeCount = MRHAND_MAX_PLANES + 1; reason = "registry_metadata_out_of_bounds"; }
        if (mode == 1) { info.magic = 0; reason = "invalid_registry_header"; }
        if (mode == 2) { info.generation = 0; reason = "invalid_registry_header"; }
        if (mode == 3) reason = "duplicate_buffer_id";
        if (mode == 4) reason = "registry_generation_changed";
        if (mode == 5) reason = "registry_record_limit";
        if (mode == 6) { info.id = 0; reason = "terminator_has_descriptor"; }
        if (mode == 7) { info.size = 1ull << 32; reason = "registry_metadata_out_of_bounds"; }
        send_record(sockets.a.get(), info, descriptor.get(), deadline);
        if (mode == 3) send_record(sockets.a.get(), info, descriptor.get(), deadline);
        if (mode == 4) send_record(sockets.a.get(), metadata(0, 8), -1, deadline);
        if (mode == 5) {
            for (uint32_t id = 2; id <= maximum_records + 1; ++id)
                send_record(sockets.a.get(), metadata(id), descriptor.get(), deadline);
        }
        rejects(reason, [&] { receive_registry(sockets.b.get(), deadline); });
    }
}
static void receive_deadlines() {
    Pair sockets;
    auto before = Clock::now();
    rejects("socket_deadline_exceeded", [&] { receive_registry(sockets.b.get(), before + 50ms); });
    require(Clock::now() - before < 500ms);
    require(shutdown(sockets.a.get(), SHUT_WR) == 0);
    rejects("socket_peer_closed", [&] { receive_registry(sockets.b.get(), Clock::now() + 1s); });
}
static void send_deadlines() {
    Pair sockets;
    int size = 4096;
    require(setsockopt(sockets.a.get(), SOL_SOCKET, SO_SNDBUF, &size, sizeof(size)) == 0);
    char bytes[4096]{};
    while (send(sockets.a.get(), bytes, sizeof(bytes), MSG_DONTWAIT | MSG_NOSIGNAL) > 0) {}
    require(errno == EAGAIN || errno == EWOULDBLOCK);
    rejects("socket_deadline_exceeded", [&] {
        send_record(sockets.a.get(), metadata(0), -1, Clock::now() + 50ms);
    });
    sockets.b = Fd();
    rejects("socket_send_failed", [&] {
        send_record(sockets.a.get(), metadata(0), -1, Clock::now() + 1s);
    });
}
static void named_connection() {
    struct Directory {
        char name[64] = "/tmp/sightcircuit-transport-XXXXXX";
        std::string path;
        Directory() {
            require(mkdtemp(name) != nullptr);
            path = std::string(name) + "/cam.sock";
        }
        ~Directory() { unlink(path.c_str()); rmdir(name); }
    } directory;
    rejects("invalid_socket_path", [&] { connect_registry("relative", Clock::now() + 1s); });
    rejects("socket_connect_failed", [&] { connect_registry(directory.path.c_str(), Clock::now() + 1s); });
    Fd listener(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
    require(listener.get() >= 0);
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    require(directory.path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, directory.path.c_str(), directory.path.size() + 1);
    require(bind(listener.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
    require(listen(listener.get(), 1) == 0);
    auto client = connect_registry(directory.path.c_str(), Clock::now() + 1s);
    Fd server(accept4(listener.get(), nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK));
    require(server.get() >= 0);
    require_same_user(server.get());
    send_record(server.get(), metadata(0), -1, Clock::now() + 1s);
    require(receive_registry(client.get(), Clock::now() + 1s).empty());
}
int main(int argc, char **argv) {
    if (argc != 2 || std::strcmp(argv[1], "--self-test")) {
        std::cout << "{\"report_kind\":\"capture_transport_fixture\",\"schema_version\":1,\"status\":\"not_run\"}\n";
        return 3;
    }
    try {
        size_t before = descriptors_open();
        snapshot_lifetime(); fragmented_record(); ancillary_validation();
        registry_validation(); receive_deadlines(); send_deadlines(); named_connection();
        require(descriptors_open() == before);
        std::cout << "{\"report_kind\":\"capture_transport_fixture\",\"schema_version\":1,\"status\":\"passed\","
                     "\"tests_run\":7,\"descriptor_leak_detected\":false,"
                     "\"runtime_initialized\":false,\"capture_started\":false,"
                     "\"scope\":\"Synthetic Unix sockets and memfd only; no camera or Vulkan calls\"}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
