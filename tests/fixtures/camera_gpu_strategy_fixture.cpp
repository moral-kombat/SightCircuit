// Runs each import strategy of the camera helper's gpu_convert pixel path
// (camera_pixels_gpu.cpp) on a buffer this fixture makes itself, a memfd filled
// with pseudo-random NV12 and P010 samples, and compares every byte with the
// cpu_linear path's conversion of the same buffer. The helper has no flag that
// chooses a strategy (the driver's answers do); make_gpu_convert_path's
// only_strategy argument exists for this fixture. Prints one JSON line per
// strategy, "driver" for the unrestricted choice first. No camera, no SDK, no
// file. Exit 0 when every line was printed, 2 when no Vulkan device opened.
// C++17.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include <sys/mman.h>
#include <unistd.h>

#include "camera_pixels.hpp"

using namespace sightcircuit_camera;

namespace {

// Width not a multiple of four (the output rows are padded), strides wider
// than the rows and a gap before the chroma plane: nothing may read padding.
const uint32_t kWidth = 70, kHeight = 38;

struct Buffer {
    int fd = -1;
    SourceFrame frame;
};

bool make_buffer(SourceFormat format, YcbcrEncoding encoding, uint32_t seed, Buffer& buffer) {
    const uint32_t bytes_per_sample = source_bytes_per_sample(format);
    const uint32_t stride = 96 * bytes_per_sample;
    const uint32_t luma_size = stride * kHeight;
    const uint32_t chroma_offset = luma_size + 4096;
    const uint32_t chroma_size = stride * (kHeight / 2);
    const std::size_t total = ((chroma_offset + chroma_size + 4095u) / 4096u) * 4096u;
    buffer.fd = ::memfd_create("sightcircuit-gpu-strategy", MFD_CLOEXEC);
    if (buffer.fd < 0 || ::ftruncate(buffer.fd, static_cast<off_t>(total)) != 0) return false;
    void* mapped = ::mmap(nullptr, total, PROT_READ | PROT_WRITE, MAP_SHARED, buffer.fd, 0);
    if (mapped == MAP_FAILED) return false;
    uint8_t* bytes = static_cast<uint8_t*>(mapped);
    uint32_t state = seed * 2654435761u + 12345u;
    for (std::size_t index = 0; index < total; ++index) {
        state = state * 1664525u + 1013904223u;
        bytes[index] = static_cast<uint8_t>(state >> 24);
    }
    // The extremes, where clamping and rounding decide: the first luma row's start and the first chroma pairs.
    const uint8_t extremes[4] = {0x00, 0xff, 0x00, 0xff};
    std::memcpy(bytes, extremes, 4);
    std::memset(bytes + chroma_offset, 0x00, 4 * bytes_per_sample);
    std::memset(bytes + chroma_offset + 4 * bytes_per_sample, 0xff, 4 * bytes_per_sample);
    ::munmap(mapped, total);
    SourceFrame& frame = buffer.frame;
    frame.fd = buffer.fd;
    frame.width = kWidth;
    frame.height = kHeight;
    frame.format = format;
    frame.drm_fourcc = format == SourceFormat::p010 ? kDrmFourccP010 : kDrmFourccNv12;
    frame.drm_modifier = kDrmModifierLinear;
    frame.plane_count = 2;
    frame.planes[0].offset = 0;
    frame.planes[0].stride = stride;
    frame.planes[0].size = luma_size;
    frame.planes[1].offset = chroma_offset;
    frame.planes[1].stride = stride;
    frame.planes[1].size = chroma_size;
    frame.encoding = encoding;
    return true;
}

std::string quoted(const std::string& text) {
    std::string out = "\"";
    for (char c : text) {
        if (c == '"' || c == '\\') out += '\\';
        out += c;
    }
    return out + "\"";
}

// One strategy (nullptr: whichever the driver accepts first) over both formats and the three encodings.
void run(const char* only_strategy) {
    const std::string label = only_strategy ? only_strategy : "driver";
    std::unique_ptr<PixelPath> gpu = make_gpu_convert_path(only_strategy);
    std::unique_ptr<PixelPath> cpu = make_cpu_linear_path();
    std::string error, facts = "{}", cpu_facts;
    std::string status = "identical", detail;
    unsigned cases = 0;
    std::string planned = "{}";
    if (!cpu->open(error, cpu_facts) || !gpu->open(error, facts)) {
        status = "open_failed";
        detail = error;
    } else {
        const SourceFormat formats[2] = {SourceFormat::nv12, SourceFormat::p010};
        const YcbcrEncoding encodings[3] = {YcbcrEncoding::bt601, YcbcrEncoding::bt709, YcbcrEncoding::bt2020};
        for (int format = 0; format < 2 && status == "identical"; ++format) {
            for (int encoding = 0; encoding < 3 && status == "identical"; ++encoding) {
                Buffer buffer;
                if (!make_buffer(formats[format], encodings[encoding], static_cast<uint32_t>(format * 3 + encoding + 1), buffer)) {
                    status = "buffer_failed";
                    break;
                }
                if (cases == 0) {
                    // What the helper does before its ready packet: the layout alone, no descriptor.
                    SourceFrame layout = buffer.frame;
                    layout.fd = -1;
                    planned = facts;
                    gpu->plan(layout, planned);
                }
                BgrImage expected, actual;
                if (!cpu->convert(buffer.frame, expected, error)) {
                    status = "cpu_failed";
                    detail = error;
                } else if (!gpu->convert(buffer.frame, actual, error)) {
                    status = "unsupported";
                    detail = error;
                } else if (actual.width != expected.width || actual.height != expected.height ||
                           actual.stride_bytes != expected.stride_bytes || actual.pixels != expected.pixels) {
                    status = "mismatch";
                    std::size_t differing = 0, first = 0;
                    for (std::size_t index = 0; index < expected.pixels.size() && index < actual.pixels.size(); ++index) {
                        if (expected.pixels[index] != actual.pixels[index] && !differing++) first = index;
                    }
                    detail = std::string(source_format_name(formats[format])) + ":" + ycbcr_encoding_name(encodings[encoding]) +
                             ":bytes_differing=" + std::to_string(differing) + ":first=" + std::to_string(first);
                } else {
                    ++cases;
                }
                ::close(buffer.fd);
            }
        }
    }
    std::printf("{\"strategy\":%s,\"status\":%s,\"detail\":%s,\"cases\":%u,\"bytes_per_case\":%u,\"planned_facts\":%s,"
                "\"notes\":%s}\n",
                quoted(label).c_str(), quoted(status).c_str(), quoted(detail).c_str(), cases,
                bgr_stride_bytes(kWidth) * kHeight, planned.c_str(), gpu->notes_json().c_str());
}

} // namespace

int main() {
    {
        std::unique_ptr<PixelPath> probe = make_gpu_convert_path();
        std::string error, facts;
        if (!probe->open(error, facts)) {
            std::printf("{\"strategy\":\"driver\",\"status\":\"open_failed\",\"detail\":%s}\n", quoted(error).c_str());
            return 2;
        }
    }
    run(nullptr);
    run("plane_views_uint");
    run("plane_views_unorm");
    run("ycbcr_sampler");
    std::fflush(stdout);
    return 0;
}
