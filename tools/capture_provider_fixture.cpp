// Original synthetic provider tests. No Vulkan loader or camera.
#include "capture_provider.hpp"
#include <filesystem>
#include <iostream>
#include <sys/mman.h>
namespace p = framejacker::provider;
namespace t = framejacker::transport;
static void require(bool value) { if (!value) throw t::Error("fixture_assertion"); }
template<class F> static void rejects(F call) {
    bool rejected = false; try { call(); } catch (const std::exception &) { rejected = true; } require(rejected);
}
struct Temporary {
    std::string path;
    Temporary() { char name[] = "/tmp/fj-provider-XXXXXX"; char *value = mkdtemp(name); if (!value) throw t::Error("mkdtemp"); path = value; }
    ~Temporary() { std::filesystem::remove_all(path); }
};
static p::Packet description() {
    p::Packet packet;
    packet.image_type = 1; packet.depth = packet.mip_levels = packet.samples = packet.metadata_complete = 1;
    packet.external_types = 0x200;
    packet.buffer.format = 1000156003; packet.buffer.width = packet.buffer.height = 2464;
    packet.buffer.arrayLayers = 1; packet.buffer.tiling = 1000158000;
    packet.buffer.hasModifier = 1; packet.buffer.modifier = 0x0500000000000001;
    packet.buffer.planeCount = 2; packet.buffer.planeRowPitch[0] = packet.buffer.planeRowPitch[1] = 2560;
    packet.buffer.planeOffset[1] = 8 << 20; packet.buffer.usage = 5;
    return packet;
}
struct Source {
    p::Registry registry;
    uint8_t uuid[16]{1}, driver[16]{2};
    uint64_t device;
    Source() {
        device = registry.add_device(uuid, driver);
        registry.image(device, 10, description());
        t::Fd memory(memfd_create("generated-provider", MFD_CLOEXEC));
        if (memory.get() < 0 || ftruncate(memory.get(), 16 << 20)) throw t::Error("memfd");
        registry.memory(device, 20, std::move(memory), 16 << 20, 0x200, 10);
    }
};
static void metadata() {
    Source source; p::Packet terminal;
    auto records = source.registry.snapshot(terminal); require(records.size() == 1);
    const auto valid = records[0].info;
    p::validate_profile(valid, source.uuid, source.driver);
    for (int field = 0; field < 15; ++field) {
        auto packet = valid;
        switch (field) {
        case 0: packet.version = 1; break; case 1: packet.bytes--; break;
        case 2: packet.image_flags = 1; break; case 3: packet.sharing_mode = 1; break;
        case 4: packet.image_type = 2; break; case 5: packet.depth = 2; break;
        case 6: packet.mip_levels = 2; break; case 7: packet.samples = 2; break;
        case 8: packet.external_types = 0; break; case 9: packet.metadata_complete = 0; break;
        case 10: packet.allocation_flags = 1; break; case 11: packet.device_mask = 1; break;
        case 12: packet.memory_metadata_complete = 0; break;
        case 13: packet.plane_array_pitch[0] = 1; break; case 14: packet.plane_depth_pitch[0] = 1; break;
        }
        rejects([&] { p::validate_profile(packet, source.uuid, source.driver); });
    }
    uint8_t other[16]{3};
    rejects([&] { p::validate_profile(valid, other, source.driver); });
    rejects([&] { p::validate_profile(valid, source.uuid, other); });
}
static void device_scope() {
    Source source; p::Packet terminal;
    const auto second = source.registry.add_device(source.uuid, source.driver);
    source.registry.image(second, 10, description());
    t::Fd memory(memfd_create("second", MFD_CLOEXEC));
    source.registry.memory(second, 20, std::move(memory), 16 << 20, 0x200, 10);
    auto records = source.registry.snapshot(terminal);
    require(records.size() == 2 && records[0].info.buffer.id != records[1].info.buffer.id);
    source.registry.remove_device(source.device);
    records = source.registry.snapshot(terminal);
    require(records.size() == 1 && records[0].info.device == second);
    source.registry.destroy_image(second, 10);
    records = source.registry.snapshot(terminal); require(records[0].info.buffer.hasImage == 0);
    source.registry.remove_device(second); require(source.registry.empty());
}
static void snapshot_ownership() {
    Source source; p::Packet terminal;
    auto records = source.registry.snapshot(terminal);
    const auto original = records[0].info.buffer.id;
    source.registry.free_memory(source.device, 20);
    require(fcntl(records[0].fd.get(), F_GETFD) & FD_CLOEXEC);
    char byte; require(pread(records[0].fd.get(), &byte, 1, 0) == 1);
    t::Fd memory(memfd_create("replacement", MFD_CLOEXEC));
    source.registry.memory(source.device, 20, std::move(memory), 16 << 20, 0x200, 10);
    auto next = source.registry.snapshot(terminal);
    require(next[0].info.buffer.id > original && next[0].info.generation != records[0].info.generation);
}
static void generations() {
    Temporary temporary; Source source; p::Server server(source.registry); server.start(temporary.path);
    p::Lease lease(server.path(), p::Access::metadata); lease.check();
    source.registry.bind(source.device, 20, 10, 0);
    rejects([&] { lease.check(); });
    p::Lease next(server.path(), p::Access::metadata); next.check();
    source.registry.destroy_image(source.device, 10);
    rejects([&] { next.check(); });
    require(server.stop());
}
static void provider_loss() {
    Temporary temporary; Source source; p::Server server(source.registry); server.start(temporary.path);
    p::Lease lease(server.path(), p::Access::metadata); lease.check();
    const auto started = t::Clock::now(); require(server.stop());
    require(t::Clock::now() - started < std::chrono::seconds(1));
    rejects([&] { lease.check(); });
    server.start(temporary.path);
    p::Lease next(server.path(), p::Access::metadata); next.check();
    source.registry.invalidate(); rejects([&] { next.check(); });
    require(server.stop());
}
static void endpoint_ownership() {
    Temporary temporary; Source source; p::Server server(source.registry); server.start(temporary.path);
    p::Server other(source.registry); rejects([&] { other.start(temporary.path); }); other.stop();
    require(std::filesystem::file_size(temporary.path + "/framejacker-camera-provider/provider.lock") > 0);
    const auto path = server.path(); require(unlink(path.c_str()) == 0);
    { t::Fd file(open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600)); require(file.get() >= 0); }
    require(!server.stop() && std::filesystem::is_regular_file(path));
    rejects([&] { server.start(temporary.path); }); server.stop();
    std::filesystem::remove(path);
    server.start(temporary.path); require(server.stop());
    chmod(temporary.path.c_str(), 0755);
    rejects([&] { server.start(temporary.path); });
}
static void idle_shutdown() {
    Temporary temporary; Source source; p::Server server(source.registry); server.start(temporary.path);
    auto connection = t::connect_registry(server.path().c_str(), p::deadline());
    const auto started = t::Clock::now(); require(server.stop());
    require(t::Clock::now() - started < std::chrono::seconds(1));
}
static void bounded_registry() {
    Source source;
    for (uint64_t handle = 21; handle < 84; ++handle) {
        t::Fd memory(memfd_create("bounded", MFD_CLOEXEC));
        source.registry.memory(source.device, handle, std::move(memory), 16 << 20, 0x200, 10);
    }
    rejects([&] { source.registry.memory(source.device, 100, t::Fd(memfd_create("too-many", MFD_CLOEXEC)),
                                         16 << 20, 0x200, 10); });
    source.registry.invalidate(); p::Packet end;
    rejects([&] { source.registry.snapshot(end); });
}
static void metadata_only() {
    Temporary temporary; Source source;
    // Match the production layer: imported allocation metadata is retained,
    // but no image descriptor is retained or available for snapshot export.
    source.registry.free_memory(source.device, 20);
    source.registry.memory_metadata(source.device, 20, 16 << 20, 0x200, 10, 0, 0, true);
    p::Packet terminal;
    rejects([&] { source.registry.snapshot(terminal); });
    p::Server server(source.registry); server.start(temporary.path);
    {
        p::Lease lease(server.path(), p::Access::metadata);
        require(lease.records().size() == 1 && lease.records()[0].fd.get() == -1);
        p::validate_profile(lease.records()[0].info, source.uuid, source.driver);
        lease.check();
    }
    rejects([&] { p::Lease unsafe(server.path(), p::Access::buffers); });
    // Refusing an access request must not leave a stale client descriptor or
    // prevent a later metadata-only observer.
    { p::Lease next(server.path(), p::Access::metadata); next.check(); }
    require(server.stop());
}
int main(int argc, char **argv) {
    if (argc == 1) { std::cout << "{\"status\":\"not_run\",\"exit_code\":3}\n"; return 3; }
    if ((argc == 3 || argc == 4) && std::string(argv[1]) == "--serve-generated") {
        try { Source source; p::Server server(source.registry); server.start(argv[2]);
              const std::string mode = argc == 4 ? argv[3] : "normal";
              if (mode == "empty") source.registry.free_memory(source.device, 20);
              if (mode == "unsupported") {
                  source.registry.destroy_image(source.device, 10);
                  auto image = description(); image.image_flags = 1;
                  source.registry.image(source.device, 10, image); source.registry.bind(source.device, 20, 10, 0);
              }
              std::cout << "ready" << std::endl;
              if (mode == "change" || mode == "drop") {
                  std::this_thread::sleep_for(std::chrono::milliseconds(800));
                  if (mode == "change") source.registry.bind(source.device, 20, 10, 0);
                  else server.stop();
              }
              for (;;) pause(); }
        catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
    }
    const std::pair<const char *, void(*)()> tests[] = {{"metadata", metadata}, {"device_scope", device_scope},
        {"snapshot_ownership", snapshot_ownership}, {"generations", generations}, {"provider_loss", provider_loss},
        {"endpoint_ownership", endpoint_ownership}, {"idle_shutdown", idle_shutdown}, {"bounded_registry", bounded_registry},
        {"metadata_only", metadata_only}};
    const bool all = argc == 2 && std::string(argv[1]) == "--self-test";
    const bool one = argc == 3 && std::string(argv[1]) == "--case";
    unsigned ran = 0;
    try {
        for (const auto &test : tests) if (all || (one && std::string(argv[2]) == test.first)) { test.second(); ++ran; }
        if (!ran) return 64;
        std::cout << "{\"report_kind\":\"capture_provider_fixture\",\"schema_version\":1,\"status\":\"passed\",\"tests_run\":"
                  << ran << ",\"runtime_initialized\":false,\"capture_started\":false,\"scope\":\"Synthetic registry and local sockets only\"}\n";
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
