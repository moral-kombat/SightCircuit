// Fake dispatch covers the exact decoder/retirement helpers used by OpenVR.
#include "../../tools/overlay_frame.hpp"
#include <cassert>
#include <string>

using namespace sightcircuit_overlay;
struct FakeOverlay {
    unsigned calls = 0;
    void frame(std::uint32_t w, std::uint32_t h, const void* data) {
        const auto* pixels = static_cast<const std::uint8_t*>(data);
        assert(w == 2 && h == 1);
        for (unsigned i = 0; i < 8; ++i) assert(pixels[i] == i + 101);
        ++calls;
    }
};
void append32(std::vector<std::uint8_t>& bytes, std::uint32_t n) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back((n >> shift) & 255);
}
int main() {
    constexpr std::uint64_t now = 0x123456789ULL;
    constexpr std::uint64_t deadline = now + 100000000;
    std::vector<std::uint8_t> payload;
    append32(payload, deadline & 0xffffffff); append32(payload, deadline >> 32);
    append32(payload, 2); append32(payload, 1);
    for (unsigned i = 0; i < 8; ++i) payload.push_back(i + 101);
    const auto frame = decode_frame(4, payload);
    assert(frame.expiry == deadline && frame.rgba == payload.data() + 16);
    assert(deadline_current(frame, now));
    assert(!deadline_current(frame, deadline));
    assert(!deadline_current(frame, deadline + 1));
    FakeOverlay fake;
    dispatch_frame(fake, frame);
    payload.erase(payload.begin(), payload.begin() + 8);
    const auto plain = decode_frame(1, payload);
    assert(plain.expiry == 0 && plain.rgba == payload.data() + 8);
    dispatch_frame(fake, plain);
    assert(fake.calls == 2);
    payload.pop_back();
    bool rejected = false;
    try { decode_frame(1, payload); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    // The generated display-age cap is accepted exactly and rejected one nanosecond later.
    constexpr auto cap = sightcircuit_contract::kMaxDisplayAgeNs;
    assert(deadline_current({1, 1, now + cap, nullptr}, now));
    for (auto expiry : {std::uint64_t{0}, now + cap + 1}) {
        rejected = false;
        try { deadline_current({1, 1, expiry, nullptr}, now); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
    // Every failure position still attempts every teardown call, in order.
    for (unsigned fail = 0; fail <= 3; ++fail) {
        std::string calls;
        const auto retired = retire_overlay(
            [&] { calls += "hide "; return fail != 1; },
            [&] { calls += "clear "; return fail != 2; },
            [&] { calls += "destroy"; return fail != 3; });
        assert(calls == "hide clear destroy");
        assert(retired.ok() == (fail == 0));
    }
}
