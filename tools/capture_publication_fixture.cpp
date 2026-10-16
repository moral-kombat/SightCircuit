// Generated luma only. Exercises the same publisher used by the adaptation.
#include "capture_publication.hpp"
#include <iostream>
#include <memory>
#include <sys/resource.h>
#include <thread>

using namespace framejacker::publication;

static void require(bool condition) { if (!condition) throw Error("fixture_assertion_failed"); }
template <typename Function> static void rejects(Function action) {
    try { action(); } catch (const std::exception &) { return; }
    throw Error("fixture_expected_rejection");
}
static int self_test() {
    struct Directory {
        char path[64] = "/tmp/framejacker-publication-XXXXXX";
        Directory() { require(mkdtemp(path) != nullptr); }
        ~Directory() { rmdir(path); }
    } directory;
    const std::string path = std::string(directory.path) + "/frames";
    std::vector<uint8_t> pixels(64 * 48, 77);
    {
        Publisher publisher(path, 64, 48, 2);
        rejects([&] { Publisher other(path, 64, 48, 2); });
        require(publisher.publish(0, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 11, 1));
        const uint64_t now = framejacker_shm::nowNs();
        require(publisher.publish(1, pixels.data(), pixels.size(), now, 12, 1));
        require(!publisher.publish(1, pixels.data(), pixels.size(), now, 12, 2));
        require(!publisher.publish(1, pixels.data(), pixels.size(), now > 2000000000ull ? now - 2000000000ull : 1, 12, 2));
        rejects([&] { publisher.publish(1, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 12, 1); });
        rejects([&] { publisher.publish(2, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 12, 2); });
        rejects([&] { publisher.publish(0, pixels.data(), 1, framejacker_shm::nowNs(), 12, 2); });
        rejects([&] { publisher.publish(0, pixels.data(), pixels.size(), UINT64_MAX, 12, 2); });
        require(chmod(path.c_str(), 0644) == 0);
        rejects([&] { publisher.publish(0, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 11, 2); });
        require(chmod(path.c_str(), 0600) == 0);
        require(publisher.close() && publisher.close());
        require(access(path.c_str(), F_OK) != 0);
    }
    {
        Publisher publisher(path, 64, 48, 1);
        require(truncate(path.c_str(), 0) == 0);
        rejects([&] { publisher.publish(0, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 1, 1); });
        require(publisher.close());
    }
    rejects([&] { Publisher invalid(path, 8192, 8192, 2); });
    require(chmod(directory.path, 0755) == 0);
    rejects([&] { Publisher invalid(path, 64, 48, 2); });
    require(chmod(directory.path, 0700) == 0);
    {
        struct Environment {
            bool present = std::getenv("XDG_RUNTIME_DIR") != nullptr;
            std::string previous = present ? std::getenv("XDG_RUNTIME_DIR") : "";
            ~Environment() {
                if (present) setenv("XDG_RUNTIME_DIR", previous.c_str(), 1);
                else unsetenv("XDG_RUNTIME_DIR");
            }
        } environment;
        require(setenv("XDG_RUNTIME_DIR", directory.path, 1) == 0);
        const auto source = runtime_source();
        { Publisher publisher(source, 64, 48, 1); require(publisher.close()); }
        require(rmdir(std::filesystem::path(source).parent_path().c_str()) == 0);
        require(setenv("XDG_RUNTIME_DIR", "/tmp", 1) == 0);
        rejects([&] { runtime_source(); });
        require(setenv("XDG_RUNTIME_DIR", "relative", 1) == 0);
        rejects([&] { runtime_source(); });
        require(unsetenv("XDG_RUNTIME_DIR") == 0);
        rejects([&] { runtime_source(); });
    }
    {
        RunLimit limit(1);
        require(limit.running());
        int configured = 0;
        require(prctl(PR_GET_PDEATHSIG, &configured) == 0 && configured == SIGKILL);
        require(raise(SIGINT) == 0);
        require(!limit.running() && limit.signal() == SIGINT);
    }
    std::cout << "{\"report_kind\":\"capture_publication_fixture\",\"schema_version\":1,"
                 "\"status\":\"passed\",\"synthetic\":true,\"runtime_initialized\":false,\"capture_started\":false}\n";
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 1) {
        std::cout << "{\"status\":\"not_run\",\"synthetic\":true,\"exit_code\":3}\n";
        return 3;
    }
    try {
        if (std::string(argv[1]) == "--check-options") {
            auto options = parse_options(argc - 1, argv + 1);
            int code = options.mode == Mode::not_run ? 3 : 0;
            std::cout << "{\"options_checked\":true,\"runtime_initialized\":false,\"capture_started\":false,"
                         "\"seconds\":" << options.seconds << ",\"fps\":" << options.fps << "}\n";
            return code;
        }
        if (argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return std::string(argv[1]) == "--check-options" ? 64 : 1;
    }
    unsigned seconds = 3;
    std::string path, hang;
    // Synthetic stuck-driver points. No Vulkan, camera or real provider is used.
    auto hang_here = [&hang](const char *phase) {
        if (hang != phase) return;
        signal(SIGTERM, SIG_IGN);
        signal(SIGINT, SIG_IGN);
        std::cout << "{\"event\":\"synthetic_hang\",\"phase\":\"" << phase << "\"}\n" << std::flush;
        for (;;) pause();
    };
    try {
        if (argc == 7 && std::string(argv[5]) == "--watchdog-hang") {
            hang = argv[6];
            if (hang != "startup" && hang != "publication" && hang != "shutdown")
                throw Error("invalid_arguments");
        } else if (argc != 5) throw Error("invalid_arguments");
        if (std::string(argv[1]) != "--create" || std::string(argv[3]) != "--seconds")
            throw Error("invalid_arguments");
        path = argv[2];
        seconds = unsigned(framejacker_shm::number(argv[4], 1, 10));
    } catch (...) { return 64; }
    std::unique_ptr<RunLimit> limit;
    std::unique_ptr<Publisher> publisher;
    int code = 0;
    std::string reason = "deadline";
    uint64_t count = 0;
    try {
        rlimit core{0, 0};
        if (setrlimit(RLIMIT_CORE, &core) != 0) throw Error("core_limit_failed");
        limit = std::make_unique<RunLimit>(seconds);
        hang_here("startup");
        publisher = std::make_unique<Publisher>(path, 1024, 1024, 2);
        std::vector<uint8_t> pixels(1024 * 1024);
        std::cout << "{\"event\":\"publication_ready\",\"synthetic\":true}\n" << std::flush;
        while (limit->running()) {
            ++count;
            for (unsigned camera = 0; camera != 2; ++camera) {
                std::fill(pixels.begin(), pixels.end(), uint8_t((count + camera * 17) % 256));
                publisher->publish(camera, pixels.data(), pixels.size(), framejacker_shm::nowNs(), 100 + camera, count);
                hang_here("publication");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(16));
        }
        if (limit->signal()) { reason = "signal"; code = 128 + limit->signal(); }
    } catch (const std::exception &error) { reason = error.what(); code = 1; }
    hang_here("shutdown");
    const bool clean = !publisher || publisher->close();
    if (!clean) code = 1;
    if (reason.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != std::string::npos)
        reason = "native_error";
    std::cout << std::boolalpha << "{\"event\":\"publication_closed\",\"synthetic\":true,\"reason\":\"" << reason
              << "\",\"frames_per_slot\":" << count << ",\"cleanup_ok\":" << clean << ",\"exit_code\":" << code << "}\n";
    return code;
}
