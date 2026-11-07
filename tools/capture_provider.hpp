// Original SightCircuit provider contract, registry and owned endpoint. MIT.
// No Vulkan loader, GPU calls or camera acquisition.
#pragma once
#include "capture_transport.hpp"
#include <atomic>
#include <functional>
#include <fstream>
#include <map>
#include <mutex>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <thread>
#include <sstream>

namespace sightcircuit::provider {
using namespace transport;
constexpr uint32_t magic = 0x50434a46; // FJCP, a separate endpoint/ABI from MRHAND.
enum Kind : uint32_t { data = 1, end = 2, ping = 3, unchanged = 4, changed = 5,
                       request_metadata = 6, request_buffers = 7, capture_blocked = 8 };
enum class Access { metadata, buffers };
struct Packet {
    uint32_t magic_value = magic, version = 3, bytes = sizeof(Packet), kind = 0;
    uint64_t epoch = 0, generation = 0, device = 0;
    uint8_t device_uuid[16]{}, driver_uuid[16]{};
    uint32_t image_flags = 0, sharing_mode = 0, image_type = 0, depth = 0;
    uint32_t mip_levels = 0, samples = 0, external_types = 0, metadata_complete = 0;
    uint32_t allocation_flags = 0, device_mask = 0, memory_metadata_complete = 0, reserved = 0;
    uint64_t plane_array_pitch[4]{}, plane_depth_pitch[4]{};
    MrhandBufInfo buffer{};
};
static_assert(sizeof(Packet) == 328 && offsetof(Packet, device_uuid) == 40 &&
              offsetof(Packet, plane_array_pitch) == 120 && offsetof(Packet, buffer) == 184,
              "provider ABI changed");
using Record = OwnedPacket<Packet>;
inline Deadline deadline() { return Clock::now() + std::chrono::milliseconds(500); }
inline void validate_header(const Packet &packet) {
    if (packet.magic_value != magic || packet.version != 3 || packet.bytes != sizeof(Packet) ||
        !packet.epoch || !packet.generation || packet.generation > UINT32_MAX)
        throw Error("provider_protocol_mismatch");
}
inline bool nonzero(const uint8_t *uuid) {
    return std::any_of(uuid, uuid + 16, [](uint8_t value) { return value != 0; });
}
inline void validate_profile(const Packet &packet, const uint8_t *device_uuid = nullptr,
                             const uint8_t *driver_uuid = nullptr) {
    validate_header(packet);
    // Numeric constants are Vulkan ABI values; this header needs no Vulkan SDK.
    if (packet.kind != data || !packet.device || packet.metadata_complete != 1 ||
        packet.image_flags != 0 || packet.sharing_mode != 0 || packet.image_type != 1 ||
        packet.depth != 1 || packet.mip_levels != 1 || packet.samples != 1 ||
        !(packet.external_types & 0x200) || packet.allocation_flags || packet.device_mask ||
        packet.memory_metadata_complete != 1 || packet.reserved ||
        !nonzero(packet.device_uuid) || !nonzero(packet.driver_uuid) ||
        std::any_of(packet.plane_array_pitch, packet.plane_array_pitch + 4, [](uint64_t n) { return n != 0; }) ||
        std::any_of(packet.plane_depth_pitch, packet.plane_depth_pitch + 4, [](uint64_t n) { return n != 0; }))
        throw Error("provider_unsupported_exporter_metadata");
    if ((device_uuid && std::memcmp(device_uuid, packet.device_uuid, 16)) ||
        (driver_uuid && std::memcmp(driver_uuid, packet.driver_uuid, 16)))
        throw Error("provider_gpu_provenance_mismatch");
    if (packet.buffer.generation != packet.generation) throw Error("provider_generation_mismatch");
}

class Registry {
    struct Device { std::array<uint8_t, 16> uuid{}, driver{}; };
    struct Memory { Packet packet; Fd fd; uint64_t image = 0; };
    mutable std::mutex mutex_;
    std::map<uint64_t, Device> devices_;
    std::map<std::pair<uint64_t, uint64_t>, Packet> images_;
    std::map<std::pair<uint64_t, uint64_t>, Memory> memory_;
    uint64_t epoch_ = 0, generation_ = 1, next_device_ = 1;
    uint32_t next_id_ = 1;
    bool valid_ = true;
    void bump() {
        if (generation_ >= UINT32_MAX) { valid_ = false; throw Error("provider_generation_exhausted"); }
        ++generation_;
    }
    void require_device(uint64_t device) const {
        if (!valid_ || !devices_.count(device)) throw Error("provider_device_unavailable");
    }
    void store_memory(uint64_t device, uint64_t handle, Fd fd, uint64_t size,
                uint32_t type, uint64_t dedicated_image, uint32_t flags = 0,
                uint32_t mask = 0, bool complete = true) {
        std::lock_guard<std::mutex> lock(mutex_); require_device(device);
        if (!handle || !size || size > (1ULL << 30) ||
            memory_.size() >= maximum_records || memory_.count({device, handle}) || !next_id_)
            throw Error("provider_memory_registry_limit_or_reuse");
        Memory value;
        value.packet.device = device;
        value.packet.buffer.id = next_id_++;
        value.packet.buffer.size = size; value.packet.buffer.handleType = type;
        value.packet.allocation_flags = flags; value.packet.device_mask = mask;
        value.packet.memory_metadata_complete = complete ? 1 : 0;
        value.fd = std::move(fd); value.image = dedicated_image;
        memory_.emplace(std::make_pair(device, handle), std::move(value)); bump();
    }
public:
    Registry() {
        if (getrandom(&epoch_, sizeof(epoch_), 0) != sizeof(epoch_) || !epoch_)
            throw Error("provider_epoch_unavailable");
    }
    uint64_t add_device(const uint8_t *uuid, const uint8_t *driver) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!valid_ || devices_.size() >= 32 || !next_device_) throw Error("provider_device_limit");
        Device value;
        std::copy_n(uuid, 16, value.uuid.begin()); std::copy_n(driver, 16, value.driver.begin());
        const uint64_t id = next_device_++;
        devices_.emplace(id, value); bump(); return id;
    }
    void remove_device(uint64_t device) {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto i = memory_.begin(); i != memory_.end();)
            i = i->first.first == device ? memory_.erase(i) : std::next(i);
        for (auto i = images_.begin(); i != images_.end();)
            i = i->first.first == device ? images_.erase(i) : std::next(i);
        if (devices_.erase(device)) bump();
    }
    bool empty() const { std::lock_guard<std::mutex> lock(mutex_); return devices_.empty(); }
    void invalidate() noexcept { std::lock_guard<std::mutex> lock(mutex_); valid_ = false; }
    void image(uint64_t device, uint64_t handle, const Packet &description) {
        std::lock_guard<std::mutex> lock(mutex_); require_device(device);
        if (!handle || images_.size() >= 256 || images_.count({device, handle}))
            throw Error("provider_image_registry_limit_or_reuse");
        images_.emplace(std::make_pair(device, handle), description); bump();
    }
    void destroy_image(uint64_t device, uint64_t handle) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (images_.erase({device, handle})) {
            for (auto &entry : memory_)
                if (entry.first.first == device && entry.second.image == handle) entry.second.image = 0;
            bump();
        }
    }
    void memory(uint64_t device, uint64_t handle, Fd fd, uint64_t size,
                uint32_t type, uint64_t dedicated_image, uint32_t flags = 0,
                uint32_t mask = 0, bool complete = true) {
        if (fd.get() < 0) throw Error("provider_descriptor_missing");
        store_memory(device, handle, std::move(fd), size, type, dedicated_image, flags, mask, complete);
    }
    void memory_metadata(uint64_t device, uint64_t handle, uint64_t size,
                         uint32_t type, uint64_t dedicated_image, uint32_t flags,
                         uint32_t mask, bool complete) {
        store_memory(device, handle, Fd(), size, type, dedicated_image, flags, mask, complete);
    }
    void free_memory(uint64_t device, uint64_t handle) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (memory_.erase({device, handle})) bump();
    }
    void bind(uint64_t device, uint64_t memory, uint64_t image, uint64_t offset) {
        std::lock_guard<std::mutex> lock(mutex_); require_device(device);
        auto entry = memory_.find({device, memory});
        if (entry != memory_.end()) {
            entry->second.image = image; entry->second.packet.buffer.bindOffset = offset; bump();
        }
    }
    std::vector<Record> snapshot(Packet &terminal, bool copy_descriptors = true) const {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!valid_) throw Error("provider_registry_invalidated");
        std::vector<Record> result;
        for (const auto &entry : memory_) {
            const auto &value = entry.second;
            Packet packet = value.packet;
            auto image = images_.find({entry.first.first, value.image});
            if (image != images_.end()) {
                const auto &description = image->second;
                const auto allocation = packet.buffer;
                packet = description;
                packet.device = value.packet.device;
                packet.buffer.id = allocation.id; packet.buffer.size = allocation.size;
                packet.buffer.handleType = allocation.handleType; packet.buffer.bindOffset = allocation.bindOffset;
                packet.buffer.hasImage = 1;
                packet.allocation_flags = value.packet.allocation_flags;
                packet.device_mask = value.packet.device_mask;
                packet.memory_metadata_complete = value.packet.memory_metadata_complete;
            }
            auto device = devices_.find(entry.first.first);
            if (device == devices_.end()) throw Error("provider_device_unavailable");
            std::copy(device->second.uuid.begin(), device->second.uuid.end(), packet.device_uuid);
            std::copy(device->second.driver.begin(), device->second.driver.end(), packet.driver_uuid);
            packet.kind = data; packet.epoch = epoch_; packet.generation = generation_;
            packet.buffer.magic = MRHAND_MAGIC; packet.buffer.generation = static_cast<uint32_t>(generation_);
            if (copy_descriptors && value.fd.get() < 0) throw Error("provider_image_descriptor_unavailable");
            result.push_back({packet, copy_descriptors ? duplicate(value.fd.get()) : Fd()});
        }
        terminal = Packet{}; terminal.kind = end; terminal.epoch = epoch_; terminal.generation = generation_;
        return result;
    }
    Packet check(const Packet &request) const {
        validate_header(request);
        if (request.kind != ping) throw Error("provider_expected_ping");
        std::lock_guard<std::mutex> lock(mutex_);
        Packet response;
        response.epoch = epoch_; response.generation = generation_;
        response.kind = valid_ && request.epoch == epoch_ && request.generation == generation_ ? unchanged : changed;
        return response;
    }
};

inline void private_directory(const struct stat &info) {
    if (!S_ISDIR(info.st_mode) || info.st_uid != geteuid() || (info.st_mode & 0077))
        throw Error("provider_directory_not_private");
}
inline bool same(const struct stat &a, const struct stat &b) { return a.st_dev == b.st_dev && a.st_ino == b.st_ino; }
inline uint64_t process_start() {
    std::ifstream file("/proc/self/stat"); std::string line;
    if (!std::getline(file, line)) throw Error("provider_process_identity_unavailable");
    const auto boundary = line.rfind(')');
    if (boundary == std::string::npos) throw Error("provider_process_identity_unavailable");
    std::istringstream fields(line.substr(boundary + 2)); std::string field;
    for (unsigned i = 0; i <= 19; ++i) if (!(fields >> field)) throw Error("provider_process_identity_unavailable");
    return std::stoull(field);
}

class Server {
    Registry &registry_;
    Fd directory_, lock_, listener_;
    struct stat directory_identity_{}, endpoint_identity_{};
    std::string directory_path_, path_;
    std::thread thread_;
    std::atomic<bool> stopping_{false};
    std::mutex client_mutex_;
    int client_ = -1;
    bool owns_endpoint_ = false;
    bool wrote_lease_ = false;
    void client(int fd) {
        Fd connection(fd);
        { std::lock_guard<std::mutex> lock(client_mutex_); client_ = fd; }
        try {
            require_same_user(fd);
            auto request = receive_packet<Packet>(fd, deadline());
            if (request.fd.get() >= 0 || request.info.magic_value != magic || request.info.version != 3 ||
                request.info.bytes != sizeof(Packet) || request.info.epoch || request.info.generation ||
                (request.info.kind != request_metadata && request.info.kind != request_buffers))
                throw Error("provider_invalid_access_request");
            Packet terminal;
            // The live producer has no coordinated release/ready/return lease.
            // Inspect metadata without even duplicating snapshot image FDs.
            auto entries = registry_.snapshot(terminal, false);
            const auto limit = deadline();
            if (request.info.kind == request_buffers) {
                terminal.kind = capture_blocked;
                send_packet(fd, terminal, -1, limit);
                throw Error("producer_access_contract_unavailable");
            }
            for (const auto &entry : entries) send_packet(fd, entry.info, entry.fd.get(), limit);
            send_packet(fd, terminal, -1, limit);
            while (!stopping_) {
                auto request = receive_packet<Packet>(fd, deadline());
                if (request.fd.get() >= 0) throw Error("provider_unexpected_descriptor");
                const auto response = registry_.check(request.info);
                send_packet(fd, response, -1, deadline());
                if (response.kind != unchanged) break;
            }
        } catch (...) { /* End this bounded lease; never escape the server thread. */ }
        { std::lock_guard<std::mutex> lock(client_mutex_); client_ = -1; }
    }
    void serve() noexcept {
        while (!stopping_) {
            pollfd item{listener_.get(), POLLIN, 0};
            int ready = poll(&item, 1, 25);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0) break;
            if (!ready || stopping_) continue;
            int fd = accept4(listener_.get(), nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
            if (fd < 0) { if (errno == EAGAIN || errno == EINTR) continue; break; }
            client(fd);
        }
    }
public:
    explicit Server(Registry &registry) : registry_(registry) {}
    Server(const Server &) = delete;
    ~Server() { stop(); }
    void start(const std::string &runtime) {
        if (thread_.joinable()) return;
        struct stat info{};
        if (runtime.empty() || runtime[0] != '/' || lstat(runtime.c_str(), &info))
            throw Error("provider_runtime_missing");
        private_directory(info);
        directory_path_ = runtime + "/sightcircuit-camera-provider";
        if (mkdir(directory_path_.c_str(), 0700) && errno != EEXIST) throw Error("provider_mkdir_failed");
        directory_ = Fd(open(directory_path_.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        if (directory_.get() < 0 || fstat(directory_.get(), &directory_identity_)) throw Error("provider_directory_unavailable");
        private_directory(directory_identity_);
        lock_ = Fd(openat(directory_.get(), "provider.lock", O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0600));
        if (lock_.get() < 0 || fstat(lock_.get(), &info) || !S_ISREG(info.st_mode) ||
            info.st_uid != geteuid() || info.st_nlink != 1 || (info.st_mode & 0077) ||
            flock(lock_.get(), LOCK_EX | LOCK_NB)) throw Error("provider_endpoint_busy");
        path_ = directory_path_ + "/camera.sock";
        sockaddr_un address{}; address.sun_family = AF_UNIX;
        if (path_.size() >= sizeof address.sun_path) throw Error("provider_socket_path_too_long");
        std::memcpy(address.sun_path, path_.c_str(), path_.size() + 1);
        listener_ = Fd(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
        if (listener_.get() < 0 || ::bind(listener_.get(), reinterpret_cast<sockaddr *>(&address), sizeof address))
            throw Error("provider_endpoint_exists_or_bind_failed");
        if (fstatat(directory_.get(), "camera.sock", &endpoint_identity_, AT_SYMLINK_NOFOLLOW))
            throw Error("provider_endpoint_identity_failed");
        owns_endpoint_ = true;
        if (fchmodat(directory_.get(), "camera.sock", 0600, 0) || listen(listener_.get(), 4)) {
            stop(); throw Error("provider_listen_failed");
        }
        // A later recovery may remove only this endpoint, after this process and
        // its lock have gone away. Never infer ownership from a socket's name.
        const std::string lease = "{\"schema_version\":1,\"pid\":" + std::to_string(getpid()) +
            ",\"start\":" + std::to_string(process_start()) + ",\"device\":" + std::to_string(endpoint_identity_.st_dev) +
            ",\"inode\":" + std::to_string(endpoint_identity_.st_ino) + "}\n";
        if (ftruncate(lock_.get(), 0) || pwrite(lock_.get(), lease.data(), lease.size(), 0) != static_cast<ssize_t>(lease.size()) ||
            fsync(lock_.get())) { stop(); throw Error("provider_lease_write_failed"); }
        wrote_lease_ = true;
        stopping_ = false;
        try { thread_ = std::thread([this] { serve(); }); }
        catch (...) { stop(); throw; }
    }
    bool stop() noexcept {
        stopping_ = true;
        { std::lock_guard<std::mutex> lock(client_mutex_); if (client_ >= 0) shutdown(client_, SHUT_RDWR); }
        if (thread_.joinable()) thread_.join();
        listener_ = Fd();
        bool clean = true;
        if (owns_endpoint_) {
            struct stat info{}, directory{};
            if (lstat(directory_path_.c_str(), &directory) || !same(directory, directory_identity_) ||
                fstatat(directory_.get(), "camera.sock", &info, AT_SYMLINK_NOFOLLOW) ||
                !same(info, endpoint_identity_) || unlinkat(directory_.get(), "camera.sock", 0)) clean = false;
            owns_endpoint_ = false;
        }
        if (clean && wrote_lease_ && lock_.get() >= 0 && ftruncate(lock_.get(), 0)) clean = false;
        wrote_lease_ = false;
        lock_ = Fd(); directory_ = Fd(); return clean;
    }
    const std::string &path() const { return path_; }
};

class Lease {
    Fd connection_;
    Packet terminal_;
    std::vector<Record> records_;
    ucred peer_{};
public:
    explicit Lease(const std::string &path, Access access = Access::buffers) {
        const auto parent = path.substr(0, path.rfind('/'));
        Fd directory(open(parent.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct stat info{};
        if (directory.get() < 0 || fstat(directory.get(), &info)) throw Error("provider_directory_unavailable");
        private_directory(info);
        if (lstat(path.c_str(), &info) || !S_ISSOCK(info.st_mode) || info.st_uid != geteuid() ||
            (info.st_mode & 0077)) throw Error("provider_endpoint_not_private");
        const auto limit = deadline(); connection_ = connect_registry(path.c_str(), limit);
        socklen_t peer_size = sizeof peer_;
        if (getsockopt(connection_.get(), SOL_SOCKET, SO_PEERCRED, &peer_, &peer_size) || peer_size != sizeof peer_)
            throw Error("provider_peer_identity_unavailable");
        Packet request;
        request.kind = access == Access::metadata ? request_metadata : request_buffers;
        send_packet(connection_.get(), request, -1, limit);
        std::set<uint32_t> identifiers;
        for (;;) {
            auto record = receive_packet<Packet>(connection_.get(), limit);
            validate_header(record.info);
            if (record.info.kind == capture_blocked) throw Error("producer_access_contract_unavailable");
            if (!records_.empty() && (record.info.epoch != records_[0].info.epoch ||
                record.info.generation != records_[0].info.generation)) throw Error("provider_snapshot_changed");
            if (record.info.kind == end) {
                if (record.fd.get() >= 0) throw Error("provider_terminator_descriptor");
                terminal_ = record.info; break;
            }
            if (record.info.kind != data || (access == Access::buffers) != (record.fd.get() >= 0) || !record.info.buffer.id ||
                records_.size() == maximum_records || !identifiers.insert(record.info.buffer.id).second)
                throw Error("provider_invalid_record");
            records_.push_back(std::move(record));
        }
    }
    const std::vector<Record> &records() const { return records_; }
    const Packet &terminal() const { return terminal_; }
    const ucred &peer() const { return peer_; }
    void check() {
        Packet request = terminal_; request.kind = ping;
        const auto limit = deadline();
        send_packet(connection_.get(), request, -1, limit);
        auto response = receive_packet<Packet>(connection_.get(), limit);
        validate_header(response.info);
        if (response.fd.get() >= 0 || response.info.kind != unchanged || response.info.epoch != terminal_.epoch ||
            response.info.generation != terminal_.generation) throw Error("provider_registry_changed");
    }
};
} // namespace sightcircuit::provider
