// The camera helper's pixel paths: the one interface behind which a held
// camera buffer becomes BGR3 pixels in host memory. Two implementations:
// cpu_linear (camera_pixels_cpu.cpp: maps the linear planes, the fixed-point
// conversion of camera_ycbcr.hpp) and gpu_convert (camera_pixels_gpu.cpp:
// Vulkan import by DRM format modifier, a compute shader with the same
// formula, host readback). The helper picks one per run; the import inside
// gpu_convert is a stage of its own, so it can be swapped without touching the
// helper. No SDK type appears here. C++11.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "camera_backend.hpp"
#include "camera_ycbcr.hpp"

namespace sightcircuit_camera {

// The largest frame either path converts; the protocol's payload bound is checked separately.
const uint32_t kMaxSourceDimension = 8192;

enum class SourceFormat { nv12, p010 };

inline const char* source_format_name(SourceFormat format) {
    return format == SourceFormat::p010 ? "p010" : "nv12";
}
inline uint32_t source_bytes_per_sample(SourceFormat format) { return format == SourceFormat::p010 ? 2u : 1u; }
inline uint32_t source_sample_max(SourceFormat format) { return format == SourceFormat::p010 ? 1023u : 255u; }
// P010 stores each 10-bit sample in the high bits of a 16-bit word.
inline uint32_t source_sample_shift(SourceFormat format) { return format == SourceFormat::p010 ? 6u : 0u; }

// One camera of a held frame set, as the pixel paths see it. The descriptor is
// borrowed from the backend's lease and is never closed here.
struct SourceFrame {
    int fd = -1;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t drm_fourcc = 0;
    uint64_t drm_modifier = 0;
    uint32_t plane_count = 0;
    PlaneMeta planes[kMaxPlanes];
    SourceFormat format = SourceFormat::nv12;
    YcbcrEncoding encoding = YcbcrEncoding::bt709;
};

// BGR3 pixels, rows of stride_bytes with zero padding after the last pixel.
struct BgrImage {
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t stride_bytes = 0;
    std::vector<uint8_t> pixels;
};

// Rows are padded to whole groups of four pixels (twelve bytes), so the GPU
// path's four-pixel work items never cross a row; the CPU path uses the same
// stride so both paths deliver identical buffers.
inline uint32_t bgr_stride_bytes(uint32_t width) { return ((width + 3u) / 4u) * 12u; }

inline bool modifier_is_linear(uint64_t modifier) { return modifier == kDrmModifierLinear; }

// Builds the pixel paths' view of a camera from the backend's metadata.
// Refuses an unknown pixel format or encoding, a FourCC that disagrees with
// the pixel format, an odd or oversized dimension or a plane count other than
// two for a linear buffer (tiled buffers may carry more planes; the GPU path
// checks them against the driver).
inline bool source_frame_from_meta(const CameraMeta& meta, int fd, SourceFrame& frame, std::string& error) {
    frame = SourceFrame();
    frame.fd = fd;
    frame.width = meta.width;
    frame.height = meta.height;
    frame.drm_fourcc = meta.drm_format_value;
    frame.drm_modifier = meta.drm_modifier_value;
    frame.plane_count = meta.plane_count;
    for (uint32_t plane = 0; plane < meta.plane_count && plane < kMaxPlanes; ++plane) frame.planes[plane] = meta.planes[plane];
    if (meta.pixel_format == "nv12" || meta.pixel_format == "nv12_ubwc") frame.format = SourceFormat::nv12;
    else if (meta.pixel_format == "p010" || meta.pixel_format == "p010_ubwc") frame.format = SourceFormat::p010;
    else { error = "pixel_format_unknown:" + meta.pixel_format; return false; }
    const uint32_t expected_fourcc = frame.format == SourceFormat::p010 ? kDrmFourccP010 : kDrmFourccNv12;
    if (frame.drm_fourcc != expected_fourcc) { error = "drm_format_disagrees_with_pixel_format:" + meta.drm_format; return false; }
    if (!parse_ycbcr_encoding(meta.ycbcr_encoding, frame.encoding)) { error = "ycbcr_encoding_unknown:" + meta.ycbcr_encoding; return false; }
    if (frame.width == 0 || frame.height == 0 || frame.width > kMaxSourceDimension || frame.height > kMaxSourceDimension) {
        error = "dimensions_out_of_range"; return false;
    }
    if (frame.width % 2 || frame.height % 2) { error = "dimensions_not_even"; return false; }
    if (frame.plane_count == 0 || frame.plane_count > kMaxPlanes) { error = "plane_count_out_of_range"; return false; }
    if (modifier_is_linear(frame.drm_modifier) && frame.plane_count != 2) { error = "linear_plane_count_not_two"; return false; }
    if (fd < 0) { error = "buffer_descriptor_missing"; return false; }
    return true;
}

class PixelPath {
public:
    virtual ~PixelPath() {}
    virtual const char* name() const = 0;        // cpu_linear or gpu_convert
    virtual const char* derivation() const = 0;  // cpu_converted or gpu_converted
    // Prepares the path before the first frame (the GPU path loads the Vulkan
    // loader, picks a device and builds its pipeline here). On success,
    // diagnostics is a JSON object with the facts the ready packet reports.
    virtual bool open(std::string& error, std::string& diagnostics_json) = 0;
    // Converts one camera's buffer into out. The descriptor stays open and
    // the frame set stays held by the caller throughout.
    virtual bool convert(const SourceFrame& frame, BgrImage& out, std::string& error) = 0;
    // Facts noted while converting (for example that the descriptor is not a
    // DMA-BUF), as a JSON object; reported once at the end.
    virtual std::string notes_json() const { return "{}"; }
};

// Defined in camera_pixels_cpu.cpp and camera_pixels_gpu.cpp respectively.
std::unique_ptr<PixelPath> make_cpu_linear_path();
std::unique_ptr<PixelPath> make_gpu_convert_path();

} // namespace sightcircuit_camera
