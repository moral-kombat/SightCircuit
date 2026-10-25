#!/usr/bin/env bash
# One guided, bounded Frame session: regression, pacing, live mirror, stills, detector
# comparison and the window-in-mirror check. Run from the graphical session on Frame:
#
#   bash scripts/frame_session.sh [stage ...]
#
# Stages (default: all except `overlay` and `window-mirror`, which need you in the headset):
#   setup regression pacing overlay mirror stills evaluate window-mirror
# Environment: DEVICE (default /dev/video99), RECORDING (default newest recording),
#   SKIP_UPDATE=1 (do not git pull), YES=1 (no prompts; observations recorded as "unanswered").
#
# Everything is bounded and read-only toward SteamVR: no layers, no restarts, no probe.
# Results go to artifacts/frame-session/<timestamp>/ with summary.md ready to paste
# into docs/status.md. These are user-run device results; the script does not judge them.
set -uo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
device="${DEVICE:-/dev/video99}"
py=.venv-replay/bin/python
stamp="$(date +%Y%m%d-%H%M%S)"
out="$root/artifacts/frame-session/$stamp"
mkdir -p "$out" evaluation/local
summary="$out/summary.md"
declare -A results

say() { printf '\n== %s\n' "$*"; }
note() { printf '%s\n' "$*" >> "$summary"; }
ask() { # ask "question" -> records answer
  local answer="unanswered"
  if [ -z "${YES:-}" ]; then read -r -p "$1 " answer || answer="unanswered"; fi
  note "- $1 **${answer:-unanswered}**"
}
confirm() { [ -n "${YES:-}" ] || { read -r -p "$1 [Enter to continue, s to skip] " reply; [ "$reply" != s ]; }; }
run() { # run name command... -> records exit code, keeps going on failure
  local name="$1"; shift
  say "$name"
  "$@"; local code=$?
  results[$name]=$code
  note "- \`$name\`: exit $code"
  return 0
}
open_image() { command -v xdg-open >/dev/null && [ -z "${YES:-}" ] && xdg-open "file://$1" >/dev/null 2>&1 || true; }

recording_args=()
[ -n "${RECORDING:-}" ] && recording_args=("$RECORDING")

{
  echo "# Frame session $stamp"
  echo
  echo "Revision: $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD)); host: $(uname -srm)"
  echo "Device: $device. User-run on Frame; interpret per docs/morning-checklist.md."
  echo
} > "$summary"

stage_setup() {
  if [ -z "${SKIP_UPDATE:-}" ]; then run git-pull git pull --ff-only; fi
  run setup-window bash scripts/setup_window.sh
  run build-overlay-bridge bash scripts/build_overlay_bridge.sh
}

stage_regression() {
  note; note "## Refactor regression (E49 on device)"
  run replay-offscreen env QT_QPA_PLATFORM=offscreen "$py" scripts/replay_window.py "${recording_args[@]}" \
    --max-frames 30 --report "$out/replay-offscreen.json"
}

stage_pacing() {
  note; note "## Realtime replay pacing"
  run replay-realtime env QT_QPA_PLATFORM=offscreen "$py" scripts/replay_window.py "${recording_args[@]}" \
    --realtime --max-frames 100 --report "$out/replay-realtime.json"
  run replay-unpaced env QT_QPA_PLATFORM=offscreen "$py" scripts/replay_window.py "${recording_args[@]}" \
    --max-frames 100 --report "$out/replay-unpaced.json"
  "$py" - "$out" <<'PY' >> "$summary"
import json, sys
from pathlib import Path
for name in ("replay-unpaced", "replay-realtime"):
    path = Path(sys.argv[1]) / f"{name}.json"
    if not path.exists():
        print(f"- {name}: no report"); continue
    r = json.loads(path.read_text())
    print(f"- {name}: status={r.get('status')} processed={r.get('frames_processed')} "
          f"fps={r.get('processed_fps')} dropped={r.get('frames_dropped')} "
          f"mean_ms={r.get('mean_processing_ms')} max_age_ms={r.get('max_input_age_ms')}")
PY
}

stage_overlay() {
  note; note "## Replay with VR panel (E47 regression)"
  confirm "Put the headset on; a head-relative panel will show the replay for ~10 s." || return 0
  run replay-overlay "$py" scripts/replay_window.py "${recording_args[@]}" --overlay --realtime \
    --max-frames 100 --report "$out/replay-overlay.json"
  ask "Was the panel visible with labels, and did passthrough/tracking stay normal during and after? (y/n/notes)"
}

stage_mirror() {
  note; note "## Bounded mirror recognition (E48 regression)"
  confirm "Aim at a chair/TV/person and keep it in view for 10 s." || return 0
  run mirror-recognize "$py" scripts/mirror_recognize.py --capture --device "$device" --seconds 10 \
    --max-fps 5 --preview "$out/mirror-detections.png" > "$out/mirror-recognize.json"
  note "  report: mirror-recognize.json, preview: mirror-detections.png"
  open_image "$out/mirror-detections.png"
  ask "Do the preview boxes/labels match what was in view, and was passthrough normal? (y/n/notes)"
}

stage_stills() {
  note; note "## Evaluation stills (private, evaluation/local/)"
  echo "Capture stills for the evaluation set. Suggested: id 'rug' (absent: couch), 'chair-tv'."
  while true; do
    local id=""
    if [ -z "${YES:-}" ]; then read -r -p "Scene id (blank to finish): " id; else id=""; fi
    [ -z "$id" ] && break
    local present absent
    read -r -p "Labels present (COCO names, comma-separated): " present
    read -r -p "Labels that must NOT be reported (e.g. couch): " absent
    local file="evaluation/local/$id-$stamp.png"
    confirm "Aim at '$id' and hold for 5 s." || continue
    "$py" scripts/mirror_recognize.py --capture --device "$device" --seconds 5 --detector yolox-nano \
      --still "$file" > "$out/still-$id.json"
    code=$?
    results[still-$id]=$code
    if [ "$code" = 0 ] && [ -f "$file" ]; then
      "$py" scripts/frame_session_manifest.py --manifest evaluation/local/frame.json --id "$id-$stamp" \
        --image "$file" --present "$present" --absent "$absent" && note "- still \`$id\`: $file added to frame.json"
    else
      note "- still \`$id\`: capture failed (exit $code)"
    fi
  done
}

stage_evaluate() {
  note; note "## Detector comparison on Frame stills"
  if [ ! -f evaluation/local/frame.json ]; then note "- skipped: no evaluation/local/frame.json"; return 0; fi
  run evaluate "$py" scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
    --report "$out/detector-report.json"
  note "  report: detector-report.json (choose the default detector from this)"
}

stage_window_mirror() {
  note; note "## Does the desktop window appear in the SteamVR mirror? (E18 analogue)"
  confirm "A replay window opens for ~15 s; keep it in the headset view while a mirror capture runs." || return 0
  "$py" scripts/replay_window.py "${recording_args[@]}" --realtime --max-frames 150 \
    --report "$out/window-replay.json" > /dev/null 2>&1 &
  local pid=$!
  sleep 4
  run mirror-check python3 scripts/mirror_check.py --capture --device "$device" --seconds 5 \
    --preview "$out/window-in-mirror.png"
  wait "$pid"
  open_image "$out/window-in-mirror.png"
  ask "Does window-in-mirror.png show the desktop replay window? (y/n)"
  ask "Was passthrough/tracking normal during and after? (y/n/notes)"
}

stages=("$@")
[ ${#stages[@]} -eq 0 ] && stages=(setup regression pacing mirror stills evaluate)
for stage in "${stages[@]}"; do
  case "$stage" in
    setup|regression|pacing|overlay|mirror|stills|evaluate) "stage_$stage" ;;
    window-mirror) stage_window_mirror ;;
    *) echo "Unknown stage: $stage" >&2; exit 2 ;;
  esac
done

failed=0
for name in "${!results[@]}"; do [ "${results[$name]}" = 0 ] || failed=1; done
note; note "Outcome: $([ $failed = 0 ] && echo 'all commands exited 0' || echo 'some commands failed (see exit codes)')."
say "Summary: $summary"
cat "$summary"
exit $failed
