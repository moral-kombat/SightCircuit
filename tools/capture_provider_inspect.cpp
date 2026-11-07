// Original SightCircuit metadata-only provider diagnostic. MIT.
// No Vulkan loader, device creation, image FD request, pixel read or GPU work.
#include "capture_provider.hpp"
#include "capture_import.hpp" // Metadata validation only; no Vulkan calls.
#include <charconv>
#include <iostream>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>

namespace p = sightcircuit::provider;
namespace t = sightcircuit::transport;
struct Entry { p::Packet packet; bool compatible; std::string reason; };
struct Report {
    std::string status = "not_run", reason;
    int code = 3;
    bool lease_established = false, registry_unchanged = false;
    uint64_t epoch = 0, generation = 0;
    pid_t peer_pid = 0; uid_t peer_uid = 0;
    unsigned checks = 0;
    std::vector<Entry> entries;
};
static void quoted(std::ostream &out, const std::string &text) {
    static const char hex[] = "0123456789abcdef";
    out << '"';
    for (unsigned char byte : text) {
        if (byte == '"' || byte == '\\') out << '\\' << static_cast<char>(byte);
        else if (byte < 32) out << "\\u00" << hex[byte >> 4] << hex[byte & 15];
        else out << static_cast<char>(byte);
    }
    out << '"';
}
static std::string uuid(const uint8_t *data) {
    static const char hex[] = "0123456789abcdef";
    std::string text;
    for (unsigned i = 0; i < 16; ++i) { text += hex[data[i] >> 4]; text += hex[data[i] & 15]; }
    return text;
}
static void emit(const Report &report) {
    std::ostringstream out;
    out << std::boolalpha << "{\"report_kind\":\"sightcircuit_provider_metadata\",\"schema_version\":1,\"status\":";
    quoted(out, report.status); out << ",\"exit_code\":" << report.code << ",\"reason\":"; quoted(out, report.reason);
    out << ",\"provider_protocol_version\":3,\"metadata_only\":true,\"image_descriptors_requested\":false,"
           "\"image_data_accessed\":false,\"frames_read\":0,\"images_saved\":false,\"gpu_import_attempted\":false,"
           "\"vulkan_loaded\":false,\"logical_device_created\":false,\"layer_installation_performed\":false,"
           "\"capture_contract_available\":false,\"producer_completion_observed\":false,\"deployment_ready\":false,"
           "\"physical_camera_identity\":\"unverified\",\"cleanup_ok\":true,\"lease_established\":" << report.lease_established
        << ",\"registry_unchanged\":" << report.registry_unchanged << ",\"registry_checks\":" << report.checks
        << ",\"provider_generation\":" << report.generation << ",\"provider_epoch\":\"" << std::hex << report.epoch
        << std::dec << "\",\"peer_pid\":" << report.peer_pid << ",\"peer_uid\":" << report.peer_uid
        << ",\"record_count\":" << report.entries.size() << ",\"reviewed_profile_candidates\":"
        << std::count_if(report.entries.begin(), report.entries.end(), [](const Entry &entry) { return entry.compatible; })
        << ",\"records\":[";
    bool comma = false;
    for (const auto &entry : report.entries) {
        const auto &packet = entry.packet; const auto &buffer = packet.buffer;
        if (comma) out << ',';
        comma = true;
        out << "{\"buffer_id\":" << buffer.id << ",\"exporter_device_token\":" << packet.device
            << ",\"device_uuid\":\"" << uuid(packet.device_uuid) << "\",\"driver_uuid\":\"" << uuid(packet.driver_uuid)
            << "\",\"width\":" << buffer.width << ",\"height\":" << buffer.height << ",\"format\":" << buffer.format
            << ",\"allocation_bytes\":" << buffer.size << ",\"bind_offset\":" << buffer.bindOffset
            << ",\"handle_type\":" << buffer.handleType << ",\"has_image\":" << buffer.hasImage
            << ",\"image_flags\":" << packet.image_flags << ",\"sharing_mode\":" << packet.sharing_mode
            << ",\"array_layers\":" << buffer.arrayLayers << ",\"depth\":" << packet.depth
            << ",\"mip_levels\":" << packet.mip_levels << ",\"samples\":" << packet.samples
            << ",\"tiling\":" << buffer.tiling << ",\"usage\":" << buffer.usage
            << ",\"has_modifier\":" << buffer.hasModifier << ",\"modifier\":\"0x" << std::hex << buffer.modifier << std::dec
            << "\",\"plane_count\":" << buffer.planeCount << ",\"metadata_complete\":" << packet.metadata_complete
            << ",\"memory_metadata_complete\":" << packet.memory_metadata_complete
            << ",\"allocation_flags\":" << packet.allocation_flags << ",\"device_mask\":" << packet.device_mask
            << ",\"reviewed_profile_compatible\":" << entry.compatible << ",\"profile_rejection_reason\":";
        quoted(out, entry.reason); out << ",\"planes\":[";
        for (unsigned i = 0; i < std::min(buffer.planeCount, uint32_t{4}); ++i) {
            if (i) out << ',';
            out << "{\"offset\":" << buffer.planeOffset[i] << ",\"row_pitch\":" << buffer.planeRowPitch[i]
                << ",\"array_pitch\":" << packet.plane_array_pitch[i] << ",\"depth_pitch\":" << packet.plane_depth_pitch[i] << '}';
        }
        out << "]}";
    }
    out << "],\"scope\":\"Provider metadata and registry stability only; no completed-frame, physical-camera, image-layout or ownership proof\"}";
    std::cout << out.str() << '\n';
}
static unsigned seconds(const char *text) {
    unsigned value = 0;
    const char *end = text + std::strlen(text);
    auto parsed = std::from_chars(text, end, value);
    if (parsed.ec != std::errc() || parsed.ptr != end || value < 1 || value > 5) throw t::Error("invalid_arguments");
    return value;
}
int main(int argc, char **argv) {
    Report report;
    bool inspect = false, has_seconds = false, has_socket = false;
    unsigned duration = 1;
    std::string path;
    if (argc == 2 && std::string(argv[1]) == "--help") {
        std::cout << "Metadata only: --inspect [--seconds 1..5] [--socket ABSOLUTE_PATH]. No image descriptors or GPU calls.\n";
        return 0;
    }
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument == "--inspect" && !inspect) inspect = true;
            else if (argument == "--seconds" && !has_seconds && i + 1 < argc) { duration = seconds(argv[++i]); has_seconds = true; }
            else if (argument == "--socket" && !has_socket && i + 1 < argc) { path = argv[++i]; has_socket = true; }
            else throw t::Error("invalid_arguments");
        }
        if (!inspect && argc > 1) throw t::Error("invalid_arguments");
    } catch (...) { report.status = "invalid_arguments"; report.code = 64; emit(report); return report.code; }
    if (!inspect) { emit(report); return report.code; }
    report.status = "inspection_failed"; report.code = 1;
    try {
        const pid_t parent = getppid();
        rlimit core{0, 0};
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent || setrlimit(RLIMIT_CORE, &core))
            throw t::Error("inspection_containment_failed");
        if (path.empty()) {
            const char *runtime = std::getenv("XDG_RUNTIME_DIR");
            if (!runtime || runtime[0] != '/') throw t::Error("runtime_directory_missing");
            path = std::string(runtime) + "/sightcircuit-camera-provider/camera.sock";
        }
        {
            p::Lease lease(path, p::Access::metadata);
            report.lease_established = true;
            report.epoch = lease.terminal().epoch; report.generation = lease.terminal().generation;
            report.peer_pid = lease.peer().pid; report.peer_uid = lease.peer().uid;
            for (const auto &record : lease.records()) {
                Entry entry{record.info, true, {}};
                try {
                    p::validate_profile(record.info);
                    sightcircuit::capture_import::validate_metadata(record.info.buffer);
                } catch (const std::exception &error) { entry.compatible = false; entry.reason = error.what(); }
                report.entries.push_back(std::move(entry));
            }
            const auto end = t::Clock::now() + std::chrono::seconds(duration);
            do {
                lease.check(); ++report.checks;
                if (t::Clock::now() >= end) break;
                // Keep the final fractional millisecond: rounding it to zero
                // can busy-poll and exceed the bounded report check count.
                std::this_thread::sleep_until(std::min(end, t::Clock::now() + std::chrono::milliseconds(100)));
            } while (true);
            report.registry_unchanged = true;
        } // Close the metadata connection before reporting completion.
        report.status = "inspected"; report.code = 0;
    } catch (const std::exception &error) { report.reason = error.what(); }
    emit(report); return report.code;
}
