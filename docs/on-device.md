# Continue development on Steam Frame

The next hardware task is to establish the headset's execution context and
camera capabilities. The repository is published at
[moral-kombat/framejacker](https://github.com/moral-kombat/framejacker).
The application, live capture backend and VR overlay are still unimplemented.

## Confirm where commands run

A web interface is useful if its terminal or development agent executes on the
headset. A browser on Frame may instead be displaying a cloud session, a PC
terminal or an isolated container. Opening this chat on Frame does not move its
attached cloud execution environment onto the headset.

Use an existing terminal/editor on the headset, or a development session already
connected to it. A browser-only page cannot directly provide the SteamVR/Linux
camera interfaces needed by this project. Existing terminal access does not
require changing operating-system protections or restarting SteamVR.

## Starting with only a browser

The simplest development connection is a terminal on another computer on the
same network, connected to Frame over SSH. The [Valve setup reference][valve-setup]
and [community Frame instructions][frame-ssh] are the relevant references. The
community instructions describe these menus:

1. On Frame, open **Steam Settings → System → Enable Developer Mode**.
2. Open the **Developer** settings and choose **Set User Password**. Enter the
   password on the device, not in a chat or committed configuration file.
3. Find the headset's IP address under **Steam Settings → Internet**.
4. From a terminal on another computer on that network, connect using the normal
   user account documented by the guide:

   ```bash
   ssh steamos@<FRAME_IP>
   ```

Replace `<FRAME_IP>` with the address shown on the headset. Once connected,
commands execute on Frame. Menu labels and access can vary with runtime builds;
follow the current Valve reference if they differ. Enabling developer access is
a manual device step; no script in this repository performs it.

If Frame is the only available computer, a local terminal/development app is
needed before these commands can run. A browser-only chat does not supply that
execution connection, and this cloud session cannot automatically reach a
headset on a private home network. Establish the local terminal or a supported
remote development connection before proceeding.

After shell access works, an authenticated browser editor/terminal can be
considered for convenience. A browser-based camera dashboard is a separate app
feature: it would communicate with a backend running on Frame, which owns the
capture adapter and detector. A web page alone does not establish Arcturus
capture capability. Start with terminal access rather than deploying a new
web service during the initial camera investigation.

## Obtain the repository

In a writable development directory belonging to your normal user, clone the
repository if it is not already present:

```bash
git clone https://github.com/moral-kombat/framejacker.git
cd framejacker
```

If a checkout already exists, preserve its local changes and use it. Update a
clean checkout of `main` with `git pull --ff-only`; do not reset local work to
match these instructions. Codex tasks are already isolated when applicable;
do not create a worktree unless explicitly requested.

## Run the device inventory first

Use the headset's available Python 3 interpreter. This script needs only the
standard library and does not require the project's virtual environment:

```bash
python3 scripts/device_check.py
```

The report includes architecture, selected OS/build identifiers, tools on PATH,
the result of a user-level SteamVR service query, video-device names and render
node presence. It does not open cameras, acquire a stream, restart services,
change settings, install packages or print credential/environment values.

Share the generated JSON in the development session. Architecture and OS help
distinguish the headset from the cloud machine, but neither establishes access
to the headset's compositor session. Missing services or commands may reflect
terminal/container isolation rather than absent software. Listed device nodes
do not prove working capture or GPU access.

## Then verify camera access

The first user-supplied inventory confirms an `aarch64` SteamOS 0.4.5 session
(build `20261007.6125817`), Python 3.12.3, an active `steamvr.service`, native
`g++`/CMake/Ninja tools, a render node and `arcimx616` sensor-driver nodes. This
establishes the target context; it does not demonstrate a connected RGB stream
or working public camera capture.

Keep the headset awake, the camera connected, and SteamVR running. Run the
probe as the normal `steamos` user in that session:

```bash
bash scripts/build_camera_probe.sh
artifacts/camera-probe/framejacker-camera-probe
```

The build uses Git, Bash and a C++17 compiler. It needs no `uv`, Python packages,
OpenGL or Vulkan development packages. It fetches the pinned Valve OpenVR SDK
sources into `.cache/openvr` and compiles the portable loader for the current
machine. The binary goes in `artifacts/camera-probe/`. Both paths are ignored by
Git. A modified or different cached SDK causes an error rather than a reset.

The probe uses the public `IVRTrackedCamera_006` interface in a background OpenVR
application. It queries presence, count/layout, frame dimensions and calibration
statuses, then requests a shared camera stream. It checks header-only retrieval
for all three frame types and retries failed size queries after acquisition.
Where dimensions are valid, it attempts an actual RGBA frame copy. A successful
handle alone cannot pass the check.

The retrieval loop has a shared three-second polling budget and a 64 MiB buffer
limit; synchronous runtime calls can add time. It reports metadata and named
errors as JSON, saves no images, and releases the stream before shutting down
OpenVR. It does not change SteamVR settings, open physical video nodes, install
compositor layers, or select an RGB/monochrome source. A received frame does not
by itself identify the Arcturus RGB source.

| Exit code | Meaning |
| --- | --- |
| `0` | At least one frame was copied with valid RGBA metadata and stream release succeeded |
| `1` | Runtime initialization, interface access, cleanup or another probe error; inspect the JSON |
| `2` | Runtime/interface access succeeded, but no valid frame was received |

An unavailable runtime registry or `NoServerForBackgroundApp` is a runtime/session
problem, not proof of unsupported camera hardware. Keep the existing home/XDG
and OpenVR runtime configuration; the loader discovers SteamVR through its
registered paths. Background initialization does not launch SteamVR.

To save a report and preserve the probe's exit status in a terminal:

```bash
(
  set -e
  bash scripts/build_camera_probe.sh
  framejacker_probe_status=0
  artifacts/camera-probe/framejacker-camera-probe \
    > artifacts/camera-probe/report.json || framejacker_probe_status=$?
  python3 -m json.tool artifacts/camera-probe/report.json || cat artifacts/camera-probe/report.json
  printf 'Probe exit code: %s\n' "$framejacker_probe_status"
  exit "$framejacker_probe_status"
)
```

Share the report for diagnosis. The next steps depend on the observed API errors,
frame format, source identity and timing data. If the public API does not provide
frames, establish a supported Arcturus or Valve capture route before selecting
an experimental backend. The community Vulkan capture path described in the
[camera guide](steam-frame-camera.md) remains a separate investigation. Neither
an active service nor listed sensor nodes replace actual frame retrieval.

Do not copy the cloud machine's x86_64 `.venv` to Frame. The existing dependency
pins and smoke setup were validated on Linux x86_64, not the headset; verify
native interpreter, wheel and library availability before installing them there.
The device inventory requires no such installation. Leave compositor-layer
deployment and camera-source changes out of this initial check.

Once capture is understood, continue with the [replay/window milestone](architecture.md#milestones-and-acceptance-checks),
connect the live adapter, and validate a VR panel. Offline splat reconstruction
can proceed separately on a suitable GPU computer.

## Context for a new development session

```text
Continue moral-kombat/framejacker on this Steam Frame.
Read README.md, docs/on-device.md and docs/steam-frame-camera.md.
First confirm where this terminal executes and run python3 scripts/device_check.py.
Treat device presence as inventory, not camera validation. Build and run the
public OpenVR probe before choosing the live camera backend. Keep runtime/session
errors separate from unavailable-camera results.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
