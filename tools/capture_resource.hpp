// Transactional Vulkan ownership. Configure destruction before acquisition.
#pragma once
#include "capture_import.hpp"
#include <functional>

namespace framejacker::capture_resource {
template <typename Handle> class Unique {
    capture_import::Completion *completion_ = nullptr;
    std::function<void(Handle)> destroy_;
    Handle value_ = VK_NULL_HANDLE;
public:
    Unique() = default;
    Unique(capture_import::Completion &completion, std::function<void(Handle)> destroy)
        : completion_(&completion), destroy_(std::move(destroy)) {}
    ~Unique() { close(); }
    Unique(const Unique &) = delete;
    Unique &operator=(const Unique &) = delete;
    Unique(Unique &&other) noexcept { *this = std::move(other); }
    Unique &operator=(Unique &&other) noexcept {
        if (this != &other) {
            close(); completion_ = other.completion_; destroy_ = std::move(other.destroy_);
            value_ = std::exchange(other.value_, VK_NULL_HANDLE);
        }
        return *this;
    }
    template <typename Create> void acquire(Create create) {
        if (!completion_ || !destroy_ || value_ || !completion_->known_idle)
            throw transport::Error("resource_acquisition_state");
        Handle value = VK_NULL_HANDLE;
        // Failed calls may leave undefined outputs. Adopt only VK_SUCCESS.
        capture_import::check(create(&value), "resource_acquisition_failed");
        if (!value) throw transport::Error("resource_null_handle");
        value_ = value;
    }
    Handle get() const { return value_; }
    bool close() noexcept {
        if (!value_) return true;
        const bool safe = completion_->known_idle;
        if (safe) destroy_(value_);
        else completion_->cleanup_deferred = true;
        value_ = VK_NULL_HANDLE;
        return safe;
    }
};
} // namespace framejacker::capture_resource
