<a id="frame-test-checklist-after-the-overnight-cloud-work"></a>

# Frame session entry

Start with the [current plan](live-capture.md), then use the
[Frame-session guide](guides/frame-sessions.md) to select a guided shell run,
record wearer observations or use the fixed on-device agent runner. It is the
canonical procedure for `frame_session.sh`, `observation_page.py` and
`frame_agent_run.py`.

Run device work from Frame's normal session and keep wearer reports distinct
from cloud/CI checks. The [device guide](on-device.md) covers individual mirror,
still and calibration procedures; the [panel guide](vr-overlay.md) covers replay
and VR controls. Preserve failed-run reports and inspect passthrough/tracking
before further work.

<a id="e5x-title"></a>

The former `E5x` template is replaced by [evidence maintenance](evidence/README.md).
The coordinator allocates the next unused ID, records a separate evidence file
and regenerates the status index. Do not append an invented ID to `status.md`.
