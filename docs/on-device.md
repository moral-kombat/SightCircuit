# Work on Steam Frame

<a id="continue-development-on-steam-frame"></a>

Use this page to establish where commands run, connect to Frame, and check its
inventory. Then choose one task guide. [Status](status.md) records verified
capabilities; the [current plan](live-capture.md) owns the next proposed work.

| Task | Guide |
| --- | --- |
| Inspect, decode, and replay a completed recording | [Replay a recording](guides/replay.md) |
| Show the replay in a headset panel | [VR replay panel](vr-overlay.md) |
| Discover the mirror, recognize objects, or collect stills | [Mirror recognition](guides/mirror-recognition.md) |
| Run a guided session or collect wearer observations | [Frame sessions](guides/frame-sessions.md) |
| Prepare a reviewed calibration checkpoint | [HUD checkpoint](guides/hud-checkpoint.md) |
| Inspect paused-provider build procedures | [Retained device checks](reference/capture-device-checks.md) |

Read prerequisites and expected results before running a device command.
A cloud terminal and a browser displayed on Frame are different execution
contexts. Keep device reports, wearer observations, and cloud/CI results distinct.

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
git clone https://github.com/moral-kombat/SightCircuit.git sightcircuit
cd sightcircuit
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

The report includes architecture, selected OS/build identifiers, the version of
the Python interpreter that ran it (3.14 since SteamOS 0.5.8; the tested
versions are in [development setup](development.md#python-314-on-the-frame-2026-11-07)), tools on PATH,
the result of a user-level SteamVR service query, video-device names and render
node presence. It does not open cameras, acquire a stream, restart services,
change settings, install packages or print credential/environment values.

Share the generated JSON in the development session. Architecture and OS help
distinguish the headset from the cloud machine, but neither establishes access
to the headset's compositor session. Missing services or commands may reflect
terminal/container isolation rather than absent software. Listed device nodes
do not prove working capture or GPU access.

## Then verify camera access

Establish the baseline using the [official Arcturus Camera app][arcturus-camera]
and [vendor setup instructions][arcturus-howto]. Confirm that its live view and
recording work before investigating a new capture route. Preserve completed
recordings and their metadata for the replay checks below.

See [inventory evidence](status.md#e1-frame-inventory) and
[vendor-app evidence](status.md#e2-vendor-app) for the reported baseline.
Device-node presence does not demonstrate frame access. Keep SightCircuit's
live-input work separate from the vendor app and use the active plan for a
new capture trial.

### Confirmed public-probe result

**The public camera probe returned no frames and blacked out passthrough while
running on the tested runtime. Do not repeat that unchanged headset test.**
A future retest needs a material change, such as a runtime update or revised
capture implementation. The detailed API failures and their limits are in
[the evidence record](status.md#e3-public-camera-probe).

The binary makes no OpenVR calls by default. Its explicit interruption flag
and [retained advanced procedure](reference/capture-device-checks.md#advanced-probe-only-for-a-deliberate-future-retest)
are documented separately. A nonzero streaming handle alone does not establish
usable capture.


## Verify independent SSH access

For device work that needs an independent terminal, verify access that does
not depend on the headset's VR console. On **another computer on the same network**, run:

```bash
ssh -o ConnectTimeout=10 steamos@<FRAME_IP> 'hostname && id -un'
```

Expect the Frame hostname (`frame`) and account name (`steamos`). If prompted
for a password, use the Frame account's password. This command reads host/user
information only; it does not restart SteamVR, enable a layer or access the
camera. Run it from the other computer, not from the Frame console itself.

Share the output or connection error. If it fails, preserve the working
headset session and diagnose remote access before any trial. Once access is
verified, review the bounded recovery procedure before reassessing activation;
the access check alone does not validate service restoration.

## Earlier section links

<details>
<summary>Expand links from the previous guide</summary>

Older links remain valid. Each section below points to its maintained guide or
retained reference.

### Prepare a recording for replay

See [Prepare a recording for replay](guides/replay.md#prepare-a-recording-for-replay).

### Proposed recording growth test

See [Proposed recording growth test](history/device-handoffs.md#proposed-recording-growth-test).

### Decode one recorded video track

See [Decode one recorded video track](guides/replay.md#decode-one-recorded-video-track).

### Open the annotated replay window

See [Open the annotated replay window](guides/replay.md#open-the-annotated-replay-window).

### Check an existing SteamVR mirror

See [Check an existing SteamVR mirror](guides/mirror-recognition.md#check-an-existing-steamvr-mirror).

### Run recognition on the saved mirror image

See [Run recognition on the saved mirror image](guides/mirror-recognition.md#run-recognition-on-the-saved-mirror-image).

### Run bounded recognition from the mirror

See [Run bounded recognition from the mirror](guides/mirror-recognition.md#run-bounded-recognition-from-the-mirror).

### Compare detectors on Frame stills

See [Compare detectors on Frame stills](guides/mirror-recognition.md#compare-detectors-on-frame-stills).

### Check whether the VR panel appears in mirror input

See [Check whether the VR panel appears in mirror input](guides/mirror-recognition.md#check-whether-the-vr-panel-appears-in-mirror-input).

### Record headset observations for a run

See [Record headset observations for a run](guides/frame-sessions.md#record-headset-observations-for-a-run).

### Run automated checks with an on-device agent

See [Run automated checks with an on-device agent](guides/frame-sessions.md#run-automated-checks-with-an-on-device-agent).

#### Preflight advice from another Frame project (upstream, not our evidence)

See [Preflight advice from another Frame project (upstream, not our evidence)](guides/frame-sessions.md#preflight-advice-from-another-frame-project-upstream-not-our-evidence).

### Measure the observer's scene precondition on saved stills

See [Measure the observer's scene precondition on saved stills](guides/hud-checkpoint.md#measure-the-observers-scene-precondition-on-saved-stills).

### Bounded edge HUD calibration checkpoint

See [Bounded edge HUD calibration checkpoint](guides/hud-checkpoint.md#bounded-edge-hud-calibration-checkpoint).

#### Before the device run

See [Before the device run](guides/hud-checkpoint.md#before-the-device-run).

#### Approved run

See [Approved run](guides/hud-checkpoint.md#approved-run).

#### Reading the result

See [Reading the result](guides/hud-checkpoint.md#reading-the-result).

### Check native capture prerequisites

See [Check native capture prerequisites](reference/capture-device-checks.md#check-native-capture-prerequisites).

### Build the capture reference without activation

See [Build the capture reference without activation](reference/capture-device-checks.md#build-the-capture-reference-without-activation).

### Test the transport adaptation without capture

See [Test the transport adaptation without capture](reference/capture-device-checks.md#test-the-transport-adaptation-without-capture).

### Test owned publication without capture

See [Test owned publication without capture](reference/capture-device-checks.md#test-owned-publication-without-capture).

### Query Vulkan capture capabilities

See [Query Vulkan capture capabilities](reference/capture-device-checks.md#query-vulkan-capture-capabilities).

### Test import ownership without capture

See [Test import ownership without capture](reference/capture-device-checks.md#test-import-ownership-without-capture).

### Test GPU resource ownership without capture

See [Test GPU resource ownership without capture](reference/capture-device-checks.md#test-gpu-resource-ownership-without-capture).

### Test the watchdog and provider without capture

See [Test the watchdog and provider without capture](reference/capture-device-checks.md#test-the-watchdog-and-provider-without-capture).

### Test metadata-only inspection without capture

See [Test metadata-only inspection without capture](reference/capture-device-checks.md#test-metadata-only-inspection-without-capture).

### Prepare the metadata-layer trial

See [Prepare the metadata-layer trial](history/capture-trials.md#prepare-the-metadata-layer-trial).

### Run the bounded metadata-layer trial

See [Run the bounded metadata-layer trial](history/capture-trials.md#run-the-bounded-metadata-layer-trial).

### Read the service failure window

See [Read the service failure window](history/capture-trials.md#read-the-service-failure-window).

### Inspect the service relationships without restarting

See [Inspect the service relationships without restarting](history/capture-trials.md#inspect-the-service-relationships-without-restarting).

### Read the platform launcher definitions

See [Read the platform launcher definitions](history/capture-trials.md#read-the-platform-launcher-definitions).

### Check runtime liveness without restarting

See [Check runtime liveness without restarting](history/capture-trials.md#check-runtime-liveness-without-restarting).

### Service-only recovery rehearsal result

See [Service-only recovery rehearsal result](history/capture-trials.md#service-only-recovery-rehearsal-result).

### Prepared platform session test

See [Prepared platform session test](history/capture-trials.md#prepared-platform-session-test).

### Prepared metadata session trial

See [Prepared metadata session trial](history/capture-trials.md#prepared-metadata-session-trial).

### Deployment preflight and command shape (not authorized)

See [Deployment preflight and command shape (not authorized)](history/device-handoffs.md#deployment-preflight-and-command-shape-not-authorized).

### Check existing luma shared memory

See [Check existing luma shared memory](reference/capture-device-checks.md#check-existing-luma-shared-memory).

### Advanced probe, only for a deliberate future retest

See [Advanced probe, only for a deliberate future retest](reference/capture-device-checks.md#advanced-probe-only-for-a-deliberate-future-retest).

## Context for a new development session

Use the [current session handoff](execution-prompt.md). The
[earlier on-device prompt](history/device-handoffs.md#context-for-a-new-development-session)
is retained as history.

</details>

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
