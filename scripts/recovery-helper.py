#!/usr/bin/python3 -I
"""Installed, root-owned recovery entry point; accepts only fixed operations."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
       'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1'}


def trusted(path):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        raise ValueError(f'Recovery path is not root-owned and protected: {path}')


def verify(root=ROOT):
    for path in (root, *root.parents):
        trusted(path)
    trusted(root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    rank = manifest['rank']
    if type(rank) is not int or rank not in (0, 1) or root.name != f'evox2-recovery-rank{rank}':
        raise ValueError('Invalid installed recovery identity')
    for name, digest in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid recovery manifest path')
        path = root / relative
        for parent in path.parents:
            if parent == root:
                break
            trusted(parent)
        trusted(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Recovery artifact changed: {name}')
    return manifest


def run(args, *, timeout=120, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, env=ENV,
                          cwd=ROOT, timeout=timeout, **kwargs)


def require_stopped(rank):
    state = run(['/usr/bin/systemctl', 'show', f'evox2-gufo@{rank}.service',
                 '-p', 'ActiveState', '--value'], capture_output=True, text=True).stdout.strip()
    if state not in ('inactive', 'failed'):
        raise ValueError('Stop paired inference before driver recovery or reboot.')


def dispatch(action, manifest):
    rank = manifest['rank']
    state = ROOT / '.private' / f'driver-state-{rank}'
    if action == 'status':
        print(json.dumps({'installed': True, 'rank': rank, 'kernel': manifest['kernel'],
                          'state_owned': state.exists(), 'settings': manifest['settings']}))
        return
    if action not in ('prepare', 'down', 'counters', 'reboot', 'revoke',
                      'nhi-unload', 'nhi-reset', 'nhi-load'):
        raise ValueError('Unknown recovery operation')
    if os.geteuid() != 0:
        raise ValueError('Use the installed exact sudo grant for this operation.')
    if action == 'revoke':
        path = Path(f'/etc/sudoers.d/evox2-recovery-rank{rank}')
        if path.exists():
            trusted(path)
            if path.read_text() != manifest['policy']:
                raise ValueError('Refusing to remove a different sudo policy')
            path.unlink()
            run(['/usr/sbin/visudo', '-c'], stdout=subprocess.DEVNULL)
        print('Recovery permissions removed; pinned bundle and driver state retained.')
        return
    if os.uname().release != manifest['kernel']:
        raise ValueError('Running kernel differs from the authorized recovery bundle.')
    if action == 'counters':
        print(Path('/sys/kernel/debug/thunderbolt_ibverbs/peers').read_text(), end='')
        return
    require_stopped(rank)
    if action == 'reboot':
        if rank != 1:
            raise ValueError('Only rank 1 has a reboot grant.')
        run(['/usr/bin/systemctl', 'start', 'reboot.target',
             '--job-mode=replace-irreversibly', '--no-block'])
        return
    if state.exists():
        trusted(state)
    if Path('/sys/module/thunderbolt_ibverbs').exists() and not state.exists():
        raise ValueError('Refusing to replace an unowned RDMA module.')
    if action == 'prepare':
        # All imports, configuration, and the kernel module are frozen root-owned
        # files, verified above. The working checkout is never executed as root.
        sys.path.insert(0, str(ROOT / 'scripts'))
        import cluster
        config = cluster.config(require_peer=True)
        cluster.route(config['peer_lan'] if rank == 0 else config['rank0_lan'])
        links = list(Path('/sys/bus/thunderbolt/devices').glob('*/rx_speed'))
        if len(links) != config['cables']:
            raise ValueError('Both USB4 cables must be connected.')
        for speed in links:
            for name in ('rx_speed', 'tx_speed', 'rx_lanes', 'tx_lanes'):
                value = (speed.parent / name).read_text().strip()
                if not (value.startswith('20.0') if name.endswith('speed') else value == '2'):
                    raise ValueError('USB4 cable is below 2 x 20 Gb/s.')
    driver = ROOT / 'scripts/driver.sh'
    nhi = ROOT / 'scripts/nhi-reset.sh'
    if action in ('nhi-reset', 'nhi-load'):
        # Both phases must start from an unloaded platform: the reset requires the
        # NHI functions to be unbound, and load must never run over a live module.
        if Path('/sys/module/thunderbolt_ibverbs').exists():
            raise ValueError('Unload the paired RDMA driver with nhi-unload first.')
        if Path('/sys/module/thunderbolt').exists():
            raise ValueError('Unload the thunderbolt module with nhi-unload first.')
        receipts = ROOT / 'logs' / 'nhi-reset'
        run(['/usr/bin/bash', nhi, 'reset' if action == 'nhi-reset' else 'load', rank, receipts],
            timeout=180)
        return
    if state.exists():
        run(['/usr/bin/bash', driver, 'down', rank])
    if action == 'prepare':
        run(['/usr/bin/bash', driver, 'up', rank, manifest['settings']['transport']])
    if action == 'nhi-unload':
        receipts = ROOT / 'logs' / 'nhi-reset'
        run(['/usr/bin/bash', nhi, 'unload', rank, receipts])


def main():
    if len(sys.argv) != 2:
        raise ValueError('Exactly one recovery operation is required.')
    os.umask(0o077)
    os.chdir(ROOT)
    dispatch(sys.argv[1], verify())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(f'RECOVERY: {error}') from error
