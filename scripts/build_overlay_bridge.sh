#!/usr/bin/env bash
set -euo pipefail
bash "$(dirname -- "${BASH_SOURCE[0]}")/build_openvr_tool.sh" overlay-bridge
echo "First headset check: artifacts/overlay-bridge/sightcircuit-overlay-bridge --test-panel --seconds 10"
