#!/usr/bin/env python3
"""Recovery permission and failure-path tests; host mutations are mocked."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import recovery_authorize as auth


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load('recovery_helper_test', 'recovery-helper.py')
prepare = load('prepare_rdma_test', 'prepare-rdma-pair.py')


class RecoveryTests(unittest.TestCase):
    def test_grants_only_fixed_root_owned_operations_and_peer_reboot(self):
        for rank in (0, 1):
            policy = auth.policy('ercan', rank)
            self.assertNotIn('*', policy)
            self.assertNotIn('/home/', policy)
            self.assertNotIn('/usr/bin/python', policy)
            self.assertNotIn('/usr/bin/bash', policy)
            self.assertIn('NOSETENV:', policy)
            self.assertEqual(' reboot' in policy, rank == 1)
            self.assertNotIn(f'rank{1-rank}/', policy)
        for user in ('root', 'bad user', 'x\nroot ALL=ALL'):
            with self.assertRaises(ValueError):
                auth.policy(user, 1)
        with self.assertRaises(ValueError):
            auth.policy('ercan', True)

    def test_default_review_cannot_install(self):
        with patch.object(auth.sys, 'argv', ['recovery_authorize.py']), \
                patch('cluster.config', return_value={'peer_user': 'ercan'}), \
                patch.object(auth, 'local') as local, patch.object(auth, 'run') as run, patch('builtins.print'):
            auth.main()
        local.assert_not_called()
        run.assert_not_called()

    def test_tampered_module_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'builds/donnerkeule-test/kernel/thunderbolt_ibverbs.ko'
            (root / name).parent.mkdir(parents=True)
            (root / name).write_bytes(b'changed')
            (root / 'config').mkdir()
            (root / 'config/builds.lock.json').write_text(json.dumps({'modules': {name: '0' * 64}}))
            with patch.object(auth, 'run') as run, self.assertRaisesRegex(ValueError, 'pinned'):
                auth.source_files(root, 'test')
            run.assert_not_called()

    def test_manifest_rejects_traversal_and_changed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'evox2-recovery-rank1'
            root.mkdir()
            (root / 'code').write_text('changed')
            manifest = {'rank': 1, 'files': {'code': hashlib.sha256(b'original').hexdigest()}}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with patch.object(helper, 'trusted'), self.assertRaisesRegex(ValueError, 'changed'):
                helper.verify(root)
            manifest['files'] = {'../escape': '0' * 64}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with patch.object(helper, 'trusted'), self.assertRaisesRegex(ValueError, 'path'):
                helper.verify(root)

    def test_protected_path_rejects_symlink_and_writable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file'
            path.write_text('x')
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                helper.trusted(path)
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                helper.trusted(link)

    def test_running_inference_blocks_recovery(self):
        with patch.object(helper, 'run', return_value=subprocess.CompletedProcess([], 0, 'active\n')):
            with self.assertRaisesRegex(ValueError, 'Stop paired'):
                helper.require_stopped(1)

    def test_reboot_only_peer_and_normal_shutdown_transaction(self):
        manifest = {'rank': 1, 'kernel': helper.os.uname().release}
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'require_stopped'), patch.object(helper, 'run') as run:
            helper.dispatch('reboot', manifest)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'start', 'reboot.target',
                                                    '--job-mode=replace-irreversibly', '--no-block'])
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, 'Only rank 1'):
                helper.dispatch('reboot', {**manifest, 'rank': 0})
            run.assert_not_called()

    def test_different_kernel_and_unknown_action_cannot_mutate(self):
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper, 'run') as run:
            for action in ('prepare', 'reboot', 'down'):
                with self.assertRaisesRegex(ValueError, 'kernel'):
                    helper.dispatch(action, {'rank': 1, 'kernel': 'wrong'})
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                helper.dispatch('shell', {'rank': 1})
            run.assert_not_called()

    def test_installed_recovery_settings_must_match(self):
        status = {'rank': 1, 'settings': {'transport': 'dmabuf'}}
        with patch.object(prepare.Path, 'exists', return_value=True), \
                patch.object(prepare, 'output', return_value=json.dumps(status)), \
                patch.object(prepare.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'configuration'):
                prepare.recovery_status(1, {'transport': 'host'})
            run.assert_not_called()

    def test_installed_recovery_checks_both_exact_noninteractive_grants(self):
        status = {'rank': 1, 'settings': {'transport': 'dmabuf'}}
        for down_allowed, prepare_allowed in ((True, True), (False, True), (True, False)):
            with self.subTest(down=down_allowed, prepare=prepare_allowed), \
                    patch.object(prepare.Path, 'exists', return_value=True), \
                    patch.object(prepare, 'output', return_value=json.dumps(status)), \
                    patch.object(prepare.subprocess, 'run', side_effect=[
                        subprocess.CompletedProcess([], 0 if down_allowed else 1),
                        subprocess.CompletedProcess([], 0 if prepare_allowed else 1)]) as run:
                result = prepare.recovery_status(1, {'transport': 'dmabuf'})
                self.assertEqual(result['allowed'], down_allowed and prepare_allowed)
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    ['sudo', '-n', '-l', prepare.recovery_helper(1), action]
                    for action in ('down', 'prepare')])

    def test_revoke_refuses_an_unrelated_policy(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.Path, 'exists', return_value=True), patch.object(helper, 'trusted'), \
                patch.object(helper.Path, 'read_text', return_value='different'), \
                patch.object(helper.Path, 'unlink') as unlink, patch.object(helper, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'different sudo'):
                helper.dispatch('revoke', {'rank': 1, 'policy': 'expected'})
            unlink.assert_not_called()
            run.assert_not_called()

    def test_nhi_grants_are_exact_fixed_actions_for_both_ranks(self):
        for rank in (0, 1):
            policy = auth.policy('ercan', rank)
            for action in ('nhi-unload', 'nhi-reset', 'nhi-load'):
                self.assertIn(f'/usr/local/lib/evox2-recovery-rank{rank}/scripts/'
                              f'recovery-helper.py {action}', policy)
            self.assertEqual(policy.count('nhi-reset'), 1)
        self.assertEqual(set(auth.ACTIONS), {'prepare', 'down', 'counters', 'revoke',
                                             'nhi-unload', 'nhi-reset', 'nhi-load'})

    def test_install_sources_include_the_pinned_nhi_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = 'builds/donnerkeule-test/kernel/thunderbolt_ibverbs.ko'
            (root / module).parent.mkdir(parents=True)
            (root / module).write_bytes(b'x')
            (root / 'config').mkdir()
            (root / 'config/builds.lock.json').write_text(
                json.dumps({'modules': {module: hashlib.sha256(b'x').hexdigest()}}))
            with patch.object(auth, 'run', return_value=subprocess.CompletedProcess([], 0, 'test ')):
                names = auth.source_files(root, 'test')
        self.assertIn('scripts/nhi-reset.sh', names)
        # cluster.py imports this at top level, so prepare fails without it.
        self.assertIn('scripts/dashboard_probe.py', names)

    def test_nhi_phases_require_stop_kernel_and_an_unloaded_platform(self):
        manifest = {'rank': 1, 'kernel': helper.os.uname().release,
                    'settings': {'transport': 'dmabuf'}}
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'require_stopped') as stopped, \
                patch.object(helper, 'trusted'), \
                patch.object(helper.Path, 'exists', return_value=True), \
                patch.object(helper, 'run') as run:
            for action in ('nhi-reset', 'nhi-load'):
                with self.assertRaisesRegex(ValueError, 'nhi-unload'):
                    helper.dispatch(action, manifest)
            run.assert_not_called()
        stopped.assert_called()
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'require_stopped'), patch.object(helper, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'kernel'):
                helper.dispatch('nhi-reset', {**manifest, 'kernel': 'wrong'})
            run.assert_not_called()

    def test_nhi_unload_removes_the_pinned_driver_then_the_module(self):
        manifest = {'rank': 0, 'kernel': helper.os.uname().release,
                    'settings': {'transport': 'dmabuf'}}
        def exists(path):
            return path.name == 'driver-state-0'
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'require_stopped'), \
                patch.object(helper, 'trusted'), \
                patch.object(helper, 'run') as run, \
                patch.object(helper.Path, 'exists', exists):
            helper.dispatch('nhi-unload', manifest)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-2:], ['down', 0])
        self.assertEqual(calls[1][2], 'unload')
        self.assertEqual(calls[1][3], 0)

    def test_nhi_reset_and_load_run_only_the_pinned_runner(self):
        manifest = {'rank': 0, 'kernel': helper.os.uname().release,
                    'settings': {'transport': 'dmabuf'}}
        def absent(path):
            return False
        for action, phase, timeout in (('nhi-reset', 'reset', 180), ('nhi-load', 'load', 180)):
            with self.subTest(action=action), \
                    patch.object(helper.os, 'geteuid', return_value=0), \
                    patch.object(helper, 'require_stopped'), \
                    patch.object(helper, 'run') as run, \
                    patch.object(helper.Path, 'exists', absent):
                helper.dispatch(action, manifest)
                command = run.call_args.args[0]
                self.assertEqual(command[0], '/usr/bin/bash')
                self.assertTrue(str(command[1]).endswith('scripts/nhi-reset.sh'))
                self.assertEqual(command[2], phase)
                self.assertEqual(run.call_args.kwargs.get('timeout'), timeout)


if __name__ == '__main__':
    unittest.main()
