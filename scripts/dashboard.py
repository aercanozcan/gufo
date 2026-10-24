#!/usr/bin/env python3
"""Loopback rank-0 dashboard with automatic local or token-based sessions."""
import argparse
from contextlib import contextmanager
import fcntl
from concurrent.futures import ThreadPoolExecutor
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

import cluster
import dashboard_probe

ROOT = cluster.ROOT
TOKEN_FILE = cluster.PRIVATE / 'dashboard-token'
ONE_POST_RECOVERY_GATE = ROOT / '.private/one-post-recovery-gate.json'
SYNTHETIC_FIXTURE_GATE = ROOT / '.private/recovery-gates/20261023-hostmr-sendimm-129-v1.json'
SYNTHETIC_FIXTURE_RECONCILE_BLOCKER = ROOT / '.private/recovery-gates/20261023-hostmr-sendimm-129-v1.reconciliation-incomplete.json'
SYNTHETIC_FIXTURE_COMMIT_RECEIPT = ROOT / '.private/reconciled-gates/20261023-hostmr-sendimm-129-v1-a12068d8eabe044ed8c5091f6d2c6caa3-81b0dc204da561c455a53bae59d2bf01a6d1e8354d182636b9ee09dad1ec65e2.reconciliation.json'
MANIFEST_REPAIR_GATE = ROOT / '.private/summary-manifest-repair-incomplete.json'
SYSTEMCTL = '/usr/bin/systemctl'
PI_BRIDGE_UNIT = 'evox2-halogen-bridge.service'
PI_BRIDGE_MODEL = 'halogen-qwen3.8-flash-next'
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SNAPSHOT_MAX_AGE = 12


_PAIR_LOCK = threading.local()


@contextmanager
def paired_operation_lock(*, stop_only=False):
    """Reserve paired controls across processes; nested CLI activation is safe."""
    if getattr(_PAIR_LOCK, 'held', False):
        if not stop_only:
            require_no_incomplete_safety_gates()
        yield
        return
    with (cluster.PRIVATE / 'pair-control.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not (stop_only and manifest_repair_gate_present()):
                raise RuntimeError('Paired controls are reserved by another operation or benchmark. Retry when it finishes.') from None
            _PAIR_LOCK.held = True
            try:
                yield
            finally:
                _PAIR_LOCK.held = False
            return
        _PAIR_LOCK.held = True
        try:
            if not stop_only:
                require_no_incomplete_safety_gates()
            yield
        finally:
            _PAIR_LOCK.held = False
            fcntl.flock(handle, fcntl.LOCK_UN)


def require_no_incomplete_one_post_run():
    """Fail closed while a prior supervised one-request run needs review."""
    # lstat intentionally treats malformed files and dangling symlinks as a
    # present gate. Only the reviewed reconciliation flow may remove it.
    try:
        os.lstat(ONE_POST_RECOVERY_GATE)
    except FileNotFoundError:
        return
    except OSError:
        raise RuntimeError('Could not verify the one-request recovery gate. '
                           'Start, Restart and Recover are blocked; Stop and status remain available.') from None
    raise RuntimeError('An incomplete one-request run requires recovery review. '
                       'Start, Restart and Recover are blocked; Stop and status remain available.')


def manifest_repair_gate_present():
    """Treat every existing entry, including a symlink or directory, as active."""
    try:
        os.lstat(MANIFEST_REPAIR_GATE)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def require_no_incomplete_manifest_repair():
    try:
        os.lstat(MANIFEST_REPAIR_GATE)
    except FileNotFoundError:
        return
    except OSError:
        raise RuntimeError('Could not verify the summary manifest repair gate. '
                           'Start, Restart and Recover are blocked; Stop and status remain available.') from None
    raise RuntimeError('An incomplete summary manifest repair requires settlement review. '
                       'Start, Restart and Recover are blocked; Stop and status remain available.')


def require_no_incomplete_synthetic_fixture_run():
    """Block ordinary service starts while the fixed synthetic run is gated."""
    try:
        cluster._require_synthetic_fixture_gate_clear(
            gate=SYNTHETIC_FIXTURE_GATE,
            blocker=SYNTHETIC_FIXTURE_RECONCILE_BLOCKER,
            receipt=SYNTHETIC_FIXTURE_COMMIT_RECEIPT)
    except (OSError, ValueError):
        raise RuntimeError('The synthetic host fixture requires settlement review. '
                           'Start, Restart and Recover are blocked; Stop and status remain available.') from None


def require_no_incomplete_payload_fixture_run():
    """Block service admission while payload markers or guards need review."""
    try:
        cluster._require_payload_fixture_gate_clear()
    except (OSError, ValueError):
        raise RuntimeError('The payload host fixture requires settlement review. '
                           'Start, Restart and Recover are blocked; Stop and status remain available.') from None


def require_no_incomplete_safety_gates():
    require_no_incomplete_one_post_run()
    require_no_incomplete_manifest_repair()
    require_no_incomplete_synthetic_fixture_run()
    require_no_incomplete_payload_fixture_run()


def invoke(args, *, source=None, timeout=12):
    try:
        p = subprocess.run([str(a) for a in args], input=source, stdin=None if source is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Command timed out; verify both host service states before retrying.') from None
    if p.returncode:
        # stderr can include arbitrary log/argument text. Return a fixed diagnostic only.
        raise RuntimeError(f'Command failed (exit {p.returncode}); check SSH, service permissions and the rank0 terminal.')
    return p.stdout


def api_get(c, path):
    key = (cluster.PRIVATE / 'api-key').read_text().strip()
    request = urllib.request.Request(f'http://{c["rank0_lan"]}:{c["port"]}{path}',
                                    headers={'Authorization': 'Bearer ' + key})
    with HTTP.open(request, timeout=3) as response:
        return response.read(1024 * 1024).decode()


def parse_metrics(body):
    result = {}
    for line in body.splitlines():
        match = re.fullmatch(r'([a-zA-Z_:][\w:]*)\s+([-+\d.eE]+)', line)
        if match:
            value = float(match[2])
            if math.isfinite(value):
                result[match[1]] = value
    return result


def ready_payload(body):
    data = json.loads(body)
    return isinstance(data, dict) and (data.get('status') in ('ok', 'ready') or data.get('ready') is True)


def current_transport_health(c):
    def read_rank(rank):
        helper = f'/usr/local/lib/evox2-recovery-rank{rank}/scripts/recovery-helper.py'
        command = ['sudo', '-n', helper, 'counters']
        try:
            raw = invoke(cluster.ssh(c, command) if rank else command, timeout=6)
        except (OSError, RuntimeError):
            raw = ''
        return {**dashboard_probe.parse_counter_health(raw), 'rank': rank}
    with ThreadPoolExecutor(max_workers=2) as pool:
        return dashboard_probe.pair_transport_health(list(pool.map(read_rank, (0, 1))))


def require_current_transport(c):
    health = current_transport_health(c)
    if health['status'] == 'fault':
        ranks = ', '.join(str(rank) for rank in health['fault_ranks'])
        raise RuntimeError(f'GPU transport is stopped on rank {ranks}. Use Restart both; '
                           'API readiness alone cannot recover the transport.')
    if health['status'] != 'ready':
        raise RuntimeError('Transport health is unverified on both hosts. Check host connections, '
                           'then use Restart both. No healthy transport was claimed.')


def apply_transport_health(data, hosts):
    data['api_ready'] = data.get('ready') is True
    data['transport_health'] = dashboard_probe.pair_transport_health([
        {**host.get('transport_health', {'status': 'unknown'}), 'rank': rank}
        for rank, host in enumerate(hosts)])
    data['ready'] = data['api_ready'] and data['transport_health']['status'] == 'ready'
    return data


def pair_control_available():
    """Probe the shared CLI/UI pair lock without waiting or creating its file."""
    path = cluster.PRIVATE / 'pair-control.lock'
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) |
                     getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def pair_control_busy():
    """Fail closed on a held shared lock, while treating a missing lock as idle."""
    path = cluster.PRIVATE / 'pair-control.lock'
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) |
                     getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def composite_readiness(snapshot, operation=None, *, now=None, cycle_started=None,
                        not_before=None, paired_idle=True):
    """Return only the current serving decision; incomplete evidence is not ready."""
    now = time.monotonic() if cycle_started is not None and now is None else time.time() if now is None else now
    operation = operation or {}
    ready = False
    try:
        observed_at = cycle_started if cycle_started is not None else float(snapshot['time'])
        age = now - observed_at
        hosts = snapshot['hosts']
        inference_data = snapshot['inference']
        ranks = {host['rank']: host for host in hosts if isinstance(host, dict)}
        ready = (0 <= age <= SNAPSHOT_MAX_AGE and not operation.get('running') and paired_idle and
                 (not_before is None or (cycle_started is not None and cycle_started >= not_before)) and
                 len(hosts) == 2 and set(ranks) == {0, 1} and
                 inference_data.get('api_ready') is True and
                 inference_data.get('transport_health', {}).get('status') == 'ready')
        for rank in (0, 1):
            host = ranks[rank]
            service = host.get('service', {})
            usb4 = host.get('usb4', {})
            cables = usb4.get('cables', [])
            health = host.get('transport_health', {})
            ready = (ready and not host.get('error') and service.get('ActiveState') == 'active'
                     and service.get('SubState') == 'running')
            ready = ready and usb4.get('full_speed') is True and len(cables) == 2
            ready = ready and all(cable.get('full_speed') is True for cable in cables)
            ready = ready and health.get('rank') == rank and health.get('status') == 'ready'
    except (KeyError, TypeError, ValueError, AttributeError):
        ready = False
    return {'ready': bool(ready), 'guidance': 'Ready' if ready else 'Cluster unavailable. Use Restart both.'}


def inference(c):
    result = {'time': time.time(), 'ready': False, 'metrics': {}, 'slots': []}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {name: pool.submit(api_get, c, path) for name, path in
                [('ready', '/ready'), ('metrics', '/metrics'), ('slots', '/slots')]}
        for name, job in jobs.items():
            try:
                body = job.result()
                if name == 'metrics':
                    result['metrics'] = parse_metrics(body)
                elif name == 'ready':
                    result['ready'] = ready_payload(body)
                else:
                    # /slots has a prompt property in some compatible servers. Never forward it.
                    slots = json.loads(body)
                    result['slots'] = [{k: row[k] for k in ('id', 'is_processing', 'n_prompt_tokens',
                        'n_prompt_tokens_cache', 'n_prompt_tokens_processed') if k in row} | {
                        'generated_tokens': next(iter(row.get('next_token', [])), {}).get('n_decoded', 0)
                    } for row in slots]
            except (OSError, ValueError, TypeError, KeyError):
                result[name + '_error'] = 'Unavailable; API may be stopped or loading.'
    return result


def bridge(c):
    if pair_control_busy():
        return {'reachable': False, 'port': 8731}
    try:
        with HTTP.open(f'http://{c["rank0_lan"]}:8731/health', timeout=2) as response:
            return {'reachable': response.status == 200, 'port': 8731}
    except OSError:
        return {'reachable': False, 'port': 8731}


def pi_bridge_state(*, timeout=5):
    """Read only the exact local Pi compatibility bridge user unit."""
    output = invoke([SYSTEMCTL, '--user', 'show', PI_BRIDGE_UNIT,
                     '-p', 'ActiveState', '-p', 'SubState', '--value'], timeout=timeout)
    rows = output.splitlines()
    if len(rows) != 2:
        raise RuntimeError('Could not verify the Pi bridge service state.')
    return tuple(row.strip() for row in rows)


def ensure_pi_bridge(c):
    """Start the local Pi bridge when needed and verify its exact model route."""
    active, substate = pi_bridge_state()
    started_here = False
    if (active, substate) not in (('active', 'running'),):
        if active not in ('inactive', 'failed'):
            raise RuntimeError('Pi bridge is transitioning; retry Start both after it settles.')
        if active == 'failed':
            invoke([SYSTEMCTL, '--user', 'reset-failed', PI_BRIDGE_UNIT], timeout=5)
        invoke([SYSTEMCTL, '--user', 'start', PI_BRIDGE_UNIT], timeout=10)
        started_here = True

    deadline = time.monotonic() + 10 if started_here else None
    while True:
        try:
            if started_here:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Pi bridge startup verification timed out.')
                if pi_bridge_state(timeout=min(0.5, remaining)) != ('active', 'running'):
                    raise RuntimeError('Pi bridge is still starting.')
                request_timeout = min(1, max(0.1, deadline - time.monotonic()))
            else:
                request_timeout = 5
            if not pi_bridge_model_discovered(c, request_timeout):
                raise RuntimeError('Pi bridge model discovery did not match the expected model.')
            return
        except (OSError, ValueError, TypeError, KeyError, RuntimeError,
                urllib.error.URLError):
            if not started_here or time.monotonic() >= deadline:
                raise RuntimeError('Pi bridge is unavailable or model discovery failed.') from None
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def pi_bridge_model_discovered(c, timeout):
    """Return whether one bounded, proxy-free discovery response has the exact ID."""
    try:
        request = urllib.request.Request(f'http://{c["rank0_lan"]}:8731/v1/models')
        with HTTP.open(request, timeout=timeout) as response:
            models = json.loads(response.read(65537).decode())
        rows = models.get('data') if isinstance(models, dict) else None
        return (isinstance(rows, list) and len(rows) == 1 and
                isinstance(rows[0], dict) and rows[0].get('id') == PI_BRIDGE_MODEL)
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        return False


def remote_probe(c):
    args = cluster.ssh(c, ['python3', '-', '--root', c['peer_root'], '--rank', '1'])
    return json.loads(invoke(args, source=(ROOT / 'scripts/dashboard_probe.py').read_text()))


def service_command(c, rank, action):
    command = ['sudo', '-n', SYSTEMCTL, action, f'evox2-gufo@{rank}.service']
    return cluster.ssh(c, command) if rank else command


def service_state(c, rank):
    command = ['systemctl', 'show', f'evox2-gufo@{rank}.service', '-p', 'ActiveState', '--value']
    state = invoke(cluster.ssh(c, command) if rank else command).strip()
    if state not in ('failed', 'inactive', 'active', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing'):
        raise RuntimeError(f'Could not determine rank {rank} service state.')
    return state


def reset_failed_if_needed(c, rank):
    if service_state(c, rank) == 'failed':
        invoke(service_command(c, rank, 'reset-failed'))
    # An inactive template instance may be garbage-collected between commands.
    # reset-failed then reports "not loaded"; it is unnecessary for that state.


def allowed(c, rank, action):
    command = ['sudo', '-n', '-l', SYSTEMCTL, action, f'evox2-gufo@{rank}.service']
    try:
        invoke(cluster.ssh(c, command) if rank else command, timeout=10)
        return True
    except (OSError, RuntimeError):
        return False


def check_management_connection(c):
    try:
        invoke(cluster.ssh(c, ['true']), timeout=10)
    except (OSError, RuntimeError):
        raise ValueError(f'Rank 1 is unreachable over SSH at {c["peer_lan"]}. '
                         'Check that it has finished booting and is connected to the LAN/Wi-Fi. '
                         'No inference services were changed.') from None


def check_rank1_safety_admission(c):
    """Run rank 1's ordinary safety-gate check before startup preflight."""
    scripts = c['peer_root'] + '/scripts'
    code = (f'import sys; sys.path.insert(0, {scripts!r}); '
            'import dashboard; dashboard.require_no_incomplete_safety_gates()')
    try:
        invoke(cluster.ssh(c, ['python3', '-c', code]), timeout=12)
    except (OSError, RuntimeError):
        raise RuntimeError('Rank 1 safety admission failed or could not be verified. '
                           'Review rank 1 recovery gates and SSH, then retry Start both. '
                           'This check made no service or driver changes.') from None


def verify_host_inputs(c, rank, emit):
    """Reuse model hashes only while their metadata is valid; always hash artifacts."""
    for reuse in (True, False):
        command = ['timeout', '--kill-after=5s', '1200s', '/usr/bin/python3',
                   str(ROOT / 'scripts/preflight.py') if rank == 0 else
                   c['peer_root'] + '/scripts/preflight.py', '--local', '--inputs-only']
        if reuse:
            command.append('--reuse-model-verification')
        if rank:
            command = cluster.ssh(c, command)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=1215)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(f'Rank {rank} input verification unavailable or timed out. '
                               'No services or drivers were changed.') from None
        if result.returncode == 0:
            return result.stdout
        if reuse and result.returncode == 3:
            emit(f'Rank {rank}: model checksum receipt is stale, missing or changed; '
                 'checking full model hashes (about 192 GB, limit 20 minutes)…')
            continue
        # Never retry SSH/configuration/artifact errors or expose raw stderr.
        raise RuntimeError(f'Rank {rank} input verification failed (exit {result.returncode}). '
                           'Check model/artifact/configuration integrity and SSH. '
                           'No services or drivers were changed.')


def refresh_preflight(c, emit):
    emit('Checking both model checksum receipts and artifact hashes; full model reads are needed only for stale or changed checksum evidence…')
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(verify_host_inputs, c, rank, emit) for rank in (0, 1)]
        started = time.monotonic()
        while not all(job.done() for job in jobs):
            time.sleep(3)
            elapsed = int(time.monotonic() - started)
            done = [str(rank) for rank, job in enumerate(jobs) if job.done() and job.exception() is None]
            emit(f'Checking model files · {elapsed}s elapsed · '
                 + ('finished on rank ' + ', '.join(done) if done else 'both hosts checking')
                 + '. Startup continues automatically; 20-minute safety limit per host.')
        results = [json.loads(job.result()) for job in jobs]
    for key in ('files', 'binaries', 'settings', 'control_token_sha256', 'image_id'):
        if results[0][key] != results[1][key]:
            raise ValueError(f'Paired input verification mismatch: {key}. No services were changed.')
    cluster.save(ROOT / 'results/paired-preflight.json', {'time': time.time(),
        'status': 'inputs_verified', 'management_interface': cluster.route(c['peer_lan']),
        'rank0': results[0], 'rank1': results[1], 'rails_checked': False, 'inference_qualified': False})


def check_start(c, emit=lambda message: None):
    # A rank 0 gate check cannot admit rank 1. Check the peer before recovery
    # prerequisites or model hashing so startup cannot mutate a rejected rank.
    check_rank1_safety_admission(c)
    # Check current cable training before expensive checksum work, on BOTH hosts.
    peer = remote_probe(c)
    require_full_speed([(0, dashboard_probe.usb4_links()), (1, peer.get('links', []))])
    if not peer['settings'] or any(c[k] != v for k, v in peer['settings'].items()):
        raise ValueError('Host settings differ. Restore matching configurations and run preflight.py.')
    check_recovery_inputs(c)
    # Refresh even recent paired receipts: namespace/chunk/artifact changes are
    # routine. Per-host model checksum receipts avoid another full weight scan.
    refresh_preflight(c, emit)
    # Verify the existing input receipt now; fresh physical rails are checked
    # only after bilateral driver preparation, before service startup.
    spec = importlib.util.spec_from_file_location('pair_terminal', ROOT / 'scripts/pair-terminal.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_preflight(c, require_rails=False)


def require_full_speed(host_links):
    problems = []
    for rank, links in host_links:
        status = dashboard_probe.usb4_status(links)
        if status['full_speed']:
            continue
        details = []
        for cable in status['cables']:
            rx = cable['rx_gbps']
            tx = cable['tx_gbps']
            details.append(f'{cable["device"]}: RX {rx:g} / TX {tx:g} Gb/s'
                           if rx is not None and tx is not None else
                           f'{cable["device"]}: speed unavailable')
        problems.append(f'Rank {rank}: ' + (', '.join(details) or 'no cable links detected'))
    if problems:
        raise ValueError('USB4 requires two cables at 40 Gb/s each in both directions on both hosts. '
                         + '; '.join(problems) + '. Stop both before reconnecting cables, then unplug '
                         'and reconnect each cable at this computer, one at a time, in the same ports. '
                         'Wait for both hosts to show 40 Gb/s, then use Recover USB4. '
                         'No driver or service changes were made by this operation.')


def recovery_module():
    spec = importlib.util.spec_from_file_location('prepare_rdma_pair', ROOT / 'scripts/prepare-rdma-pair.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_recovery_inputs(c):
    """Read-only recovery prerequisites; run before any stop or driver command."""
    preparation = recovery_module()
    source = (ROOT / 'scripts/prepare-rdma-pair.py').read_text()
    try:
        statuses = preparation.read_pair(cluster, ROOT, c, source)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError('Could not verify both recovery bundles over SSH. '
                         'No driver or service changes were made.') from None
    require_full_speed([(row['rank'], row['physical_links']) for row in statuses])
    preparation.validate_recovery_pair(statuses, c, require_stopped=False)


def ensure_transport(c, emit):
    """A stopped service pair needs fresh bilateral transport, even with ACTIVE rails."""
    require_no_incomplete_safety_gates()
    preparation = recovery_module()
    source = (ROOT / 'scripts/prepare-rdma-pair.py').read_text()
    emit('Preparing fresh USB4 transport before paired startup…')
    try:
        statuses = preparation.two_phase_recovery(
            cluster, ROOT, c, source,
            execute=lambda command: invoke(command, timeout=150), emit=emit)
        require_full_speed([(row['rank'], row['physical_links']) for row in statuses])
        if not preparation.both_ready(statuses):
            raise RuntimeError('Four active rails were not verified on both hosts.')
    except preparation.RecoveryFailure as error:
        raise RuntimeError(f'USB4 recovery stopped: {error} Inference remains stopped; '
                           'inspect both host states before retrying.') from None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise RuntimeError('USB4 recovery did not verify a ready pair. Inference remains stopped; '
                           'check the cable indicators and host service states before retrying. '
                           'A timed-out driver command may require host recovery.') from None


def paired_recovery(c, emit):
    with paired_operation_lock():
        require_no_incomplete_safety_gates()
        return _paired_recovery_locked(c, emit)


def _paired_recovery_locked(c, emit):
    require_no_incomplete_safety_gates()
    cluster.route(c['peer_lan'])
    check_management_connection(c)
    emit('Checking both cables, pinned recovery bundles and password-free grants…')
    check_recovery_inputs(c)
    # Stop checks BOTH service grants and attempts both stops. A failed stop
    # must never be followed by driver unload.
    paired_control(c, 'stop', emit)
    ensure_transport(c, emit)
    emit('USB4 ready: two 40 Gb/s cables and four active rails on both hosts. '
         'Inference remains stopped. Press Start both.')


NHI_ACTIONS = ('nhi-unload', 'nhi-reset', 'nhi-load')
NHI_LABELS = {'nhi-unload': 'Unloading the thunderbolt module',
              'nhi-reset': 'ACPI-resetting the USB4 controllers',
              'nhi-load': 'Reloading the thunderbolt module'}


def recovery_helper(rank):
    return f'/usr/local/lib/evox2-recovery-rank{rank}/scripts/recovery-helper.py'


def installed_manifest(c, rank):
    """Read the installed bundle manifest so a stale install is refused early."""
    command = ['cat', f'/usr/local/lib/evox2-recovery-rank{rank}/manifest.json']
    try:
        manifest = json.loads(invoke(cluster.ssh(c, command) if rank else command, timeout=15))
    except (OSError, TypeError, ValueError, RuntimeError):
        raise ValueError(f'Rank {rank} has no readable pinned recovery manifest. '
                         'Reinstall the pinned recovery bundle on both hosts; '
                         'no service or driver changes were made.') from None
    files = manifest.get('files') if isinstance(manifest, dict) else None
    policy = manifest.get('policy') if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not isinstance(policy, str):
        raise ValueError(f'Rank {rank} has an unreadable pinned recovery manifest. '
                         'Reinstall the pinned recovery bundle on both hosts; '
                         'no service or driver changes were made.')
    return files, policy


def check_nhi_grants(c):
    """Read-only check for the installed bundle and the three exact NHI grants."""
    for rank in (0, 1):
        files, policy = installed_manifest(c, rank)
        if 'scripts/nhi-reset.sh' not in files or any(
                action not in policy for action in NHI_ACTIONS):
            raise ValueError(f'Rank {rank} has the older recovery bundle without the USB4 NHI '
                             'actions. Reinstall the pinned recovery bundle on both hosts; '
                             'no service or driver changes were made.')
        for action in NHI_ACTIONS:
            command = ['sudo', '-n', '-l', recovery_helper(rank), action]
            try:
                invoke(cluster.ssh(c, command) if rank else command, timeout=15)
            except (OSError, RuntimeError):
                raise ValueError(f'Rank {rank} is missing the exact password-free NHI reset grant. '
                                 'Reinstall the pinned recovery bundle on both hosts; '
                                 'no service or driver changes were made.') from None


def physical_links(c):
    return [(0, dashboard_probe.usb4_links()), (1, remote_probe(c).get('links', []))]


def paired_nhi_reset(c, emit):
    """ACPI-reset both hosts' USB4 NHI functions; inference stays stopped."""
    with paired_operation_lock():
        return _paired_nhi_reset_locked(c, emit)


def _paired_nhi_reset_locked(c, emit):
    require_no_incomplete_safety_gates()
    cluster.route(c['peer_lan'])
    emit('Checking the connection to rank 1…')
    check_management_connection(c)
    emit('Checking both cables, pinned recovery bundles and password-free grants…')
    check_recovery_inputs(c)
    check_nhi_grants(c)
    states = [service_state(c, rank) for rank in (0, 1)]
    if any(state not in ('inactive', 'failed') for state in states):
        emit('Stopping paired inference before the USB4 reset…')
        paired_control(c, 'stop', emit)
        states = [service_state(c, rank) for rank in (0, 1)]
        if any(state not in ('inactive', 'failed') for state in states):
            raise RuntimeError('Paired stop did not complete; the USB4 reset was not started. '
                               'No driver changes were made.')
    # Both hosts unload before either resets, and both reset before either loads.
    for phase in NHI_ACTIONS:
        for rank in (1, 0):
            emit(f'{NHI_LABELS[phase]} on rank {rank}…')
            command = ['sudo', '-n', recovery_helper(rank), phase]
            try:
                invoke(cluster.ssh(c, command) if rank else command, timeout=240)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                raise RuntimeError(f'USB4 NHI reset stopped at {phase} on rank {rank}. '
                                   'Inference remains stopped and no later phase was attempted. '
                                   'Check the cable indicators and host power, then use Restart both; '
                                   'a host whose controller did not return needs a reboot. '
                                   'Do not retry the reset automatically.') from None
    emit('Waiting for both USB4 links to re-train on both hosts…')
    deadline = time.monotonic() + 90
    while True:
        try:
            require_full_speed(physical_links(c))
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise RuntimeError('The USB4 NHI reset finished but both cables did not report '
                                   '40 Gb/s on both hosts within 90 seconds. Inference remains '
                                   'stopped. Check the cable indicators, then use Restart both; '
                                   'do not retry the reset automatically.') from None
            time.sleep(3)
    emit('USB4 NHI reset complete: both hosts reset their USB4 controllers and both cables '
         'train at 40 Gb/s. Inference remains stopped. Press Start both.')


def wait_for_readiness(c, emit, timeout=240):
    started = time.monotonic()
    while True:
        states = [service_state(c, rank) for rank in (0, 1)]
        for rank, state in enumerate(states):
            if state not in ('active', 'activating', 'reloading'):
                raise RuntimeError(f'Rank {rank} service became {state} during model startup.')
        try:
            ready = ready_payload(api_get(c, '/ready'))
            if ready and states == ['active', 'active']:
                require_current_transport(c)
                return
        except (OSError, ValueError):
            pass
        elapsed = int(time.monotonic() - started)
        if elapsed >= timeout:
            raise RuntimeError(f'Model API did not become ready within {timeout} seconds.')
        emit(f'Loading model · {elapsed}s elapsed · waiting for API readiness (limit {timeout}s)…')
        time.sleep(3)


def paired_control(c, action, emit, *, on_start_failure=None):
    if action not in ('start', 'stop', 'restart'):
        raise ValueError('Unknown paired action')
    already_locked = getattr(_PAIR_LOCK, 'held', False)
    with paired_operation_lock(stop_only=(action == 'stop')):
        if action in ('start', 'restart'):
            require_no_incomplete_safety_gates()
        result = _paired_control_locked(c, action, emit, on_start_failure=on_start_failure)
    # Bridge repair and its gated model-discovery route run only after the pair
    # lock is released. A bridge-only failure must never enter Gufo rollback.
    if action in ('start', 'restart') and not already_locked:
        finish_pi_bridge_start(c, emit)
    return result


def finish_pi_bridge_start(c, emit):
    try:
        ensure_pi_bridge(c)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        message = 'Models are running; Pi bridge unavailable. Check the EVO-X2 bridge service.'
        emit(message)
        raise RuntimeError(message) from error
    emit('Models are running; Pi bridge is ready.')


def _paired_control_locked(c, action, emit, *, on_start_failure=None):
    if action not in ('start', 'stop', 'restart'):
        raise ValueError('Unknown paired action')
    if action != 'stop':
        require_no_incomplete_safety_gates()
    cluster.route(c['peer_lan'])
    emit('Checking the connection to rank 1…')
    try:
        check_management_connection(c)
    except ValueError:
        if action != 'stop':
            raise
        emit('Rank 1 is unreachable; attempting bounded Stop on both ranks anyway…')
    if action == 'start':
        states = [service_state(c, rank) for rank in (0, 1)]
        if states == ['active', 'active']:
            try:
                ready = ready_payload(api_get(c, '/ready'))
            except (OSError, ValueError):
                ready = False
            if ready:
                require_current_transport(c)
                emit('Cluster already ready. Both services are running and the model API is ready.')
                return
            raise ValueError('Both services are active but the model API is not ready. '
                             'Wait for loading or use Restart both; no services or drivers were changed.')
        if any(state not in ('inactive', 'failed') for state in states):
            raise ValueError('Paired start requires both services stopped, or both active with the API ready. '
                             'Use Stop both or Restart both to resolve a partial/transitioning pair. '
                             'No services or drivers were changed.')
    required = ('stop',) if action == 'stop' else ('start', 'stop', 'reset-failed')
    if action != 'stop':
        for rank in (0, 1):
            if not all(allowed(c, rank, item) for item in required):
                # A connection can disappear between the reachability probe and
                # sudo -l; do not mislabel that as missing authorization either.
                if rank == 1:
                    check_management_connection(c)
                raise ValueError(f'Rank {rank} service-control permission check failed. '
                                 'Use the already-approved one-time service setup if its grants are missing. '
                                 'No inference services were changed.')
    if action != 'stop':
        emit('Checking both hosts and recent paired preflight…')
        check_start(c, emit)
    if action in ('stop', 'restart'):
        errors = []
        for rank in (0, 1):
            emit(f'Stopping rank {rank}…')
            try:
                invoke(service_command(c, rank, 'stop'), timeout=45)
            except (OSError, RuntimeError) as error:
                errors.append(f'Rank {rank}: {error}')
        if errors:
            raise RuntimeError('Paired stop incomplete. ' + ' '.join(errors))
    if action in ('start', 'restart'):
        # All input/grant/checksum checks above are read-only. Fresh transport
        # preparation rechecks both stopped states and proves bilateral removal.
        ensure_transport(c, emit)
        try:
            # Matches pair-terminal.py: rank1 can wait for the leader while rank0 starts.
            for rank in (1, 0):
                emit(f'Starting rank {rank}…')
                reset_failed_if_needed(c, rank)
                invoke(service_command(c, rank, 'start'), timeout=45)
            wait_for_readiness(c, emit)
        except (OSError, RuntimeError) as error:
            failure = str(error) if isinstance(error, RuntimeError) else 'Host command unavailable.'
            if on_start_failure is not None:
                try:
                    on_start_failure(error)
                except BaseException as capture_error:
                    error.add_note(f'Start-failure capture failed: {capture_error}')
            emit('Start incomplete; stopping both ranks…')
            cleanup = []
            for rank in (0, 1):
                try:
                    invoke(service_command(c, rank, 'stop'), timeout=45)
                except (OSError, RuntimeError):
                    cleanup.append(str(rank))
            receipt = {'attempted': True, 'completed': not cleanup, 'failed_ranks': cleanup}
            message = (f'{failure} Rollback stop also failed on rank ' + ', '.join(cleanup)
                       if cleanup else f'{failure} Start failed; both ranks were stopped.')
            wrapped = RuntimeError(message)
            wrapped.perf_cleanup_attempted = True
            wrapped.paired_cleanup = receipt
            raise wrapped from error
    emit('Both services stopped.' if action == 'stop' else 'Cluster ready. Both services are running and the model API is ready.')


class State:
    def __init__(self, c):
        self.c = c
        self.lock = threading.Lock()
        self.control_lock = threading.Lock()
        self.snapshot = {'time': 0, 'hosts': [], 'inference': {}, 'bridge': {}, 'history': []}
        self.snapshot_cycle_started_monotonic = 0.0
        self.readiness_not_before_monotonic = 0.0
        self.operation = {'running': False, 'message': 'No control operation requested.'}
        self.sessions = {}
        self.failures = {}
        self.stop = threading.Event()

    def collect(self):
        previous = {}
        history = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            while not self.stop.is_set():
                cycle_started = time.monotonic()
                cycle_started_wall = time.time()
                try:
                    self.c = cluster.config(require_peer=True)
                except (OSError, ValueError) as error:
                    with self.lock:
                        self.snapshot = {'time': cycle_started_wall, 'hosts': [], 'inference': {}, 'bridge': {},
                                         'history': [], 'error': 'Current cluster configuration is invalid.'}
                        self.snapshot_cycle_started_monotonic = cycle_started
                    self.stop.wait(3)
                    continue
                with self.lock:
                    operation_running = self.operation.get('running') is True
                bridge_job = None if operation_running else pool.submit(bridge, self.c)
                jobs = [pool.submit(dashboard_probe.probe, ROOT, 0), pool.submit(remote_probe, self.c),
                        pool.submit(inference, self.c), bridge_job]
                hosts = []
                for rank in (0, 1):
                    try:
                        host = jobs[rank].result()
                        old = previous.get(rank)
                        if old:
                            total = host['cpu']['total_ticks'] - old['cpu']['total_ticks']
                            idle = host['cpu']['idle_ticks'] - old['cpu']['idle_ticks']
                            host['cpu']['busy_percent'] = max(0, min(100, 100 * (1 - idle / total))) if total > 0 else None
                        previous[rank] = host
                        hosts.append(host)
                    except Exception:
                        hosts.append({'rank': rank, 'time': time.time(), 'error': 'Host probe unavailable; check LAN/SSH and journal access.'})
                data = apply_transport_health(jobs[2].result(), hosts)
                bridge_data = jobs[3].result() if jobs[3] is not None else {'reachable': False, 'port': 8731}
                metrics = data.get('metrics', {})
                row = {'time': time.time(), 'prefill_tps': metrics.get('llamacpp:prompt_tokens_seconds'),
                       'decode_tps': metrics.get('llamacpp:predicted_tokens_seconds')}
                history = (history + [row])[-120:]
                with self.lock:
                    self.snapshot = {'time': cycle_started_wall, 'hosts': hosts, 'inference': data,
                                     'bridge': bridge_data, 'history': history,
                                     'configuration': {k: self.c[k] for k in ('context', 'sessions', 'transport',
                                         'cables', 'prefill_chunk', 'speculative', 'model', 'rank0_lan', 'peer_lan')}}
                    self.snapshot_cycle_started_monotonic = cycle_started
                self.stop.wait(3)

    def control(self, action):
        if not self.control_lock.acquire(blocking=False):
            return False
        def worker():
            outcome = 'success'
            def emit(message):
                with self.lock:
                    self.operation = {'running': True, 'action': action, 'message': message,
                                      'time': time.time(), 'started_at': started}
            try:
                with paired_operation_lock(stop_only=(action == 'stop')):
                    current = cluster.config(require_peer=True)
                    self.c = current
                    if action == 'recover':
                        paired_recovery(current, emit)
                    elif action == 'nhi-reset':
                        paired_nhi_reset(current, emit)
                    else:
                        paired_control(current, action, emit)
                if action in ('start', 'restart'):
                    finish_pi_bridge_start(current, emit)
            except Exception as error:
                outcome = 'failed'
                # Known checks can contain paths, but never raw subprocess stderr or secrets.
                emit(str(error) if isinstance(error, (ValueError, RuntimeError)) else
                     'Operation failed. Check the rank0 terminal and paired preflight.')
            finally:
                with self.lock:
                    self.operation['running'] = False
                    self.operation['outcome'] = outcome
                    self.readiness_not_before_monotonic = time.monotonic()
                self.control_lock.release()
        started = time.time()
        with self.lock:
            self.operation = {'running': True, 'action': action, 'message': 'Queued…',
                              'time': started, 'started_at': started}
        threading.Thread(target=worker, daemon=True).start()
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = 'EVO-X2'

    def log_message(self, *args):
        pass  # Tokens, cookies and user input never enter request logs.

    def reply(self, code, value, content_type='application/json', extra=None):
        body = value.encode() if isinstance(value, str) else json.dumps(value, allow_nan=False).encode()
        self.send_response(code)
        for key, value in {'Content-Type': content_type, 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Content-Length': str(len(body)), **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        try:
            jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
            token = jar['evox2_session'].value
            with self.server.state.lock:
                value = self.server.state.sessions.get(token)
            return value if value and value['expires'] > time.time() else None
        except (KeyError, cookies.CookieError):
            return None

    def valid_host(self):
        return self.headers.get('Host') in self.server.allowed_hosts

    def begin_session(self):
        state = self.server.state
        token = secrets.token_urlsafe(32)
        with state.lock:
            state.sessions = {k: v for k, v in state.sessions.items() if v['expires'] > time.time()}
            if len(state.sessions) >= 128:
                del state.sessions[min(state.sessions, key=lambda k: state.sessions[k]['expires'])]
            state.sessions[token] = {'expires': time.time() + 8 * 3600, 'csrf': secrets.token_urlsafe(32)}
        return self.reply(200, {'ok': True}, extra={
            'Set-Cookie': f'evox2_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Invalid host'})
        if self.path == '/api/bridge-readiness':
            try:
                loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                return self.reply(403, {'error': 'Local access only'})
            paired_idle = pair_control_available()
            with self.server.state.lock:
                readiness = composite_readiness(self.server.state.snapshot,
                                                 self.server.state.operation,
                                                 cycle_started=self.server.state.snapshot_cycle_started_monotonic,
                                                 not_before=self.server.state.readiness_not_before_monotonic,
                                                 paired_idle=paired_idle)
            return self.reply(200, readiness)
        if self.path in ('/', '/app.js', '/style.css'):
            name, kind = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript'),
                          '/style.css': ('style.css', 'text/css')}[self.path]
            return self.reply(200, (ROOT / 'dashboard' / name).read_text(), kind)
        session = self.session()
        if not session:
            return self.reply(401, {'error': 'Sign in required'})
        if self.path == '/api/state':
            paired_idle = pair_control_available()
            with self.server.state.lock:
                data = {**self.server.state.snapshot, 'operation': self.server.state.operation.copy(),
                        'bridge_readiness': composite_readiness(self.server.state.snapshot,
                                                                self.server.state.operation,
                                                                cycle_started=self.server.state.snapshot_cycle_started_monotonic,
                                                                not_before=self.server.state.readiness_not_before_monotonic,
                                                                paired_idle=paired_idle),
                        'csrf': session['csrf'], 'access': 'local' if self.server.local_access else 'token'}
            return self.reply(200, data)
        self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or origin not in self.server.allowed_origins
                or origin != 'http://' + self.headers.get('Host', '')
                or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin')):
            return self.reply(403, {'error': 'Invalid origin'})
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 4096 or self.headers.get('Content-Type') != 'application/json':
                return self.reply(400, {'error': 'Expected small JSON request'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, TypeError):
            return self.reply(400, {'error': 'Invalid JSON'})
        state = self.server.state
        if self.path == '/api/session':
            if not self.server.local_access:
                return self.reply(401, {'error': 'Sign in required', 'requires_token': True})
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.reply(403, {'error': 'Local access only'})
            if self.session():
                return self.reply(200, {'ok': True})
            return self.begin_session()
        if self.path == '/api/login':
            if self.server.local_access:
                return self.reply(404, {'error': 'Use the local dashboard session'})
            client = self.client_address[0]
            with state.lock:
                failures = [t for t in state.failures.get(client, []) if t > time.time() - 60]
                if len(failures) >= 5:
                    return self.reply(429, {'error': 'Wait one minute before trying again.'})
                if not isinstance(data.get('token'), str) or not secrets.compare_digest(data['token'].encode(), self.server.token.encode()):
                    state.failures[client] = failures + [time.time()]
                    return self.reply(401, {'error': 'Incorrect dashboard token'})
            return self.begin_session()
        session = self.session()
        if not session or not secrets.compare_digest(self.headers.get('X-CSRF-Token', '').encode(), session['csrf'].encode()):
            return self.reply(403, {'error': 'Session expired or invalid request. Sign in again.'})
        if self.path == '/api/logout':
            with state.lock:
                state.sessions = {k: v for k, v in state.sessions.items() if v is not session}
            return self.reply(200, {'ok': True}, extra={'Set-Cookie': 'evox2_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        if self.path != '/api/control' or data.get('action') not in ('start', 'stop', 'restart', 'recover', 'nhi-reset'):
            return self.reply(400, {'error': 'Unknown paired action'})
        if data.get('confirm') != 'both':
            return self.reply(400, {'error': 'Confirm action for both hosts'})
        accepted = state.control(data['action'])
        self.reply(202 if accepted else 409, {'ok': accepted, 'message': 'Queued' if accepted else 'Another paired operation is running'})


def load_token():
    cluster.PRIVATE.mkdir(mode=0o700, exist_ok=True)
    try:
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32) + '\n')
    info = TOKEN_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('dashboard-token must be a normal file owned by your account with mode 0600')
    token = TOKEN_FILE.read_text().strip()
    if len(token) < 32:
        raise ValueError('dashboard-token must contain at least 32 characters')
    return token


def load_access_mode(path=None):
    path = path or ROOT / 'config/dashboard.json'
    if not path.exists():
        return 'token'
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != {'access'} or data['access'] not in ('local', 'token'):
        raise ValueError('dashboard.json must select access "local" or "token"')
    return data['access']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your normal rank0 account')
    if not 1024 <= args.port <= 65535 or args.port in (8080, 8731, 18515, 18516):
        parser.error('Choose a separate unprivileged dashboard port')
    c = cluster.config(require_peer=True)
    cluster.route(c['peer_lan'])
    local_access = load_access_mode() == 'local'
    token = None if local_access else load_token()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.token = token
    server.local_access = local_access
    server.state = State(c)
    server.allowed_hosts = {f'127.0.0.1:{args.port}', f'localhost:{args.port}'}
    server.allowed_origins = {'http://' + h for h in server.allowed_hosts}
    threading.Thread(target=server.state.collect, daemon=True).start()
    access_message = ('Local access opens automatically; no token entry required.' if local_access else
                      'Sign in using .private/dashboard-token.')
    print(f'EVO-X2 dashboard: http://127.0.0.1:{args.port}\n{access_message}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.state.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
