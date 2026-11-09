// Hashes standard input with the camera helper's own SHA-256 and prints the hex
// digest, so the test can compare it with Python's hashlib. C++11.
#include <cstdio>
#include <unistd.h>

#include "camera_sha256.hpp"

int main() {
    sightcircuit_camera::Sha256 hash;
    char buffer[4096];
    for (;;) {
        const ssize_t count = ::read(STDIN_FILENO, buffer, sizeof buffer);
        if (count <= 0) break;
        hash.update(buffer, static_cast<std::size_t>(count));
    }
    std::printf("%s\n", hash.hex_digest().c_str());
    return 0;
}
