# Historical capture and recovery procedures

**Historical reference, preserved during the documentation reorganization.**
These procedures and incident notes record earlier provider activation and
SteamVR/session recovery work. Their “next” statements and test counts belong
to those milestones. They are not instructions for the current session.

The provider track is paused. Consult the [current plan](../live-capture.md)
and [evidence](../status.md) before proposing a changed device trial. Retained
commands include blocked operations and session logouts; no device operation
is authorized by this historical page.

## Prepare the metadata-layer trial

The native v3 build and generated-data checks are complete. Next prepare an
inactive bundle from the tested build and collect service metadata. Run on
Frame in the existing checkout:

```bash
cd "$HOME/sightcircuit" &&
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-v3-new &&
systemctl --user show steamvr.service \
  -p LoadState -p ActiveState -p SubState -p MainPID \
  -p FragmentPath -p DropInPaths -p Type -p KillMode -p TimeoutStopUSec
```

Expect `prepared_inactive_bundle`, with activation and restart both false. If
the directory already exists, preserve it and use another new bundle name.
Keep any earlier v2 bundle unchanged. Share the preparation report and service
properties; keep the bundle's backups local.

The inactive bundle and service properties were received; see
[E38](../status.md#e38-inactive-bundle-and-steamvr-service-on-frame). Preparation
writes only the bundle and the service query changes nothing. Keep that bundle
unchanged. The next procedure builds the later loader fallback/timing fixes and
requires a fresh bundle before any activation.


## Run the bounded metadata-layer trial

**Paused after service-control failures on Frame; do not repeat the
trial.** Current `--start-metadata-test` returns `blocked`/exit 3 before any
runtime or file action. Reporting, read-only preflight and known-run recovery
remain available. Normal UI/passthrough work; see
[E41](../status.md#e41-frame-trial-report-and-service-control-failures).
The retained procedure below describes the earlier trial for investigation.

The earlier experiment restarted SteamVR for the test and again for restoration.
The headset view/browser/console could disappear. The trial used a separate user service and systemd ran
its post-exit recovery independently of the console. This is the first actual
layer-load/restoration trial; the cloud has tested generated data, not this
device operation. It requests no image data and runs no recognition or overlay.

The earlier preparation command is retained below for investigation; do not
use it to resume activation. Preserve existing bundles:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_service_lifecycle.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_trial.py -v &&
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-v3-trial &&
python3 scripts/capture_layer_trial.py --preflight artifacts/capture-layer-plan-v3-trial
```

Current suites contain **14, 12, 10 and 48 tests**, with no skips; the original
preparation expected `prepared_inactive_bundle`
and `preflight_passed`. If the new directory already exists, preserve it and
use a different new name throughout. Preflight requires matching current
build/helper hashes, absent prior SightCircuit active targets, no existing
provider/enable flags, and the reported active simple/control-group service
with a 20-second stop timeout and no drop-ins. The revised preflight also needs
an idle job and a live compositor in the original unified cgroup. It refuses changed conditions
without restarting anything. This trial does not support replacing an existing
SightCircuit installation.

The earlier activation command is now blocked; it is retained for reference:

```bash
python3 scripts/capture_layer_trial.py --start-metadata-test artifacts/capture-layer-plan-v3-trial
```

Before touching SteamVR, the launcher runs a dummy one-second timeout to verify
this Frame's systemd executes the cleanup command. Failure refuses activation.
After a passing rehearsal it schedules `sightcircuit-metadata-trial.service`,
which stages only the metadata layer and a temporary runtime drop-in scoped
to `steamvr.service`. It waits for the provider and inspects metadata for two
seconds. Systemd's separate `ExecStopPost` then stops SteamVR, restores the
recorded targets, removes its exact enable drop-in, and starts normal SteamVR.
The normal path can finish sooner; allow **about three minutes** for timeout
and recovery. A launch acknowledgment means scheduled, not completed. If it
says `launch_unconfirmed`, inspect that run before attempting another launch.

Restoring SteamVR does not relaunch the terminal/console application. Open a
terminal independently, or use SSH from another computer, then read reports:

```bash
cd "$HOME/sightcircuit" &&
python3 scripts/capture_layer_trial.py --report-latest
```

Success is `status: completed`, with `trial.status: inspected`,
`rollback.status: restored`, `configuration_restored: true` and
`normal_runtime_observed: true`, plus either a confirmed normal-service start
or `normal_service_already_running: true`. The metadata peer must be a `vrcompositor` in
the service cgroup. Zero compatible candidates is still a metadata result,
not camera access. `incomplete`/exit 3 means the final recovery report is absent;
`failed`/exit 1 must not be treated as restoration success. Share the report
and whether normal passthrough recovered. No images are saved.

From another computer, read the same report without restarting or activating
anything on Frame:

```bash
ssh steamos@<FRAME_IP> 'cd "$HOME/sightcircuit" && python3 scripts/capture_layer_trial.py --report-latest'
```

If SSH cannot connect, first establish whether the normal headset interface
and passthrough work and whether the full boot sequence occurred. Console loss
alone does not prove a headset reboot or failed configuration restoration.
Do not restart the trial to recreate the symptom.

If recovery fails, use the known `trial_directory` from the launch/result
report. From a separate terminal/SSH session, stop any remaining trial first:

```bash
systemctl --user stop sightcircuit-metadata-trial.service
```

Then use `python3 scripts/capture_layer_trial.py --rollback /exact/trial-directory`,
substituting that recorded path. It refuses a living worker and preserves
foreign/later edits; it never guesses paths from a wildcard. Keep the report if
it refuses or normal SteamVR does not start. A failed manager/service stop
cannot guarantee restoration. Backups stay local in ignored private artifacts.

For a **future already running v3 metadata provider**, the diagnostic command is:

```bash
python3 scripts/capture_provider_check.py --inspect --seconds 2
```

Do not run it merely to retry a missing provider; the generated tests above
exercise the consumer now. The default without `--inspect` does nothing and
exits 3. An existing provider is queried for metadata and registry stability
only, with no Vulkan calls, image descriptors, pixel reads or file outputs.
Success is `inspected`/exit 0; zero candidates or unsupported metadata still
produce useful diagnostic results. Missing/old providers, generation changes,
unexpected descriptors and deadline failures exit 1. Cancellation exits
`128 + signal`. Capture remains blocked until the
[producer handoff](../live-capture.md#producer-synchronization-review-and-next-checkpoint)
or a synchronized compositor-owned copy is established.


## Read the service failure window

The saved reports are now received. Rollback reports configuration restoration,
SteamVR is active with no drop-ins, and the user confirms working UI/passthrough.
Do not run another trial or rollback to reproduce the error. The failed worker
and rollback operation is not identified by the earlier generic error string;
the supplied SteamVR tail starts after the failure window.

On Frame, read the existing journal for 10:36:30–10:37:05 Eastern on 2026-10-17
(14:36:30–14:37:05 UTC), plus current service policy. No restart, configuration
change or image access is performed:

```bash
journalctl --user -u steamvr.service -u sightcircuit-metadata-trial.service \
  --since '2026-10-17 14:36:30 UTC' --until '2026-10-17 14:37:05 UTC' \
  --no-pager -o short-iso -n 200 \
  --grep='(?i)fail|error|denied|refus|cancel|dependency|start|stop|job'
systemctl --user show steamvr.service \
  -p RefuseManualStart -p RefuseManualStop -p Restart -p RestartUSec \
  -p NRestarts -p Result -p ExecMainStatus -p ActiveEnterTimestamp
```

Share this output. It does not expose the service environment or backup plan.
If the journal has no matching entries, record that result; do not restart the
experiment to generate more logs. The corrected runner adds phase and safe
operation diagnostics for offline testing; it remains blocked for activation.


## Inspect the service relationships without restarting

The focused journal and policy query were received; see
[E42](../status.md#e42-focused-service-window-and-restart-policy-on-frame).
Manual start/stop are allowed. The worker failed while SteamVR was stopping;
SteamVR started again after the rollback error. `Restart=always` alone does
not explain that sequence. Keep the working headset running and do not repeat
the trial or rollback.

Pull the diagnostic update, then query service relationships and executable
paths on Frame. This changes repository files only; the diagnostic performs
one `systemctl show` query and makes no runtime/configuration change:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
python3 scripts/capture_layer_trial.py --service-details
```

Expect `status: service_details`, exit 0, `activation_requested: false`, and
service relationships plus executable paths for start/stop hooks. Command
arguments and environment values are omitted. The corrected inspector preserves
all repeated hooks in order; the earlier E43 report contained only each field's
last hook. Source collection has already supplied the full inventory, so there
is no need to repeat this query just to continue the review. No
rebuild is needed. The activation pause stays in place while the unit
relationships/launcher behavior are reviewed.


## Read the platform launcher definitions

The service relationship report was received; see
[E43](../status.md#e43-steamvr-session-relationships-and-platform-hooks).
The initial relationship report identified the platform selector and Bash
hooks, but omitted earlier repeated hooks. The following collector was used
to read the shipped unit and selector without executing them.

**Received and reviewed:** the user supplied this output; see
[E44](../status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix).
Keep the command below as a reference, not another required collection step.
The unit requests startup before tracking a waiter and runs health-check and
display cleanup hooks on shutdown. Do not execute the selector as a probe: its
fallback can change the selected SteamVR installation. The
[offline lifecycle model](../live-capture.md#steamvr-service-control-acceptance)
is now implemented and cloud-tested. The next device step below reads runtime
metadata without a restart.

```bash
python3 - <<'PY'
from pathlib import Path
import re

for name in ('/usr/lib/systemd/user/steamvr.service',
             '/usr/share/deckard/select_steamvr.sh'):
    print(f'\n--- {name} ---')
    try:
        text = Path(name).read_text()
    except OSError as error:
        print(error)
        continue
    if name.endswith('.service'):
        continuation = False
        for line in text.splitlines():
            selected = continuation or re.match(r'^\s*(?:Exec[A-Za-z]*|Type)\s*=', line)
            if selected:
                print(line)
            continuation = bool(selected) and line.rstrip().endswith('\\')
    else:
        print(text)
PY
```

The unit portion includes only Exec*/Type definitions and
their continuation lines; environment/credential directives are omitted. The
selector is read as static source, not run. No runtime environment or credential
file is read. No additional output from this collector is needed now.


## Check runtime liveness without restarting

The [offline recovery work](../status.md#e45-offline-platform-lifecycle-and-guarded-recovery)
passes 65 targeted cloud tests. **Frame result received:** the runtime observation
passes with an active/idle service and a live compositor in its original cgroup;
see [E46](../status.md#e46-read-only-runtime-baseline-on-frame). Do not repeat this
successful check just to continue the review. The command remains available as
a read-only reference, without a rebuild or inactive bundle:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
python3 scripts/capture_layer_trial.py --runtime-status
```

Expect `status: runtime_observed`, exit 0, `activation_requested: false`,
`normal_runtime_observed: true`, and `rendering_readiness_verified: false`.
The runtime observation should show active/running/success, no pending job,
a populated original cgroup and a live compositor identity. The JSON is received,
and the user confirms normal passthrough during/after. Independent SSH access
is not verified. A missing/unreadable cgroup or
absent compositor returns `runtime_unconfirmed` or `refused`, exit 1; retain the
report and do not restart the experiment to change the result.

This mode reads only allowlisted service properties and cgroup/proc metadata.
It performs no configuration writes, service start/stop/reload, OpenVR/Vulkan
calls, provider connection, pixel read or image save. It checks process liveness,
not actual rendering. A passing report does not unblock activation. Independent
terminal/SSH access and a reviewed restoration/readiness procedure are still
required before any later device rehearsal.

The offline suites can also run on Frame without touching its VR runtime:

```bash
python3 -m unittest tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

Expect 65 tests. The configuration suite's generated-provider test requires the
previously built `--provider-fixes` fixture; if absent, that test skips. Record
the actual pass/skip totals. These tests do not validate real systemd jobs.


## Service-only recovery rehearsal result

`scripts/service_rehearsal.py` stops and restarts `steamvr.service` once,
without preparing or installing a capture layer. Its design and tests are in
[development](../reference/capture-provider.md#service-only-recovery-rehearsal). Independent SSH
from the PC is verified, and the first Frame run is recorded as
[E63](../status.md#e63-service-only-rehearsal-and-graphical-session-dependency).

What happened on Frame: the SteamVR stop job was **canceled**. The shipped
Gamescope session is `PartOf=steamvr.service`, so stopping SteamVR also
stopped the graphical session, which then restarted. Independent cleanup later
observed a healthy compositor without issuing another service command. The
rehearsal itself is a failure; the runtime recovered on its own.

Current guard: `--preflight` (and so `--start`) now refuses to run while
Gamescope is `PartOf` SteamVR, reporting
`steamvr_stop_propagates_to_graphical_session`. On this runtime a bare
SteamVR stop is not a safe recovery model. Use the
[platform session test](#prepared-platform-session-test) for session-level
recovery instead. Do not edit service policy to get past the guard.

To confirm the guard or read the saved result, run on Frame:

```bash
cd "$HOME/sightcircuit"
python3 scripts/service_rehearsal.py --preflight
python3 scripts/service_rehearsal.py --report-latest
```

Both are read-only. With no mode the tool does nothing (status `not_run`,
exit 3). `--start` additionally requires an inbound SSH session from another
computer and refuses if a trial unit already exists. `--recover RUN` acts only
on a known run after its worker has exited. Neither is needed while the guard
holds.


## Prepared platform session test

`scripts/session_rehearsal.py` asks SteamOS Manager for one
`switch-to-game-mode` session cycle (`/usr/bin/steamosctl switch-to-game-mode`)
and observes recovery independently. It never stops or starts SteamVR
directly and never installs a layer. Design and tests are in
[development](../reference/capture-provider.md#platform-managed-session-rehearsal).

Result: the operator's session test **completed successfully**, with healthy
independent recovery and the wearer confirming a normal headset
([E64](../status.md#e64-platform-session-test-completed-and-headset-confirmed)).
A completed run is also the `--session-proof` the
[metadata session trial](#prepared-metadata-session-trial) requires.

A session cycle **logs out the graphical session**: games, the replay window,
VR panels and anything else in it close. Repeat it only for a changed platform
(SteamOS, SteamOS Manager or Gamescope session files), and only with approval.

```bash
cd "$HOME/sightcircuit"
python3 scripts/session_rehearsal.py --preflight
bundle="artifacts/session-bundle-$(date +%Y%m%d-%H%M%S)"
python3 scripts/session_rehearsal.py --prepare "$bundle"
python3 scripts/session_rehearsal.py --check "$bundle"
```

`--preflight` reads only the platform and runtime. `--prepare` writes a new,
exclusive, inactive bundle of helper sources, hashes and platform fingerprints,
and refuses an existing path. `--check` must report `prepared_test_ready`; it
refuses changed platform files, foreign drop-ins or existing capture
configuration.

The approved run must be started **from an SSH session on another computer**,
so the session that is logged out is not the one running the command:

```bash
cd "$HOME/sightcircuit"
python3 scripts/session_rehearsal.py --start "$bundle" --allow-session-logout --wait
python3 scripts/session_rehearsal.py --report-latest
```

`--start` without `--allow-session-logout` is refused. The worker sends exactly
one request and never retries, even after a failure or timeout, because the
request may still have started a logout. `--wait` waits at most 180 seconds for
the saved result; disconnecting does not cancel the run. Completion needs new
invocation IDs for Gamescope, SteamVR and both session targets, all healthy
and stable for two seconds. A partial restart cannot pass. Process liveness is
not the wearer's check: ask whether the UI, passthrough and tracking look
normal afterward, and record that separately.


## Prepared metadata session trial

`scripts/metadata_session_trial.py` runs a metadata-only provider activation
and restoration across two platform session cycles. It uses the same vendor
session request as the [platform session test](#prepared-platform-session-test),
not a bare SteamVR stop. No image descriptors, camera bytes, detector or VR
output are involved; the v3 provider still rejects buffer requests. Design,
ownership rules and tests are in
[development](../reference/capture-provider.md#metadata-trial-across-platform-sessions).

Results so far
([E65](../status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed)):

- First run: the provider loaded and normal startup was restored. It received
  20 metadata records, including eight candidate-profile matches, but failed
  with `provider_registry_changed`.
- v2, with bounded settling: three snapshots held identical metadata for the
  same eight candidate IDs, while other allocations appeared and disappeared.
  Full-registry inspection still failed. Software restoration and normal UI,
  passthrough and tracking were confirmed.

**Do not repeat v2 unchanged.** The next step is offline work on scoped
lifetime and version tracking. Use the commands below only for a changed
version that has been reviewed and approved. The trial performs **two**
session logouts, so the same warnings as the session test apply.

Inputs: a verified v3 metadata bundle from
[Prepare the metadata-layer trial](#prepare-the-metadata-layer-trial), the
metadata inspector built with `bash scripts/build_capture_reference.sh
--provider-fixes`, and a completed session-test run directory under
`artifacts/session-rehearsals/` on the same platform.

```bash
cd "$HOME/sightcircuit"
trial="artifacts/metadata-session-$(date +%Y%m%d-%H%M%S)"
python3 scripts/metadata_session_trial.py --prepare "$trial" \
  --metadata-bundle artifacts/capture-layer-plan-v3-NAME \
  --session-proof artifacts/session-rehearsals/RUN
python3 scripts/metadata_session_trial.py --check "$trial"
```

Replace `NAME` and `RUN` with the existing bundle and completed session run.
`--prepare` refuses a session proof that did not complete or that came from a
different platform. `--check` must report `metadata_session_test_ready`. The
approved run, again from an SSH session on another computer:

```bash
cd "$HOME/sightcircuit"
python3 scripts/metadata_session_trial.py --start "$trial" --allow-session-logouts --wait
python3 scripts/metadata_session_trial.py --report-latest
```

`--wait` waits at most six minutes. Keep the whole run directory: every raw
inspection attempt and the settling summary are saved, and a later success
does not hide earlier invalidations. Cleanup removes only the trial's own
drop-in and manifest and requests one normal session. Library and source
artifacts are kept for diagnosis. Report the result, the restoration fields and
the wearer's answers about UI, passthrough and tracking separately.


[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
