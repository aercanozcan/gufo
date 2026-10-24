# USB4 NHI ACPI reset — runbook (2026-10-24)

Purpose: clear the NHI data-ring interrupt-loss state that makes every dmabuf SEND
fall through the driver's 10 ms missed-notification timeout. This is the documented
cure for the exact fault recorded on 2026-10-08
(`repos/donnerkeule/bench/results/2026-10-08-hardware.md`), where a module reload and
a function PM reset failed but an **ACPI reset of both hosts' USB4 NHI functions**
restored normal latency (~404 µs/msg afterwards, data-ring interrupt counters rising).

This does **not** modify the frozen recovery bundle, the Donnerkeule module, the
kernel, module parameters, transport mode, or the pinned deployment.

## Current preconditions (verified read-only, 2026-10-24)

| | rank 0 | rank 1 |
|---|---|---|
| kernel | 7.2.5-200.fc44.x86_64 | 6.19.10-300.fc44.x86_64 |
| service | inactive | inactive |
| recovery state | owned (state file present) | owned (state file present) |
| NHI functions | `0000:c7:00.5` `0000:c7:00.6` | same |
| `reset_method` | `acpi pm` | `acpi pm` |
| IOMMU siblings | none (groups 30/31) | none |
| management | 192.168.0.133 (Wi-Fi) | 192.168.0.217 (Wi-Fi, independent of USB4) |
| cables | 2 × 20 Gb/s RX/TX | 2 × 20 Gb/s RX/TX |

We reset only the two NHI USB controllers, never the PCIe USB4 bridges
(`00:01.1/.2`, which advertise only `pm`).

## What the runner does, in order (both hosts)

1. `unload` — stop `evox2-gufo@<rank>`, run the **sanctioned**
   `recovery-helper.py down` (unloads `thunderbolt_ibverbs`, deletes `evox2rdma`,
   removes the owned driver state), then `rmmod thunderbolt`. Asserts both absent.
2. `reset` — for `0000:c7:00.5` and `0000:c7:00.6`: require unbound and `acpi` in
   `reset_method`, write `acpi`, write `1` to `reset`, restore the original
   `reset_method` even on error. Aborts if either function vanishes.
3. `load` — `modprobe thunderbolt`, wait for both NHIs to bind and links to
   enumerate (best effort; links train once both hosts are loaded).

Both hosts are unloaded before either is reset; both are reset before either is
loaded.

## Run it (visible terminal on rank 0)

```sh
cd /home/ercanozcan/Desktop/llm/evox2-cluster
bash results/slowdown-20261024/nhi-reset-orchestrate.sh
```

- It copies the runner to rank 1 and **verifies identical sha256** on both hosts
  before doing anything.
- It asks you to type `RESET-NHI`.
- rank 0 phases run via `sudo` (this terminal).
- rank 1 phases run over the pinned SSH config with `ssh -t`, so `sudo` can prompt;
  type the rank-1 password when asked. Nothing is stored.
- Receipts are written under `results/slowdown-20261024/nhi-reset-receipts/`
  (rank-1 receipts copied back to `…/rank1-remote/`).

### Abort points

Stop immediately if any phase prints `FATAL`. Do **not** continue to later phases.
The system is recoverable because:
- `thunderbolt` re-loads with plain `modprobe` (stock installed module, default
  parameters — identical to what `driver.sh up` uses), and
- the normal `recovery-helper.py prepare` / dashboard Start reloads the RDMA stack
  from scratch anyway.

If `reset` reports `vanished after reset`, the controller did not re-enumerate; a
host reboot is then required (rank 0 may be rebooted locally; rank 1 boots
Windows-first — see risks).

## After the reset

1. Verify links and cable speed on both hosts (must be 2 × 20 Gb/s = 40 Gb/s/dir,
   4 rails). The `load` phase already prints this; re-check:
   ```sh
   for d in /sys/bus/thunderbolt/devices/*/; do
     [ -f "$d/rx_speed" ] && echo "$(basename "$d") rx=$(cat "$d/rx_speed") tx=$(cat "$d/tx_speed") lanes=$(cat "$d/rx_lanes")"
   done
   ```
2. Start via the normal sanctioned path (dashboard **Start** / paired controller),
   which does read-only checks, fresh bilateral preparation, service start and
   API readiness.
3. Re-measure the transport (services must be stopped again for the probe), or,
   if you keep serving, measure a real request. The decisive check is per-exchange
   latency: healthy is tens of µs, this fault is ~11.3 ms.
   ```sh
   # rank 0 then rank 1, using the existing probe harness:
   bash results/slowdown-20261024/probe_rank2.sh 0 192.168.0.133 \
       /home/ercanozcan/Desktop/llm/evox2-cluster queue 10240 512
   # (rank 1 via the pinned ssh, same arguments with rank 1)
   ```
   Success: `collective_wall_s / operations` drops from ~11.3 ms to well under
   100 µs. If it is still ~11 ms, the NHI state did not clear and options B/C from
   `ROOT_CAUSE.md` remain (full power cycle / cable replug).

## Risks (read before running)

- Unloading `thunderbolt` and resetting its NHIs tears down **both USB4 links** for
  the duration. SSH is over Wi-Fi and is unaffected, but the RDMA rail disappears.
- A PCI/ACPI reset can, on some firmware, fail to re-enumerate the controller or
  disturb shared power. If rank 1 becomes unreachable you need physical access;
  rank 1 boots Windows first in EFI order.
- Do not run this while any Gufo service is active.

## Rollback

- If a phase fails before `load`: run `load` on both hosts, then the normal
  `recovery-helper.py prepare` (rank 1 then rank 0) and the dashboard Start.
- If you want to abandon after `load`: the stock `thunderbolt` module is loaded and
  the platform is in its normal (pre-reset) state; simply run the normal recovery
  and restart. No file, module or parameter was changed by this procedure.
