// Private endpoint ownership and joined server lifetime. No configuration edits.
#pragma once
#include "capture_transport.hpp"
#include <atomic>
#include <functional>
#include <mutex>
#include <thread>
#include <sys/stat.h>

namespace framejacker::capture_endpoint {
using transport::Error;
class Server {
    std::mutex lifecycle_;
    std::atomic<bool> stopped_{false};
    std::thread thread_;
    transport::Fd socket_;
    std::string path_;
    struct stat identity_{};
    bool owns_ = false;
    void cleanup() noexcept {
        socket_ = transport::Fd();
        if (owns_) {
            struct stat current{};
            if (lstat(path_.c_str(), &current) == 0 && current.st_dev == identity_.st_dev &&
                current.st_ino == identity_.st_ino && S_ISSOCK(current.st_mode)) unlink(path_.c_str());
            owns_ = false;
        }
    }
    static void private_directory(const std::string &path) {
        struct stat info{};
        if (lstat(path.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) ||
            info.st_uid != geteuid() || (info.st_mode & 0077)) throw Error("endpoint_directory_invalid");
    }
public:
    Server() = default;
    Server(const Server &) = delete;
    Server &operator=(const Server &) = delete;
    ~Server() { stop(); }
    void start(const std::string &runtime, std::function<void(int)> client) {
        std::lock_guard<std::mutex> lock(lifecycle_);
        if (thread_.joinable()) return;
        if (runtime.empty() || runtime[0] != '/') throw Error("endpoint_runtime_missing");
        private_directory(runtime);
        const std::string directory = runtime + "/mrhand";
        if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST) throw Error("endpoint_directory_create_failed");
        private_directory(directory);
        path_ = directory + "/cam.sock";
        sockaddr_un address{}; address.sun_family = AF_UNIX;
        if (path_.size() >= sizeof(address.sun_path)) throw Error("endpoint_path_too_long");
        std::memcpy(address.sun_path, path_.c_str(), path_.size() + 1);
        try {
            socket_ = transport::Fd(socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
            if (socket_.get() < 0 || bind(socket_.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0)
                throw Error("endpoint_bind_failed"); // Never unlink an existing name to take it over.
            if (lstat(path_.c_str(), &identity_) != 0 || !S_ISSOCK(identity_.st_mode))
                throw Error("endpoint_identity_failed");
            owns_ = true;
            if (chmod(path_.c_str(), 0600) != 0 || listen(socket_.get(), 4) != 0)
                throw Error("endpoint_listen_failed");
            stopped_ = false;
            thread_ = std::thread([this, client = std::move(client)] {
                while (!stopped_) {
                    pollfd event{socket_.get(), POLLIN, 0};
                    int status = poll(&event, 1, 100);
                    if (status < 0 && errno == EINTR) continue;
                    if (status < 0 || (event.revents & (POLLERR | POLLHUP | POLLNVAL))) break;
                    if (!status || stopped_) continue;
                    transport::Fd connection(accept4(socket_.get(), nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC));
                    if (connection.get() < 0) {
                        if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                        break;
                    }
                    try { client(connection.get()); } catch (...) { /* owned connection still closes */ }
                }
            });
        } catch (...) { cleanup(); throw; }
    }
    void stop() noexcept {
        std::lock_guard<std::mutex> lock(lifecycle_);
        stopped_ = true;
        if (thread_.joinable()) thread_.join();
        cleanup();
    }
};
} // namespace framejacker::capture_endpoint
