#include "capture_endpoint.hpp"
#include "capture_registry.hpp"
#include <iostream>
#include <fstream>
#include <filesystem>
using namespace framejacker;
static void require(bool value) { if (!value) throw std::runtime_error("fixture_assertion_failed"); }
template <typename Action> static void rejects(Action action) {
    bool failed = false; try { action(); } catch (const std::exception &) { failed = true; }
    require(failed);
}
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    char temporary[] = "/tmp/framejacker-registry-XXXXXX";
    if (!mkdtemp(temporary)) return 1;
    const std::string root(temporary), endpoint = root + "/mrhand/cam.sock";
    struct Cleanup { std::string root; ~Cleanup() { std::filesystem::remove_all(root); } } cleanup{root};
    try {
        const std::string mode(argv[1]);
        capture_endpoint::Server server;
        transport::Fd original(open((root + "/data").c_str(), O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC, 0600));
        transport::Fd replacement(open((root + "/other").c_str(), O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC, 0600));
        require(original.get() >= 0 && replacement.get() >= 0);
        std::atomic<unsigned> generation{1}, record_id{1};
        std::atomic<int> descriptor{original.get()};
        std::atomic<bool> empty{false}, stall{false};
        auto callback = [&](int socket) {
            if (stall) { std::this_thread::sleep_for(std::chrono::milliseconds(150)); return; }
            MrhandBufInfo record{}; record.magic = MRHAND_MAGIC; record.generation = generation;
            record.id = record_id; record.size = 1024;
            auto deadline = transport::Clock::now() + std::chrono::milliseconds(100);
            if (!empty) transport::send_record(socket, record, descriptor, deadline);
            record.id = 0; transport::send_record(socket, record, -1, deadline);
        };
        auto fetch = [&] { return capture_registry::fetch(endpoint, transport::Clock::now() + std::chrono::milliseconds(100)); };
        if (mode == "ownership") {
            require(mkdir((root + "/mrhand").c_str(), 0700) == 0);
            { std::ofstream file(endpoint); file << "keep"; }
            rejects([&] { server.start(root, callback); });
            require(std::filesystem::is_regular_file(endpoint));
            require(unlink(endpoint.c_str()) == 0);
            require(symlink((root + "/data").c_str(), endpoint.c_str()) == 0);
            rejects([&] { server.start(root, callback); });
            require(std::filesystem::is_symlink(endpoint));
            require(unlink(endpoint.c_str()) == 0);
            server.start(root, callback);
            auto snapshot = fetch(); require(snapshot.records.size() == 1);
            require(unlink(endpoint.c_str()) == 0);
            { std::ofstream file(endpoint); file << "replacement"; }
            server.stop(); server.stop(); require(std::filesystem::is_regular_file(endpoint));
        } else if (mode == "continuity") {
            server.start(root, callback);
            auto snapshot = fetch(); capture_registry::unchanged(snapshot, fetch());
            generation = 2; rejects([&] { capture_registry::unchanged(snapshot, fetch()); });
            generation = 1; record_id = 2; rejects([&] { capture_registry::unchanged(snapshot, fetch()); });
            record_id = 1; descriptor = replacement.get(); rejects([&] { capture_registry::unchanged(snapshot, fetch()); });
            descriptor = original.get(); empty = true; rejects([&] { capture_registry::unchanged(snapshot, fetch()); });
            empty = false; stall = true; rejects([&] { fetch(); });
            server.stop(); rejects([&] { fetch(); });
            require(!std::filesystem::exists(endpoint));
        } else if (mode == "restart") {
            server.start(root, callback); auto before = fetch(); server.stop();
            // Retain another inode to prevent immediate endpoint inode recycling.
            { std::ofstream file(endpoint); file << "retired"; }
            require(rename(endpoint.c_str(), (root + "/retired").c_str()) == 0);
            server.start(root, callback); auto after = fetch();
            rejects([&] { capture_registry::unchanged(before, after); });
            server.stop(); require(!std::filesystem::exists(endpoint));
        } else if (mode == "private") {
            require(chmod(root.c_str(), 0755) == 0);
            rejects([&] { server.start(root, callback); });
            require(chmod(root.c_str(), 0700) == 0);
            require(mkdir((root + "/mrhand").c_str(), 0755) == 0);
            require(chmod((root + "/mrhand").c_str(), 0755) == 0);
            rejects([&] { server.start(root, callback); });
            struct stat state{}; require(stat((root + "/mrhand").c_str(), &state) == 0 && (state.st_mode & 0777) == 0755);
        } else return 64;
        server.stop();
        std::cout << "{\"status\":\"passed\",\"synthetic\":true}\n";
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
