// The cpu_linear pixel path: a linear NV12 or P010 DMA-BUF is mapped read-only,
// bracketed by DMA_BUF_IOCTL_SYNC, and converted to BGR3 on the CPU with the
// frame's own YCbCr encoding at full range (camera_ycbcr.hpp). It refuses any
// buffer whose modifier is not linear: tiled or compressed layouts are the GPU
// path's. It writes nothing anywhere but the caller's buffer. No OpenCV. C++11.
#include <cerrno>
#include <cstring>
#include <string>

#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "camera_export_protocol.hpp"
#include "camera_pixels.hpp"

namespace sightcircuit_camera {
namespace {

std::string with_errno(const char* what) {
    return std::string(what) + ":errno=" + std::to_string(errno);
}

class CpuLinearPath : public PixelPath {
public:
    CpuLinearPath() : sync_unsupported_(false), sync_note_() {}

    const char* name() const { return sightcircuit_camera_export::kPixelPathCpuLinear; }
    const char* derivation() const { return sightcircuit_camera_export::kDerivationCpuConverted; }

    bool open(std::string& error, std::string& diagnostics_json) {
        (void)error;
        diagnostics_json = "{\"path\":\"cpu_linear\",\"chroma_siting\":\"nearest\",\"fixed_point_bits\":16}";
        return true;
    }

    bool convert(const SourceFrame& frame, BgrImage& out, std::string& error) {
        if (!modifier_is_linear(frame.drm_modifier)) { error = "modifier_not_linear"; return false; }
        if (frame.plane_count != 2) { error = "linear_plane_count_not_two"; return false; }
        const uint32_t bps = source_bytes_per_sample(frame.format);
        const PlaneMeta& luma = frame.planes[0];
        const PlaneMeta& chroma = frame.planes[1];
        // Row bytes: luma width samples; chroma width/2 pairs of samples = width samples.
        const uint64_t row_bytes = static_cast<uint64_t>(frame.width) * bps;
        if (luma.stride < row_bytes || chroma.stride < row_bytes) { error = "plane_stride_too_small"; return false; }
        const uint64_t luma_needed = static_cast<uint64_t>(luma.stride) * (frame.height - 1) + row_bytes;
        const uint64_t chroma_needed = static_cast<uint64_t>(chroma.stride) * (frame.height / 2 - 1) + row_bytes;
        if (luma.size < luma_needed || chroma.size < chroma_needed) { error = "plane_size_too_small"; return false; }
        const uint64_t luma_end = static_cast<uint64_t>(luma.offset) + luma.size;
        const uint64_t chroma_end = static_cast<uint64_t>(chroma.offset) + chroma.size;
        const uint64_t map_bytes = luma_end > chroma_end ? luma_end : chroma_end;
        struct stat status;
        if (::fstat(frame.fd, &status) != 0) { error = with_errno("fstat_failed"); return false; }
        if (status.st_size >= 0 && static_cast<uint64_t>(status.st_size) < map_bytes) {
            error = "planes_exceed_buffer_size"; return false;
        }
        const uint64_t payload = static_cast<uint64_t>(bgr_stride_bytes(frame.width)) * frame.height;
        if (payload > sightcircuit_camera_export::kMaxPayloadBytes) { error = "payload_exceeds_protocol_bound"; return false; }

        void* base = ::mmap(0, static_cast<std::size_t>(map_bytes), PROT_READ, MAP_SHARED, frame.fd, 0);
        if (base == MAP_FAILED) { error = with_errno("mmap_failed"); return false; }
        if (!sync(frame.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ, error)) { ::munmap(base, map_bytes); return false; }
        const uint8_t* bytes = static_cast<const uint8_t*>(base);
        out.width = frame.width;
        out.height = frame.height;
        out.stride_bytes = bgr_stride_bytes(frame.width);
        out.pixels.assign(static_cast<std::size_t>(payload), 0);
        const YcbcrCoefficients coefficients = ycbcr_coefficients(frame.encoding);
        const uint32_t sample_max = source_sample_max(frame.format);
        const uint32_t shift = source_sample_shift(frame.format);
        for (uint32_t y = 0; y < frame.height; ++y) {
            const uint8_t* luma_row = bytes + luma.offset + static_cast<uint64_t>(luma.stride) * y;
            const uint8_t* chroma_row = bytes + chroma.offset + static_cast<uint64_t>(chroma.stride) * (y / 2);
            uint8_t* out_row = &out.pixels[static_cast<std::size_t>(out.stride_bytes) * y];
            for (uint32_t x = 0; x < frame.width; ++x) {
                uint32_t luma_sample, cb, cr;
                if (bps == 1) {
                    luma_sample = luma_row[x];
                    cb = chroma_row[(x / 2) * 2];
                    cr = chroma_row[(x / 2) * 2 + 1];
                } else {
                    luma_sample = read_u16(luma_row + x * 2) >> shift;
                    cb = read_u16(chroma_row + (x / 2) * 4) >> shift;
                    cr = read_u16(chroma_row + (x / 2) * 4 + 2) >> shift;
                }
                ycbcr_to_bgr(luma_sample, cb, cr, sample_max, coefficients, out_row + x * 3);
            }
        }
        const bool ended = sync(frame.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ, error);
        ::munmap(base, static_cast<std::size_t>(map_bytes));
        return ended;
    }

    std::string notes_json() const {
        return std::string("{\"dma_buf_sync\":\"") + (sync_unsupported_ ? "not_supported_by_descriptor" : "used") + "\"}";
    }

private:
    static uint32_t read_u16(const uint8_t* p) { return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8); }

    // A descriptor that is not a DMA-BUF (the fake backend's memfd) answers
    // ENOTTY; that is recorded and reading goes on without a cache sync. Any
    // other failure is an error.
    bool sync(int fd, uint64_t flags, std::string& error) {
        struct dma_buf_sync request;
        std::memset(&request, 0, sizeof request);
        request.flags = flags;
        if (::ioctl(fd, DMA_BUF_IOCTL_SYNC, &request) == 0) return true;
        if (errno == ENOTTY || errno == EINVAL) { sync_unsupported_ = true; return true; }
        error = with_errno("dma_buf_sync_failed");
        return false;
    }

    bool sync_unsupported_;
    std::string sync_note_;
};

} // namespace

std::unique_ptr<PixelPath> make_cpu_linear_path() {
    return std::unique_ptr<PixelPath>(new CpuLinearPath());
}

} // namespace sightcircuit_camera
