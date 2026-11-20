#!/usr/bin/env bash
set -euo pipefail
bash "$(dirname -- "${BASH_SOURCE[0]}")/build_openvr_tool.sh" presence-watcher
echo "Offline check: artifacts/presence-watcher/sightcircuit-presence-watcher --describe (no SteamVR call)"
