# NHI reset dashboard action — installed and verified (2026-10-24T21:13Z)

Status: **INSTALLED on both hosts and serving.** The dashboard action
`Reset USB4 NHI` is now usable without a token or password; it refuses only
when its preflight finds a stale bundle or missing grant.

## What was installed

| Host | Bundle | Kernel | Helper sha256 (first 16) | `state_owned` |
|---|---|---|---|---|
| rank 0 | `/usr/local/lib/evox2-recovery-rank0` | 7.2.5-200.fc44.x86_64 | `59e44a775515ee2d` | true |
| rank 1 | `/usr/local/lib/evox2-recovery-rank1` | 6.19.10-300.fc44.x86_64 | `59e44a775515ee2d` | true |

Both manifests pin `scripts/nhi-reset.sh` at
`46deedbb3111c5cf38f4574d8baf244ba7d981a3ac0e5b68cd9a8ee56a4dfb56`, which is
byte-identical to the workspace copy.

Passwordless (`NOPASSWD`) grants now present on both hosts:
`prepare`, `down`, `counters`, `nhi-unload`, `nhi-reset`, `nhi-load`, `revoke`
(rank 1 additionally `reboot`).

## How it was installed

1. Synced `scripts/nhi-reset.sh`, `scripts/recovery-helper.py` and
   `scripts/recovery_authorize.py` to rank 1 (its workspace copy was older).
2. Removed the stale `/etc/sudoers.d/evox2-recovery-rankN` and
   `/usr/local/lib/evox2-recovery-rankN` on both hosts with one authenticated
   `sudo` per host.
3. Installed with
   `sudo python3 -I scripts/recovery_authorize.py --root-install <rank> --root <workspace>`
   on each host, equivalent to the `--install` path but without the interactive
   prompt wrapper.
4. Verified manifests, pinned runner hash, helper `status` (`state_owned: true`)
   and the exact `NOPASSWD` grant list (see `rank0-after.txt`,
   `rank1-after.txt`, and the pre-install `rank1-before.txt`).

Installing twice was necessary: the first install omitted
`scripts/dashboard_probe.py`, which the bundled `cluster.py` imports at top
level, so rank 1's `prepare` failed inside the helper. The installer now pins
that module; the second install is the one recorded above.

## Verification after install

- `python3 -c 'import dashboard; dashboard.check_nhi_grants(cluster.config(require_peer=True))'`
  → passed on both hosts.
- Dashboard **Start both** → `success`, API `/ready` 200, Pi bridge ready.
- Both cables 20.0 Gb/s × 2 lanes in each direction on both hosts.
- One live request: 55 prompt + 8 completion tokens in 0.55 s wall time,
  TTFT 378 ms, 4090/4090 data TX completions, `data_rx_lost=0`,
  `gpu_stopped=0`, credits 768/768.

## Known limitation recorded during this install

`sudo -n -l <helper> <action>` returns success because the account has
`(ALL) ALL` in the base sudoers policy, even when the action is **not** in a
`NOPASSWD` rule. Grant checks that rely on `-n -l` alone are therefore weak.
The dashboard's `nhi-reset` preflight additionally requires the installed
manifest to pin `scripts/nhi-reset.sh` and to contain the three actions in its
policy text, which is what caught the stale bundle. Hardening the remaining
`-n -l` checks (dashboard and `prepare-rdma-pair.py`) by parsing the
`NOPASSWD` lines is a follow-up, not a blocker for the installed action.

## Rollback

Remove the same two root paths per host (`/etc/sudoers.d/evox2-recovery-rankN`
and `/usr/local/lib/evox2-recovery-rankN`) and reinstall from the previous
workspace revision. No kernel, driver, transport, config or service change is
involved; inference can keep running.
