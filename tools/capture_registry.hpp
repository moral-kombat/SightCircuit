// Conservative registry continuity checks; not an exporter fence or read lease.
#pragma once
#include "capture_transport.hpp"
#include <sys/stat.h>
#include <algorithm>

namespace framejacker::capture_registry {
using transport::Error;
struct Snapshot {
    std::vector<transport::Record> records;
    uint32_t generation = 0;
    pid_t peer = 0;
    dev_t device = 0;
    ino_t inode = 0;
};
inline Snapshot fetch(const std::string &path, transport::Deadline deadline) {
    struct stat before{}, after{};
    if (lstat(path.c_str(), &before) != 0 || !S_ISSOCK(before.st_mode) ||
        before.st_uid != geteuid() || (before.st_mode & 0077)) throw Error("registry_endpoint_invalid");
    auto connection = transport::connect_registry(path.c_str(), deadline);
    ucred credentials{}; socklen_t length = sizeof(credentials);
    if (getsockopt(connection.get(), SOL_SOCKET, SO_PEERCRED, &credentials, &length) != 0 ||
        length != sizeof(credentials) || credentials.pid <= 0) throw Error("registry_peer_invalid");
    Snapshot result;
    result.records = transport::receive_registry(connection.get(), deadline, &result.generation);
    if (lstat(path.c_str(), &after) != 0 || before.st_dev != after.st_dev ||
        before.st_ino != after.st_ino || before.st_mode != after.st_mode || before.st_uid != after.st_uid)
        throw Error("registry_endpoint_changed");
    result.peer = credentials.pid; result.device = before.st_dev; result.inode = before.st_ino;
    std::sort(result.records.begin(), result.records.end(), [](const auto &a, const auto &b) {
        return a.info.id < b.info.id;
    });
    return result;
}
inline void unchanged(const Snapshot &expected, const Snapshot &actual) {
    if (!expected.generation || expected.generation != actual.generation ||
        expected.peer != actual.peer || expected.device != actual.device || expected.inode != actual.inode ||
        expected.records.size() != actual.records.size()) throw Error("registry_provider_changed");
    for (size_t i = 0; i < expected.records.size(); ++i) {
        const auto &a = expected.records[i]; const auto &b = actual.records[i];
        struct stat left{}, right{};
        if (std::memcmp(&a.info, &b.info, sizeof(a.info)) ||
            fstat(a.fd.get(), &left) != 0 || fstat(b.fd.get(), &right) != 0 ||
            left.st_dev != right.st_dev || left.st_ino != right.st_ino)
            throw Error("registry_buffer_changed");
    }
}
} // namespace framejacker::capture_registry
