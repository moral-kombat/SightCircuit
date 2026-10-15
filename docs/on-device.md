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

After confirming the terminal is on the headset and can reach its runtime:

1. Identify the installed OpenVR SDK/client library and available native build
   tools. Preserve the runtime/build versions in the hardware notes.
2. Build a public `IVRTrackedCamera` capability probe using the official sources
   linked in the [camera guide](steam-frame-camera.md). Exercise discovery,
   dimensions, calibration, stream acquisition, an actual frame retrieval and
   release. A successful streaming handle alone does not validate capture.
3. Record the observed frame format, camera identity, timestamps and named API
   errors. Check connected-camera and headset wake/idle behavior.
4. If the public API does not provide frames, establish a supported Arcturus or
   Valve capture route before selecting an experimental backend. The community
   Vulkan capture path described in the guide remains a separate investigation.

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
Treat device presence as inventory, not camera validation. Next implement and run
a public OpenVR capability probe before choosing the live camera backend.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
