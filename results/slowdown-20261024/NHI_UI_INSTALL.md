# Install the dashboard "Reset USB4 NHI" action (one time, both hosts)

Status: **INSTALLED and verified on both hosts on 2026-10-24T21:13Z.** The three
exact grants (`nhi-unload`, `nhi-reset`, `nhi-load`) and the pinned
`scripts/nhi-reset.sh` are present in both installed bundles; the dashboard
button works without a token or password. Receipts, hashes and the verified
grant list: `install-20261024/README.md`, `rank0-after.txt`, `rank1-after.txt`
and the pre-install `rank1-before.txt`.

The procedure below is retained as the repeatable reinstall path (for a future
bundle update, or a new host). Two corrections were learned during the actual
install and are already included: rank 1 needs the changed scripts synced
first, and the installer must pin `scripts/dashboard_probe.py` because the
bundled `cluster.py` imports it.

Run everything below from a **visible terminal on rank 0**. Nothing is stored
and no password goes into a file.

## 0. Preconditions

- Both hosts reachable over the LAN/Wi-Fi (not USB4).
- Inference stopped on both ranks. Use the dashboard **Stop both**, or the exact
  grants:
  ```sh
  sudo -n /usr/bin/systemctl stop evox2-gufo@0.service
  ssh -F .private/ssh_config -T -o BatchMode=yes ercan@192.168.0.217 \
      'sudo -n /usr/bin/systemctl stop evox2-gufo@1.service'
  ```
  The RDMA driver does **not** need to be unloaded to replace the bundles;
  **Start both** performs fresh bilateral preparation afterwards.

Known state before this procedure (verified 2026-10-24): both hosts already have
`/etc/sudoers.d/evox2-recovery-rank{0,1}` listing the three `nhi-*` actions, but
the installed helper is the older 2026-10 build that does not implement them and
`scripts/nhi-reset.sh` is absent. `recovery_authorize.py --rollback` therefore
refuses ("different sudo policy"), because the installed policy no longer matches
the installed manifest. Use the manual removal in step 3. The dashboard now
refuses the button in this state before stopping inference.

## 1. Verify the workspace artifacts first (read-only)

```sh
cd /home/ercanozcan/Desktop/llm/evox2-cluster
bash -n scripts/nhi-reset.sh
sha256sum scripts/nhi-reset.sh
python3 -c "import sys; sys.path.insert(0,'scripts'); import recovery_authorize as a; print(a.policy('ercan',0))"
```

The printed policy must contain exactly the three new actions plus the existing
ones, with absolute paths and no wildcards. Record the `nhi-reset.sh` sha256;
the installer pins it in `manifest.json`.

## 2. Sync the changed scripts to rank 1 (one time)

Rank 1's workspace copy is older: it has no `scripts/nhi-reset.sh` and its
helper/authorizer differ, so installing from rank 0 would otherwise put the old
policy on rank 1. Two of the bundled files are shared and already identical
(`driver.sh`, `cluster.py`, `config/cluster.json`); copy only the three that
changed:

```sh
scp -F .private/ssh_config scripts/nhi-reset.sh scripts/recovery-helper.py \
    scripts/recovery_authorize.py \
    ercan@192.168.0.217:/home/ercan/Desktop/llm/evox2-cluster/scripts/
```

Confirm the peer hashes now match the rank-0 copies:

```sh
sha256sum scripts/nhi-reset.sh scripts/recovery-helper.py scripts/recovery_authorize.py
ssh -F .private/ssh_config -T -o BatchMode=yes ercan@192.168.0.217 \
  'sha256sum /home/ercan/Desktop/llm/evox2-cluster/scripts/nhi-reset.sh \
   /home/ercan/Desktop/llm/evox2-cluster/scripts/recovery-helper.py \
   /home/ercan/Desktop/llm/evox2-cluster/scripts/recovery_authorize.py'
```

Both `config/builds.lock.json` copies are already identical and pin both hosts'
kernel modules, so nothing else needs syncing.

## 3. Remove the stale policy and bundle (one time, visible terminal)

`--rollback` cannot be used here: it compares the installed sudoers file with the
installed manifest and now refuses. Remove exactly four items after checking the
manifest identity:

```sh
python3 -c "import json;print(json.load(open('/usr/local/lib/evox2-recovery-rank0/manifest.json'))['rank'])"
ssh -F .private/ssh_config -T -o BatchMode=yes ercan@192.168.0.217 \
  'python3 -c "import json;print(json.load(open(\"/usr/local/lib/evox2-recovery-rank1/manifest.json\"))[\"rank\"])"'
sudo rm -f /etc/sudoers.d/evox2-recovery-rank0
sudo rm -rf /usr/local/lib/evox2-recovery-rank0
ssh -t -F .private/ssh_config ercan@192.168.0.217 \
  'sudo rm -f /etc/sudoers.d/evox2-recovery-rank1 && sudo rm -rf /usr/local/lib/evox2-recovery-rank1'
```

Both printed ranks must be `0` and `1` respectively, and
`/usr/local/lib/evox2-recovery-rank{0,1}/manifest.json` must exist and be owned
by root, before removing anything. Do not remove any other path. Concurrent sudo
access is unavailable for a few seconds until step 4 finishes; do not start
inference in that window.

## 4. Install the reviewed bundles

```sh
python3 scripts/recovery_authorize.py --install
```

- Validates the module pin, the helper, `driver.sh`, `nhi-reset.sh`,
  `cluster.py` and `config/cluster.json` **before** the first prompt.
- Prompts once for rank 0, then once over `ssh -tt` for rank 1.
- Writes root-owned bundles, runs the helper's `status` check, installs the two
  `sudoers.d` files and validates them with `visudo -c`.

## 5. Verify the installed grants and bytes

```sh
for a in nhi-unload nhi-reset nhi-load; do
  sudo -n -l /usr/local/lib/evox2-recovery-rank0/scripts/recovery-helper.py $a >/dev/null && echo "rank0 $a ok"
done
ssh -F .private/ssh_config -T -o BatchMode=yes ercan@192.168.0.217 '
  for a in nhi-unload nhi-reset nhi-load; do
    sudo -n -l /usr/local/lib/evox2-recovery-rank1/scripts/recovery-helper.py $a >/dev/null && echo "rank1 $a ok"
  done'
sha256sum /usr/local/lib/evox2-recovery-rank0/scripts/nhi-reset.sh \
          scripts/nhi-reset.sh
python3 -c "import json;print(json.load(open('/usr/local/lib/evox2-recovery-rank0/manifest.json'))['files']['scripts/nhi-reset.sh'])"
```

The installed copy hash and the workspace copy hash must equal the manifest
entry. The same check for rank 1 is read-only over SSH.

## 6. Restore service

Press **Start both** in the dashboard. It performs the normal read-only checks,
fresh bilateral driver preparation and API-readiness wait. The new button
**Reset USB4 NHI** becomes usable only when the three grants verify on both
hosts; otherwise it refuses before stopping anything.

## Rollback

- Before step 3: nothing was changed; delete no files.
- After step 3, before step 4: no sudo grant and no bundle exists on either
  host, so the dashboard cannot use any recovery action. Reinstall immediately
  from this tree, or from the previous workspace revision using that tree's
  `python3 scripts/recovery_authorize.py --install`.
- After step 4: the new bundle is a strict superset of the old one; drivers,
  kernel, transport mode, config and services are untouched. To go back, repeat
  step 3 on both hosts and install from the previous workspace revision. The
  running inference is unaffected either way.

## Safety properties of the installed action

- Only the two USB4 NHI PCI functions `0000:c7:00.5` and `0000:c7:00.6` are
  reset; class `0x0c0340` and vendor `0x1022` are asserted first.
- The USB4 PCIe bridges `0000:00:01.1` and `0000:00:01.2` are never touched.
- The helper refuses any action when the running kernel differs from the pinned
  manifest, when the service is active, or when the RDMA module is unowned.
- `nhi-reset`/`nhi-load` refuse to run while `thunderbolt_ibverbs` or
  `thunderbolt` is loaded; `nhi-unload` removes the pinned driver first.
- The dashboard never starts inference, never reboots and never retries the
  reset automatically.
