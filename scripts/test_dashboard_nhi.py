#!/usr/bin/env python3
"""Dashboard-triggered USB4 NHI reset; all host mutations are mocked."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import threading
from unittest.mock import patch
import unittest

import dashboard as app
import dashboard_probe as probe


def links(speed='20.0 Gb/s'):
    return [{'device': name, 'rx_speed': speed, 'tx_speed': speed,
             'rx_lanes': '2', 'tx_lanes': '2'} for name in ('0-2', '1-2')]


PHASES = ('nhi-unload', 'nhi-reset', 'nhi-load')


class NhiResetTests(unittest.TestCase):
    def setUp(self):
        self.c = {'peer_lan': '192.168.0.217', 'peer_user': 'ercan',
                  'peer_root': '/peer', 'transport': 'dmabuf', 'cables': 2}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def mock(obj, name, **kwargs):
            return self.stack.enter_context(patch.object(obj, name, **kwargs))
        mock(app.cluster, 'route')
        mock(app, 'check_management_connection')
        self.inputs = mock(app, 'check_recovery_inputs')
        self.grants = mock(app, 'check_nhi_grants')
        self.service = mock(app, 'service_state', return_value='inactive')
        self.stop = mock(app, 'paired_control')
        self.invoke = mock(app, 'invoke')
        self.speed = mock(app, 'require_full_speed')
        self.links = mock(app, 'physical_links', return_value=[(0, links()), (1, links())])
        self.messages = []
        self.preparation = app.recovery_module()

    def reset(self):
        app.paired_nhi_reset(self.c, self.messages.append)

    def commands(self):
        return [call.args[0] for call in self.invoke.call_args_list]

    def test_both_hosts_unload_then_reset_then_load_and_inference_never_starts(self):
        self.reset()
        commands = self.commands()
        self.assertEqual(len(commands), 6)
        for index, phase in enumerate(PHASES):
            peer, local = commands[index * 2], commands[index * 2 + 1]
            self.assertIn(f'evox2-recovery-rank1/scripts/recovery-helper.py {phase}', peer[-1])
            self.assertEqual(local, ['sudo', '-n',
                f'/usr/local/lib/evox2-recovery-rank0/scripts/recovery-helper.py', phase])
        self.stop.assert_not_called()
        self.assertNotIn('start', [part for command in commands for part in command])
        self.assertIn('Press Start both', self.messages[-1])
        self.assertIn('Inference remains stopped', self.messages[-1])

    def test_active_pair_is_stopped_first_and_never_restarted(self):
        self.service.side_effect = ['active', 'active', 'inactive', 'inactive']
        self.reset()
        self.stop.assert_called_once_with(self.c, 'stop', self.messages.append)
        self.assertEqual(self.service.call_count, 4)
        self.assertEqual(len(self.commands()), 6)
        self.assertNotIn('start', [part for command in self.commands() for part in command])

    def test_incomplete_stop_never_touches_a_controller(self):
        self.service.side_effect = ['active', 'active', 'active', 'inactive']
        with self.assertRaisesRegex(RuntimeError, 'reset was not started.*No driver changes'):
            self.reset()
        self.invoke.assert_not_called()

    def test_missing_grant_or_failed_input_check_never_touches_a_controller(self):
        for gate in (self.inputs, self.grants):
            with self.subTest(gate=gate):
                self.invoke.reset_mock()
                gate.side_effect = ValueError('missing exact grant')
                with self.assertRaisesRegex(ValueError, 'grant'):
                    self.reset()
                self.invoke.assert_not_called()
                gate.side_effect = None

    def test_failed_phase_aborts_without_later_phases_or_start(self):
        self.invoke.side_effect = [None, None, None, RuntimeError('phase failed')]
        with self.assertRaisesRegex(RuntimeError, 'nhi-reset on rank 0.*Do not retry'):
            self.reset()
        self.assertEqual(len(self.invoke.call_args_list), 4)
        self.assertNotIn('start', [part for command in self.commands() for part in command])
        self.assertEqual(self.grants.call_count, 1)

    def test_unverified_retrain_fails_closed_without_start(self):
        self.speed.side_effect = ValueError('USB4 requires two cables')
        with patch.object(app.time, 'monotonic', side_effect=[0, 91]), \
                patch.object(app.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'did not report.*Inference remains stopped'):
                self.reset()
        self.assertNotIn('start', [part for command in self.commands() for part in command])
        self.stop.assert_not_called()

    def test_state_control_routes_the_ui_action_to_the_reset(self):
        state = app.State(self.c)
        with patch.object(app, 'paired_nhi_reset') as reset, \
                patch.object(app.cluster, 'config', return_value=self.c), \
                patch.object(app, 'finish_pi_bridge_start'):
            self.assertTrue(state.control('nhi-reset'))
            for thread in threading.enumerate():
                if thread is not threading.current_thread():
                    thread.join(timeout=2)
        reset.assert_called_once()
        self.assertEqual(reset.call_args.args[0], self.c)

    def test_reset_uses_shared_control_lock(self):
        state = app.State(self.c)
        state.control_lock.acquire()
        try:
            self.assertFalse(state.control('nhi-reset'))
        finally:
            state.control_lock.release()
        self.invoke.assert_not_called()


def manifest(rank, actions=PHASES, runner=True):
    files = {'scripts/recovery-helper.py': 'a'}
    if runner:
        files['scripts/nhi-reset.sh'] = 'b'
    policy = ' '.join(f'recovery-helper.py {action}' for action in actions)
    return json.dumps({'rank': rank, 'files': files, 'policy': policy})


def installed(command, **kwargs):
    if any('manifest.json' in str(part) for part in command):
        return manifest(1 if any('rank1' in str(part) for part in command) else 0)
    return None


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.c = {'peer_lan': '192.168.0.217', 'peer_user': 'ercan'}

    def test_exact_grant_probe_reads_the_manifest_and_both_hosts_grants(self):
        with patch.object(app, 'invoke', side_effect=installed) as invoke:
            app.check_nhi_grants(self.c)
        commands = [call.args[0] for call in invoke.call_args_list]
        self.assertEqual(len(commands), 8)
        # Rank 0 runs locally; rank 1 goes through the pinned SSH wrapper.
        self.assertEqual([command[0] for command in commands],
                         ['cat', 'sudo', 'sudo', 'sudo', 'ssh', 'ssh', 'ssh', 'ssh'])
        for rank in (0, 1):
            for action in PHASES:
                local = ['sudo', '-n', '-l',
                         f'/usr/local/lib/evox2-recovery-rank{rank}/scripts/recovery-helper.py', action]
                self.assertTrue(any(command == local or command[-1] == ' '.join(local)
                                    for command in commands))

    def test_missing_grant_names_the_host_and_makes_no_change(self):
        with patch.object(app, 'invoke',
                          side_effect=[manifest(0), RuntimeError('denied')]):
            with self.assertRaisesRegex(ValueError, 'Rank 0.*NHI reset grant.*no service or driver changes'):
                app.check_nhi_grants(self.c)

    def test_stale_bundle_is_refused_before_any_grant_or_service_check(self):
        cases = {
            'older recovery bundle': manifest(0, runner=False),
            'older recovery bundle (actions)': manifest(0, actions=('prepare', 'down')),
        }
        for expected, payload in cases.items():
            with self.subTest(expected=expected), \
                    patch.object(app, 'invoke', side_effect=[payload]) as invoke:
                with self.assertRaisesRegex(ValueError, 'Rank 0.*older recovery bundle'):
                    app.check_nhi_grants(self.c)
                self.assertEqual(len(invoke.call_args_list), 1)

    def test_unreadable_manifest_is_refused_before_any_mutation(self):
        with patch.object(app, 'invoke', side_effect=['not json']) as invoke:
            with self.assertRaisesRegex(ValueError, 'Rank 0.*no readable pinned recovery manifest'):
                app.check_nhi_grants(self.c)
        self.assertEqual(len(invoke.call_args_list), 1)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.port = self.server.server_address[1]
        self.host = f'127.0.0.1:{self.port}'
        self.origin = 'http://' + self.host
        self.server.allowed_hosts = {self.host}
        self.server.allowed_origins = {self.origin}
        self.server.token = None
        self.server.local_access = True
        self.server.state = app.State({})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        data = json.dumps(body) if body is not None else None
        h = {'Host': self.host, 'Content-Type': 'application/json', 'Origin': self.origin, **(headers or {})}
        connection.request(method, path, data, {k: v for k, v in h.items() if v is not None})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def session(self):
        status, headers, body = self.request('POST', '/api/session', {})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        state = json.loads(self.request('GET', '/api/state', headers={'Cookie': cookie})[2])
        return cookie, state['csrf']

    def test_control_requires_csrf_and_paired_confirmation(self):
        cookie, csrf = self.session()
        with patch.object(self.server.state, 'control', return_value=True) as control:
            self.assertEqual(self.request('POST', '/api/control',
                {'action': 'nhi-reset', 'confirm': 'both'}, {'Cookie': cookie})[0], 403)
            self.assertEqual(self.request('POST', '/api/control',
                {'action': 'nhi-reset'}, {'Cookie': cookie, 'X-CSRF-Token': csrf})[0], 400)
            control.assert_not_called()
            self.assertEqual(self.request('POST', '/api/control',
                {'action': 'nhi-reset', 'confirm': 'both'},
                {'Cookie': cookie, 'X-CSRF-Token': csrf})[0], 202)
            control.assert_called_once_with('nhi-reset')

    def test_browser_exposes_the_reset_control_and_its_warning(self):
        index = (app.ROOT / 'dashboard/index.html').read_text()
        browser = (app.ROOT / 'dashboard/app.js').read_text()
        self.assertIn('data-action="nhi-reset"', index)
        self.assertIn("pendingAction==='nhi-reset'", browser)
        self.assertIn('fixed ~10 ms per exchange', browser)


if __name__ == '__main__':
    unittest.main()
