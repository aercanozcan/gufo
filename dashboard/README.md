# Local cluster dashboard

The dashboard runs only on rank 0 and binds **127.0.0.1:8090**. It is installed
as the enabled user service `evox2-dashboard.service`. It monitors
both hosts over the existing pinned management SSH connection. It needs Python
3.9+ and no additional packages. No rank1 monitor or dashboard daemon is needed.

Check or restart it from the rank0 workspace:

```sh
systemctl --user status evox2-dashboard.service
systemctl --user restart evox2-dashboard.service
```

## Starting and recovering from this computer

Both hosts must be powered on, booted into their existing Fedora kernels and
reachable over the LAN/Wi-Fi. Use <http://127.0.0.1:8090> on rank 0; no token or
routine password entry is required. Both exact recovery bundles are installed.

Press **Start both**. When both services are stopped, it first checks both cable
speeds, matching settings and exact grants. It checks rank 1's ordinary safety
admission over pinned SSH before these preflight steps or model inputs. An
incomplete or unverifiable rank 1 gate blocks
startup with guidance to review the gates and SSH; no services or drivers are
changed. It then prepares fresh paired
RDMA transport, even when the old rails report ACTIVE, and starts both services.
Fresh preparation removes both drivers, proves both modules absent, then
prepares rank 1 and rank 0. Old queue state can survive a clean service stop;
ACTIVE rails alone cannot authorize reuse for the next model process.
If both services and the API are already ready, Start both reads fresh counter
health from both hosts, then returns without checking hashes or reloading drivers.
A known stopped GPU rail blocks that no-op and shows **Restart both** guidance;
unavailable counter health stays unverified and cannot count as healthy. A partial or transitioning service
pair requires Stop both or Restart both; Start never unloads beneath it.
Restart checks inputs before stopping either service, then performs one fresh
paired preparation after both stops succeed.

It waits for **API ready**, rather than treating a systemd start as completion.
The UI displays checking/loading progress and elapsed time. A full checksum
scan reads about 192 GB on each host concurrently, with a 20-minute limit per
host. Full model checksums are reused for one hour while file metadata stays
unchanged. Each startup refreshes the paired input receipt and current artifact
hashes, so namespace/chunk changes need no manual receipt maintenance. Only a
stale, missing or changed model checksum receipt triggers another full scan;
artifact, configuration and SSH errors stop without a blind rehash retry. Loading has a separate
240-second readiness limit. On a startup failure it attempts to stop both ranks
and reports any failed stop. Only failed service units need reset-failed;
inactive template instances can be unloaded by systemd and start normally.

After native Gufo readiness, **Start both** also checks the rank-0 user unit
`evox2-halogen-bridge.service` and discovers the exact legacy model ID at
`/v1/models`. It starts that one unit only when it is inactive or failed; a
healthy running bridge is left alone. This runs after the paired Gufo operation
and its lock have finished. If bridge startup or discovery fails, the UI reports
“Models are running; Pi bridge unavailable. Check the EVO-X2 bridge service.”
and leaves both Gufo ranks running. After starting an inactive or failed unit,
Start waits up to ten seconds for it to bind and pass discovery. An already
active/running unit gets one discovery request and is never restarted by Start.
When native Gufo readiness is healthy but bridge discovery is unavailable, the
status says **Pi bridge unavailable · Start both** and promotes **Start both**.
This does not control the separate Halogen server. Bridge model discovery has a
five-second upstream timeout.

**Each of the two USB4 cables must negotiate 40 Gb/s in each direction on both
hosts: 2 lanes x 20 Gb/s.** Two lanes at 10 Gb/s means only 20 Gb/s per cable and
is rejected. The two host cards show each local cable device's RX/TX rate. The
summary turns green only when both hosts report both links at full speed;
unknown/missing hosts or links cannot pass. Four RDMA rails and API readiness
are separate checks. These rates are negotiated line speeds, not model-transfer
throughput. Cable labels describe local device order, not a cross-host wiring map.

If speeds are below target, press **Stop both** and wait for completion. Unplug
and reconnect each cable at this rank-0 computer, one at a time, in the same
ports, leaving both attached. Wait for 40 Gb/s on both hosts, then use **Start
both**. If rails remain broken while inference is active, use **Recover USB4**:
it confirms interruption, stops both services, removes the pinned rank-1 and
rank-0 drivers, verifies that the module is absent on **both** hosts, and only
then prepares rank 1 followed by rank 0. It verifies two full-speed cables/four
active rails per host. Completing down/up on one host while the other retains
its old peer state does not establish a clean paired recovery.
It leaves inference stopped. Cable speeds and active rails do not by themselves
establish transport or API readiness; a separate **Start both** operation checks
both exact `down` and `prepare` password-free grants on each host, pinned
kernels/settings and cable speeds before stopping services. A failed down
command or missing module-absence proof prevents preparation on either host.
A failed service stop prevents driver unload. No automatic reboot is performed.

**Reset USB4 NHI** is the only dashboard action that resets the host USB4
controllers. It is for the verified interrupt-loss fault: inference is slow with
a fixed ~10 ms cost per exchange while both cables already report 40 Gb/s. The
action checks rank 1 reachability, both cables, the pinned bundles and the exact
`nhi-unload`, `nhi-reset` and `nhi-load` grants on both hosts before changing
anything, stops both services through the ordinary paired stop, then unloads the
pinned RDMA driver and the `thunderbolt` module, ACPI-resets only the two USB4 NHI
PCI functions `0000:c7:00.5` and `0000:c7:00.6`, and reloads `thunderbolt`. Both
hosts unload before either resets, and both reset before either loads. It asserts
that both NHIs rebind and that both cables return to 40 Gb/s, never touches the
USB4 PCIe bridges or any other device, leaves inference stopped and performs no
automatic reboot or retry. Press **Start both** afterwards; fresh paired driver
preparation happens there. The recovery bundles were reinstalled on both hosts
on 2026-10-24, so the action is available now. On a future host or after a
bundle update, remove the old policy and bundle, install again from rank 0, and
let rank 1 prompt once. The dashboard reads the installed manifest before
anything else and refuses the action when the bundle is stale, so it never
stops inference for an install that cannot work. The exact procedure is
`results/slowdown-20261024/NHI_UI_INSTALL.md`; the completed install receipt is
`results/slowdown-20261024/install-20261024/README.md`.

Physical link training is negotiated by the hardware. Software recovery cannot
force or guarantee permanent 40 Gb/s training; another reconnect may be needed
after reboot or power loss. A stop timeout/kernel-blocked process requires host
recovery; the UI reports the failure instead of continuing to unload a driver.
Do not relax the 40 Gb/s gate, replace the kernel or change driver/runtime settings.

Terminal alternatives, entirely from rank 0 over pinned SSH:

```sh
python3 scripts/prepare-rdma-pair.py --check  # read-only
python3 scripts/prepare-rdma-pair.py          # both inference services must be stopped
```

The dashboard and bridge start with the rank-0 user session. Inference services
and transient RDMA drivers remain disabled at boot; Start both handles ordinary
startup. Explicit Recover USB4 leaves inference stopped. A separate Start both
operation checks inputs and prepares fresh paired transport. No persistent freshness receipt or
automatic generation warmup is used. No rank-1 dashboard daemon or monitor installation is required.

## Local access without token entry

The user explicitly requested automatic local access on 2026-10-19.
`config/dashboard.json` selects `"access": "local"`. Open
<http://127.0.0.1:8090>; the console opens directly, including in a fresh browser
session. Expired sessions and dashboard restarts reconnect automatically.
The token form and Sign out button stay hidden in this mode. Do not ask the
user to copy a dashboard token during ordinary use.

The server still binds only to `127.0.0.1`. A same-origin POST from a loopback
client establishes an HttpOnly, SameSite=Strict session cookie. Host, Origin,
fetch-site, and CSRF checks remain in place; paired controls still require
explicit confirmation. No token is embedded in the page or URL, and no password
is stored. Local clients and clients using an authenticated SSH forward can
access the dashboard. Inference API credentials and sudo grants are separate.

To restore manual token login, set `"access": "token"` in
`config/dashboard.json` and run `systemctl --user restart evox2-dashboard.service`.
That mode uses the existing private `.private/dashboard-token` and sign-in form.
The old token file is preserved but unused in local mode. A missing dashboard
configuration defaults to token mode; invalid settings stop startup.

From a laptop, forward the local port over your authenticated SSH connection:

```sh
ssh -N -L 8090:127.0.0.1:8090 ercanozcan@192.168.0.133
```

Then use <http://127.0.0.1:8090> on the laptop. No dashboard firewall rule is
needed. Gufo stays on 8080; the legacy laptop bridge stays on 8731.

## Optional unattended recovery

The user explicitly approved these persistent sudo grants on 2026-10-19:
driver preparation/down and transport counters on both hosts, revocation of
these grants, and orderly reboot on rank 1. Do not ask for that approval again.
Installation progress is recorded in `../HANDOVER.txt` and
`../results/gufo-refresh/status.json`; do not assume both hosts are installed.
Review the policy with `python3 scripts/recovery_authorize.py`, then run the one-time setup
with `--install` in a rank-0 terminal. Each host authenticates once. It installs
root-owned copies of the existing driver, its kernel-matched module, the helper,
and configuration under `/usr/local/lib/evox2-recovery-rankN`; hashes are checked
before use. The sudo policies are `/etc/sudoers.d/evox2-recovery-rankN`.
Only fixed preparation, driver-down, counter-reading, and revocation operations
are granted, plus an orderly reboot on rank 1. The granted commands do not run
code or load modules from the user-writable checkout. Kernels, driver parameters,
runtime, network routing, and boot enablement remain unchanged.

After installation, `python3 scripts/prepare-rdma-pair.py` uses those grants
without a terminal or password. Stop paired inference first. Both down grants
and both prepare grants are required before changes. Recovery removes both
drivers, verifies both modules are absent, then prepares rank 1 and rank 0.
A healthy pair returns without reloading. Missing grants fail with setup
guidance; there is no fallback to executing mutable workspace driver code or
a one-sided reload retry. Kernel or driver configuration mismatches fail before recovery. Root-owned recovery state takes
precedence over the old workspace state file. For transport evidence, run
`sudo -n /usr/local/lib/evox2-recovery-rankN/scripts/recovery-helper.py counters`
on the corresponding rank. To reboot the stopped peer, use the rank-1 helper's
`reboot` action over the pinned SSH connection. No automatic reboot policy is
installed. Remove only these grants with
`python3 scripts/recovery_authorize.py --rollback`; the pinned bundle and driver
state remain for manual recovery. Existing dashboard grants are unaffected.

## Paired service controls

Monitoring works without sudo. Service controls use exact, noninteractive
`systemctl start/stop/reset-failed` commands for the corresponding rank service.
Driver recovery uses only the separate, pinned root-owned recovery helpers.
Ordinary terminal sudo timestamps are tied to their terminal and cannot reliably
authorize the dashboard process. The user approved the exact persistent grants
for both hosts, and the one-time installation completed. All six exact command
permissions were verified on both hosts; the existing services stayed active.

The dashboard also recognizes the persistent one-request recovery gate at
`.private/one-post-recovery-gate.json`. Its default-off behavior leaves existing
controls unchanged while that filesystem entry is absent. If any entry exists
(including malformed data or a symlink), Start, Restart and Recover fail closed
under the paired-operation lock; Stop and read-only status remain available.
The dashboard never clears this gate. Only a separately reviewed reconciliation
may remove it after recovery is established.

A separate synthetic host-fixture gate uses
`.private/recovery-gates/20261023-hostmr-sendimm-129-v1.json`. The guard source (SHA-256 `5e34f9b42a0513b6a445b0ad67d7c41033f95df0dfee63ba34f2d7b7d137d83b`) was loaded in the running
dashboard after restart at 2026-10-22T23:44:32Z; the service is active/running and
loopback HTTP returned 200. Its 40 focused tests passed, but live Start/Restart/
Recover behavior with this marker has not been exercised, so do not treat the
guard as an admitted fixture execution path. The fixture remains NO-GO and
unarmed.

The dashboard also blocks Start, Restart and Recover while
`.private/summary-manifest-repair-incomplete.json` exists, including if a
visible manifest-only repair loses its rank-1 SSH acknowledgment. Stop and
read-only status remain available. The marker is created durably before the
first privileged repair and is cleared only after both ranks return verified
completion; an unknown outcome requires separate reviewed reconciliation.
The gate-aware dashboard source was reloaded on rank 0 at 19:17 UTC on
2026-10-22 and returned loopback HTTP 200. That check did not exercise a live
Start or repair. See
`results/gufo-refresh/current-tps-20261022/dashboard-gate-reload-postcheck.json`
and `MANIFEST_GATE_INDEPENDENT_REVIEW.md` there.

This dashboard guard is only one integration boundary. The one-request
supervisor remains **NO-GO** until its worker and Stop guardians are integrated
with the guard and the complete flow receives independent review. Presence of
the guard does not enable a one-request run or qualify recovery.
Review the exact policy without installing anything:

```sh
python3 scripts/dashboard_authorize.py
```

Use `python3 scripts/dashboard_authorize.py --install`
in the rank0 terminal. The installer asks for rank0 and rank1 sudo passwords there, with
rank1 reached using the existing verified SSH identity and a pseudo-terminal.
It never captures passwords. It validates all sudoers configuration, refuses
unrelated files, and installs only
`/etc/sudoers.d/evox2-dashboard-rank0` locally and
`/etc/sudoers.d/evox2-dashboard-rank1` on the peer. It grants no shell, editor,
wildcards, driver operations, arbitrary units, or service enablement. The
partial/completed installation receipt is `.private/dashboard-authorization.json`.
No running services are changed by authorization.

Remove these exact grants from both machines with:

```sh
python3 scripts/dashboard_authorize.py --rollback
```

Start/restart checks the deployment's recent paired input receipt, full cable
speeds, matching current settings and all required grants **before stopping any
running service**. Hash verification can run with drivers absent: the internal
`preflight.py --local --inputs-only` receipt explicitly records `rails: null`
and `rails_checked: false`. Default deployment preflight and terminal checks
still require actual rails. Startup verifies full physical readiness after
fresh paired preparation and before starting either model service. An expired
or missing model checksum receipt triggers full verification only on the host
that needs it, bounded to twenty minutes per host
and displayed in operation status. Stop checks both hosts' permissions before
changing either service, then attempts both stops independently.
A partial start attempts to stop both hosts and reports any failed rollback.
Only one control operation runs at a time. The UI confirms all paired actions.
Service startup success is separate from inference readiness; loading may take
minutes. Restart/stop interrupts active requests.

A rootless user-service alternative is unavailable on rank1 because its account
and user-manager hard locked-memory limit is 8 MiB. The existing system service
provides the unlimited locked memory required by the pinned GPU/RDMA runtime.
The dashboard does not replace that working service with user units.

## Current stopped state and historical `rdma` Q8 deployment, 2026-10-23

As of 16:28 UTC, both Gufo services are **stopped** and the Gufo Pi bridge
health route returns 503. A clean build from `neuhaus/rdma` pin `39f437cc`
plus only the known linker fix started on both hosts and passed one short Q8
request, then failed in prefill on the first larger Pi-style bridge stream
(rank 0 WC5, rank 1 WC4; four stopped GPU rails per host). First-fault evidence
was captured; the old manifests and binary selection were restored on both
hosts without relaunching them. Both cables remain at 40 Gb/s RX/TX on each
host, but transport health is faulted. The laptop Gufo endpoint is not usable.
Do not repeat the same Restart/request cycle under unchanged conditions. See
`../results/clean-rdma-20261023/RESULT.md` and `../HANDOVER.txt`.

The following paragraphs describe the earlier `rdma` Q8 activation and its
dashboard behavior before the later transport failures.

The user-approved exact rank-1 `COMMIT_READY` receipt repair is installed and
verified; startup does not require entering a password. The fork's default
`rdma` source plus only the known linker fix is selected on both hosts. Paired
activation completed and one Q8 generation request passed. Both services,
the API and transport were ready at the final check. The dashboard source is
installed on both hosts and the rank-0 user service was reloaded. A local,
session and CSRF protected HTTP **Start both** request completed as an
active-ready no-op with “Cluster already ready.” See
`results/rdma-main-q8-20261023/` and the current `HANDOVER.txt` for exact
pins, evidence, and limits. A stopped-to-running dashboard click uses the
same paired controller that completed the successful activation; this session
did not stop the running service solely to retest the browser path.

## Focused monitoring

The main view keeps CPU/GPU utilization, used/total memory and CPU/GPU
temperatures for each host; prefill/decode token rates; per-cable RX/TX speeds;
API status; and the four controls. CPU/GPU temperatures use the highest available
reading for their corresponding hwmon device. Missing sensors show unavailable.
Do not add back configuration tables, request history, charts or driver details
without user direction. Richer telemetry remains available through scripts and
saved evidence. UMA system/GPU memory overlaps; the UI shows system memory once.

Prefill/decode are Gufo's latest server-reported rates, retained while idle.
Host probes are read-only and run about every three seconds after collection;
SSH/API timeouts can delay a sample. Lost probes remove host readings and make
cable status unverified. Active rails alone do not prove working transport;
actual per-rank data counters and model API tests still qualify a deployment.
The host probes also read current transport health through the already-installed
exact `counters` grants, with a two-second local helper limit inside each
existing probe (no additional polling SSH connection). A rail can report ACTIVE
and data-ready while its GPU data rings are stopped. The API-status pill then
reports `Transport fault · inspect status`.
Use paired status to diagnose the fault before choosing a control action.
Physical cable RX/TX indicators continue to show negotiated speeds. Missing or malformed
counters show transport unverified, never healthy. Startup completion and an
already-active Start no-op require fresh clean health on both hosts. No automatic
retry or recovery is triggered. Fresh bilateral preparation is a workaround,
not proof that the underlying transport fault is fixed.

`config/evox2-dashboard.service.in` was rendered to
`config/evox2-dashboard.service` and linked into the rank0 user service manager.
The dashboard runs as the normal rank0 account. To remove the service link and
stop it, run `systemctl --user disable --now evox2-dashboard.service` followed
by `systemctl --user daemon-reload`.

Validation without any host changes:

```sh
python3 -m unittest discover -s scripts -p 'test_dashboard*.py' -v
node --test scripts/test_dashboard_browser.js
python3 scripts/dashboard_probe.py --root "$PWD" --rank 0
```

## Readiness semantics

The local dashboard's unauthenticated loopback-only `/api/bridge-readiness`
endpoint on port 8090 exposes only a ready boolean and generic recovery guidance. The bridge rejects oversized readiness bodies above 1 KiB and does not use an ambient proxy. It reads
cached evidence from the latest probe cycle. Readiness requires a cycle no more
than 12 seconds old, API readiness, both active Gufo services, two 40 Gb/s
USB4 cable inventories on each host, both exact transport-counter checks, no
UI operation, and an idle shared UI/CLI pair lock. Probe age starts at the
beginning of the collection cycle, so slow SSH or API checks cannot make old
evidence appear fresh. Completion of a dashboard paired control invalidates the previous snapshot
until a later collection publishes. Active CLI pair operations also deny readiness
through the shared nonblocking pair-lock check.

This reports recently observed health; it cannot guarantee that a cable or
transport remains healthy after the observation. The Halogen bridge checks this
endpoint before health responses and supported inference requests. The UI also
requires its separately collected bridge health result before showing overall
API ready. `/v1/models` remains available for discovery.

As of 2026-10-23, this source is installed and live verified. The dashboard
opened without token entry. **Start both** checked model discovery and reported
“Models are running; Pi bridge is ready” without restarting either Gufo rank or
the healthy bridge. One short streaming request through the Gufo compatibility
bridge returned HTTP 200, PONG and a complete stream before the final discovery
timeout change. Both Gufo service PIDs, 40 Gb/s cable readings and exact
transport checks remained healthy afterward. The inactive bridge startup path
passed offline tests; it was not deliberately exercised live. The separate
Halogen server was untouched. See
`../results/pi-503-20261023/bridge-start-rollout.json` and
`../results/pi-503-20261023/final-bridge-start-postcheck.json`.

When the UI shows **Transport fault · Restart both**, press **Restart both** and
confirm the paired action. It performs the already tested bilateral recovery
and starts both Gufo services. If either cable is below 40 Gb/s, follow the
replug guidance first; the UI cannot force cable negotiation. A request can
still fail between health observations, and no automatic request replay occurs.
The underlying USB4/RDMA fault is unresolved.

The previous dashboard, Gufo compatibility bridge, UI script and style source
are saved under `../results/pi-503-20261023/rollback/`. To revert the rank-0
user services from this workspace, run:

```sh
cp results/pi-503-20261023/rollback/dashboard.py scripts/dashboard.py
cp results/pi-503-20261023/rollback/halogen-bridge.py scripts/halogen-bridge.py
cp results/pi-503-20261023/rollback/app.js dashboard/app.js
cp results/pi-503-20261023/rollback/style.css dashboard/style.css
systemctl --user restart evox2-dashboard.service evox2-halogen-bridge.service
```

The rank-1 `scripts/dashboard.py` copy should be restored over pinned SSH with
its SHA-256 checked before and after; no dashboard service runs on rank 1.
The independent launch guard has a separate prior `scripts/cluster.py` copy in
`../results/pi-503-20261023/rank1-cluster-before-candidate-guard.py`.
