#!/usr/bin/env python3
"""Review/install exact recovery grants; one terminal authentication per host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SETTINGS = ('transport', 'cables', 'rdma_device', 'rank0_lan', 'peer_lan')
ACTIONS = ('prepare', 'down', 'counters', 'nhi-unload', 'nhi-reset', 'nhi-load', 'revoke')
MARKER = '# EVO-X2 recovery: pinned root-owned commands only\n'


def bundle(rank):
    if type(rank) is not int or rank not in (0, 1):
        raise ValueError('Invalid rank')
    return Path(f'/usr/local/lib/evox2-recovery-rank{rank}')


def helper(rank):
    return bundle(rank) / 'scripts/recovery-helper.py'


def policy(user, rank):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user) or user == 'root':
        raise ValueError('Invalid account')
    actions = list(ACTIONS)
    if rank == 1:
        actions.append('reboot')
    commands = ', '.join(f'{helper(rank)} {action}' for action in actions)
    return MARKER + f'{user} ALL=(root) NOPASSWD: NOSETENV: {commands}\n'


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(root, kernel):
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', kernel):
        raise ValueError('Invalid kernel release')
    module = f'builds/donnerkeule-{kernel}/kernel/thunderbolt_ibverbs.ko'
    expected = json.loads((root / 'config/builds.lock.json').read_text())['modules'][module]
    if sha(root / module) != expected:
        raise ValueError('Kernel module does not match the pinned build manifest')
    vermagic = run(['/usr/sbin/modinfo', '-F', 'vermagic', root / module],
                   capture_output=True, text=True).stdout
    if not vermagic.startswith(kernel + ' '):
        raise ValueError('Kernel module vermagic mismatch')
    return ['scripts/recovery-helper.py', 'scripts/driver.sh', 'scripts/nhi-reset.sh',
            'scripts/cluster.py', 'scripts/dashboard_probe.py', 'config/cluster.json', module]


def protected(path):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        raise ValueError(f'Unprotected installation path: {path}')


def install_root(root, rank):
    if os.geteuid() != 0 or not os.environ.get('SUDO_USER'):
        raise ValueError('Installer must be authenticated through sudo.')
    user = os.environ['SUDO_USER']
    os.umask(0o022)
    account = pwd.getpwnam(user)
    if root.stat().st_uid != account.pw_uid:
        raise ValueError('Workspace does not belong to the authenticating account')
    destination = bundle(rank)
    sudoers = Path(f'/etc/sudoers.d/evox2-recovery-rank{rank}')
    text = policy(user, rank)
    for path in (destination.parent, *destination.parent.parents, sudoers.parent):
        protected(path)
    if destination.exists() or sudoers.exists() or destination.is_symlink() or sudoers.is_symlink():
        raise ValueError('Recovery installation already exists; refusing to overwrite it.')
    config = json.loads((root / 'config/cluster.json').read_text())
    if config['transport'] != 'dmabuf' or config['cables'] != 2:
        raise ValueError('This authorization is limited to the current two-cable DMA-BUF setup.')
    if (root / '.private/usb4-copy-network.json').exists():
        raise ValueError('Remove the temporary model-copy network before installation.')
    kernel = os.uname().release
    names = source_files(root, kernel)
    snapshots = {name: (root / name).read_bytes() for name in names}
    # Check the snapshot, not just the earlier file read, against the pinned hash.
    pins = json.loads((root / 'config/builds.lock.json').read_text())['modules']
    module = names[-1]
    if hashlib.sha256(snapshots[module]).hexdigest() != pins[module]:
        raise ValueError('Module changed during authorization')
    state = root / '.private' / f'driver-state-{rank}'
    old_state = None
    if state.exists():
        if state.is_symlink():
            raise ValueError('Legacy driver state must not be a symlink')
        old_state = state.read_bytes()
        if old_state not in (b'thunderbolt_net=loaded\n', b'thunderbolt_net=absent\n'):
            raise ValueError('Unexpected legacy driver state')
    if Path('/sys/module/thunderbolt_ibverbs').exists() and old_state is None:
        raise ValueError('Loaded driver is not owned by the workspace')
    run(['/usr/sbin/visudo', '-c'], stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='.evox2-recovery-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        for name, data in snapshots.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            target.write_bytes(data)
            target.chmod(0o755 if name == 'scripts/recovery-helper.py' else 0o644)
        for name in ('.private', 'logs'):
            (stage / name).mkdir(mode=0o755)
        if old_state is not None:
            target = stage / '.private' / f'driver-state-{rank}'
            target.write_bytes(old_state)
            target.chmod(0o600)
        manifest = {'version': 1, 'rank': rank, 'user': user, 'kernel': kernel,
                    'settings': {name: config[name] for name in SETTINGS}, 'policy': text,
                    'files': {name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (stage / 'manifest.json').chmod(0o644)
        candidate = stage / 'sudoers'
        candidate.write_text(text)
        candidate.chmod(0o440)
        run(['/usr/sbin/visudo', '-cf', candidate], stdout=subprocess.DEVNULL)
        # Rename only a complete root-owned bundle. No user-writable scripts or
        # module paths become privileged entry points.
        stage.rename(destination)
        try:
            # TemporaryDirectory cleanup still expects its original directory.
            stage.mkdir(mode=0o700)
            run([helper(rank), 'status'], stdout=subprocess.DEVNULL)
            run(['/usr/bin/install', '-o', 'root', '-g', 'root', '-m', '0440',
                 destination / 'sudoers', sudoers])
            run(['/usr/sbin/visudo', '-c'], stdout=subprocess.DEVNULL)
        except BaseException:
            if sudoers.exists() and sudoers.read_text() == text:
                sudoers.unlink()
            shutil.rmtree(destination)
            raise
    print(f'Rank {rank}: pinned recovery bundle and exact sudo grants installed.', flush=True)


def local(root, rank, rollback):
    if os.geteuid() == 0 or not sys.stdin.isatty():
        raise ValueError('Run from the normal account in a terminal.')
    if helper(rank).exists():
        run([helper(rank), 'status'], stdout=subprocess.DEVNULL)
        if rollback:
            run(['sudo', '-n', helper(rank), 'revoke'])
            return
        for action in list(ACTIONS) + (['reboot'] if rank == 1 else []):
            run(['sudo', '-n', '-l', helper(rank), action], stdout=subprocess.DEVNULL)
        print(f'Rank {rank}: recovery authorization is already installed.', flush=True)
        return
    if rollback:
        print(f'Rank {rank}: no recovery authorization installed.', flush=True)
        return
    # Validate source artifacts before the only authentication prompt on this host.
    source_files(root, os.uname().release)
    print(f'Rank {rank}: one-time authorization; type this host password below.', flush=True)
    run(['sudo', '-p', f'[SETUP RANK {rank}] Password for %u: ', '/usr/bin/python3', '-I',
         root / 'scripts/recovery_authorize.py', '--root-install', str(rank), '--root', root])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--rollback', action='store_true')
    parser.add_argument('--local-rank', type=int, choices=(0, 1), help=argparse.SUPPRESS)
    parser.add_argument('--root-install', type=int, choices=(0, 1), help=argparse.SUPPRESS)
    parser.add_argument('--root', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.root or Path(__file__).resolve().parents[1]
    if args.root_install is not None:
        install_root(root, args.root_install)
        return
    if args.local_rank is not None:
        if not (args.install or args.rollback):
            raise ValueError('Explicit installation or rollback mode is required.')
        local(root, args.local_rank, args.rollback)
        return
    import cluster
    config = cluster.config(require_peer=True)
    if not (args.install or args.rollback):
        print('READ-ONLY REVIEW: exact grants plus root-owned copies of the current driver,\n'
              'kernel module, helper, and configuration. No boot or runtime changes.\n')
        print(policy(pwd.getpwuid(os.getuid()).pw_name, 0))
        print(policy(config['peer_user'], 1))
        return
    if os.geteuid() == 0 or not sys.stdin.isatty():
        raise ValueError('Run from the normal rank-0 terminal.')
    cluster.route(config['peer_lan'])
    receipt = root / '.private/recovery-authorization.json'
    status = {'time': time.time(), 'action': 'rollback' if args.rollback else 'install',
              'rank0': 'pending', 'rank1': 'pending',
              'rollback': 'python3 scripts/recovery_authorize.py --rollback'}
    try:
        # Check the peer before asking for either password.
        run(cluster.ssh(config, ['test', '-f', config['peer_root'] + '/scripts/recovery_authorize.py']))
        local(root, 0, args.rollback)
        status['rank0'] = 'complete'
        cluster.save(receipt, status)
        remote = ['python3', config['peer_root'] + '/scripts/recovery_authorize.py',
                  '--local-rank', '1', '--rollback' if args.rollback else '--install']
        run(['ssh', '-F', cluster.PRIVATE / 'ssh_config', '-tt', '-o', 'BatchMode=yes',
             f'{config["peer_user"]}@{config["peer_lan"]}', shlex.join(remote)])
        status['rank1'] = 'complete'
    finally:
        cluster.save(receipt, status)
    print('Both hosts configured. Routine recovery now uses the exact noninteractive grants.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(f'RECOVERY AUTHORIZATION: {error}') from error
