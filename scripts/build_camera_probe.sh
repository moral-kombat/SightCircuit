#!/usr/bin/env bash
set -euo pipefail
bash "$(dirname -- "${BASH_SOURCE[0]}")/build_openvr_tool.sh" camera-probe
echo "The probe has interrupted headset passthrough; default execution makes no runtime calls."
echo "Use --help to read the explicit opt-in. Do not repeat the unchanged headset test."
