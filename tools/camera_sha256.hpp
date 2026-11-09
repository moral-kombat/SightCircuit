// SHA-256 (FIPS 180-4) for the camera helper's digests; original SightCircuit code.
//
// C++11, header-only, no third-party code and no libcrypto: the SDK-linked helper
// links only the Client Library archive and pthread. tests/test_camera_helper_boundaries.py
// checks it against Python's hashlib.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace sightcircuit_camera {

class Sha256 {
public:
    Sha256() { reset(); }

    void reset() {
        static const uint32_t initial[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                                            0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
        std::memcpy(state_, initial, sizeof state_);
        length_ = 0;
        buffered_ = 0;
    }

    void update(const void* data, std::size_t size) {
        const unsigned char* bytes = static_cast<const unsigned char*>(data);
        length_ += static_cast<uint64_t>(size);
        while (size) {
            std::size_t take = 64 - buffered_;
            if (take > size) take = size;
            std::memcpy(buffer_ + buffered_, bytes, take);
            buffered_ += take;
            bytes += take;
            size -= take;
            if (buffered_ == 64) {
                compress(buffer_);
                buffered_ = 0;
            }
        }
    }

    // Finishes the hash and returns 64 lowercase hex digits; call reset() to reuse.
    std::string hex_digest() {
        const uint64_t bits = length_ * 8u;
        static const unsigned char pad[64] = {0x80};
        const std::size_t pad_size = buffered_ < 56 ? 56 - buffered_ : 120 - buffered_;
        update(pad, pad_size);
        unsigned char tail[8];
        for (int i = 0; i < 8; ++i) tail[i] = static_cast<unsigned char>(bits >> (56 - 8 * i));
        update(tail, 8);
        static const char digits[] = "0123456789abcdef";
        std::string out;
        out.reserve(64);
        for (int i = 0; i < 8; ++i) {
            for (int shift = 28; shift >= 0; shift -= 4) out.push_back(digits[(state_[i] >> shift) & 0xfu]);
        }
        return out;
    }

private:
    static uint32_t rotr(uint32_t value, unsigned count) { return (value >> count) | (value << (32 - count)); }

    void compress(const unsigned char* block) {
        static const uint32_t k[64] = {
            0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
            0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
            0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
            0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
            0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
            0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
            0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
            0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};
        uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = (static_cast<uint32_t>(block[4 * i]) << 24) | (static_cast<uint32_t>(block[4 * i + 1]) << 16) |
                   (static_cast<uint32_t>(block[4 * i + 2]) << 8) | static_cast<uint32_t>(block[4 * i + 3]);
        }
        for (int i = 16; i < 64; ++i) {
            const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
        for (int i = 0; i < 64; ++i) {
            const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const uint32_t choice = (e & f) ^ (~e & g);
            const uint32_t t1 = h + s1 + choice + k[i] + w[i];
            const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const uint32_t t2 = s0 + majority;
            h = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }

    uint32_t state_[8];
    uint64_t length_;
    unsigned char buffer_[64];
    std::size_t buffered_;
};

inline std::string sha256_hex(const void* data, std::size_t size) {
    Sha256 hash;
    hash.update(data, size);
    return hash.hex_digest();
}

inline std::string sha256_hex(const std::string& text) { return sha256_hex(text.data(), text.size()); }

} // namespace sightcircuit_camera
