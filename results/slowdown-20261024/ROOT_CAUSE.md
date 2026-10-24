# TP2 slowdown root cause — 2026-10-24

Status: **cause identified (high confidence), fix requires authorization outside the
standing `sudo -n` grants.** No host/kernel/provider change has been made.

## 1. What was measured (both ranks, services stopped, fresh transport)

Production GPU-SEND path (`communicator-probe-normal --api queue`), exact-oracle pass
on every size. `enqueue` is only ~18 us; the entire cost is the wait for the peer.

| payload | ops | enqueue | drain | per-op |
|---|---|---|---|---|
| 64 B    | 512  | 0.0185 s | 6.007 s | **11.71 ms** |
| 10 KiB  | 1024 | 0.0180 s | 11.585 s | **11.33 ms** |
| 2.88 MiB| 46   | 0.0128 s | 0.510 s | **11.09 ms** |

`--api start_finish` gives the same 11.7 ms (all inside the synchronous call).
Two orders of magnitude independent of payload => a **fixed per-operation cost**, not
bandwidth. For reference, the fork's own lab measured ~30 us/op for a 1-row
all-reduce, and 404 us/msg for 1 MiB on this hardware after it was healthy.

## 2. Cause (driver source attribution)

One dmabuf SEND waits for the NHI TX data-ring **completion interrupt**
(`tbv_dmabuf_complete`, `verbs_dmabuf.c:648`) to zero `batch->pending`. The wait is
`tbv_dmabuf_batch_wait()` `wait_event_timeout(..., msecs_to_jiffies(10))`
(`verbs_dmabuf.c:963-966`); on timeout it is rescued by `tbv_dmabuf_batch_kick()`.

Normally a 1 ms TX-ring progress poller would mask a missing interrupt, but
`path.c:1273-1275` **explicitly disables `tx_poll_enabled` whenever dmabuf/gpu_direct
verbs is on**. So in dmabuf mode the only recovery is the 10 ms timeout. Result:
a uniform ~10 ms added to every WR — exactly the measured 11.3-11.7 ms.

The missing signal is the NHI data-ring completion interrupt (host-side NHI state),
not a configuration difference and not bandwidth.

## 3. Corroboration — this is the documented Oct-8 signature

`repos/donnerkeule/bench/results/2026-10-08-hardware.md` (~lines 100-131) records the
same fault after a forced link teardown on this same hardware:
"das lokale TX-Warten haeufig rund 10 ms", 1 MiB control ~**11.7 ms/Nachricht**,
payloads correct, DONE still delivered, STATUS stayed 0, lost data-ring interrupts.
There, **module reload/re-init and the kernel function PM reset did NOT fix it**;
only an **ACPI reset of both hosts' USB4 NHI functions** (with the thunderbolt driver
unloaded) restored it, after which 1 MiB dropped to ~404 us/msg and the data-ring
interrupt counters rose again.

## 4. Why the ordinary config is exonerated

Both the fast Oct-19 run and the current runs used the identical dmabuf path
(`windows=gpu_send`) and the identical module flags, including
`tx_poll enabled=0` / `rx_supp_poll enabled=0` (verified in the Oct-19 evidence and in
the current counters). Same config, different NHI interrupt behaviour => the
difference is NHI hardware/firmware state, not a setting.

Module `.ko` hash, provider hash, model hashes, image, kernel pairs, power profile and
GPU clocks were all already verified identical/changed-without-effect; the `dc55ce48`
binary swap measured the same 4.4 tok/s.

## 5. Current counters (rank 0, last run)

- `data_tx_posted == data_tx_completed` on every rail; `control_status: 0` everywhere
  (the STATUS fallback, used when DONE is missed, never fires) => the missing
  notification is the TX-completion path, not the pushed-DONE path.
- `tx_poll enabled=0`, `rx_supp_poll enabled=0` on every rail (no safety-net poller).
- Link healthy: both cables 20.0 Gb/s x 2 lanes, 4 rails active, `gpu_stopped=0`.
- Uptime 5 days 6:47 (the NHI has not been re-powered since the fault began).

## 6. Fix options and what each needs

| # | Action | Evidence basis | Authorization needed |
|---|---|---|---|
| A | **ACPI reset of both hosts' USB4 NHI functions**, thunderbolt unloaded | The only documented cure for this exact signature (Oct 8) | New scoped root authorization + rank-1 remote-recovery plan (rank 1 boots Windows-first) |
| B | **Full power cycle (cold power-off) of both hosts** | Colder than the failed warm reboot; clears NHI power state | User physical action |
| C | **Unplug/replug both USB4 cables** on both hosts | Never tried; a link-retrain event | User physical action |
| D | **Host-window transport mode** diagnostic (`driver.sh up <rank> host`) | Would re-enable the 1 ms `tx_poll` safety net -> ~1 ms instead of ~10 ms penalty; also isolates the dmabuf TX path | One-time visible-terminal authorization (outside standing grants) |

Warm reboot of rank 1 (2026-10-23) did not fix it; module reload (bilateral prepare)
does not reset the NHI and has not fixed it.

## 7. RESOLUTION (verified 2026-10-24)

The ACPI NHI reset (option A) was run on both hosts and **fixed it**.

- Reset: `0000:c7:00.5` and `0000:c7:00.6` ACPI-reset `rc=0` on both hosts,
  vendor `0x1022` retained, `reset_method` restored to `acpi pm`, both NHIs
  re-bound `2/2`. Receipts: `nhi-reset-receipts/` (rank0 + `rank1-remote/`).
- Cables after reset: both `20.0 Gb/s` x `2` lanes = 40 Gb/s per direction on
  both hosts (no replug was needed; links had already trained).
- Per-exchange (10 KiB queue, 512 ops, exact oracle pass):
  **11.33 ms -> 0.161 ms** (collective wall 0.08242 s / 512).
- Full-model request (cold prefill, greedy, 64 tokens):
  **prefill 1110.2 tok/s, decode 29.61 tok/s**, draft accepted 22/54,
  `cache_hit=false`. That matches the Oct-19 fast baseline (1103.8 / 29.2).

Conclusion: the earlier fix-by-replug restored *link training*; this fault was
NHI host-side interrupt state, which a replug does not clear but an ACPI reset
does. The ACPI reset is the verified cure for this occurrence.

## 8. Bottom line

The pair is not slow because of the binary, the model, the provider, the kernel pair,
power or clocks. The USB4 NHI on the host(s) is no longer delivering data-ring TX
completion interrupts, so every dmabuf SEND falls through the driver's 10 ms
missed-notification timeout. This is the same fault the Donnerkeule lab fixed on
2026-10-08 with an ACPI NHI reset on both hosts. Restoring it needs an action outside
the standing grants; nothing further can be fixed by configuration alone.
