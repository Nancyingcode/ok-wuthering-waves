from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.ocr_observer import (BoundaryObserver, DiagnosticState, complete_recording,
                                  completion_status, observed_run)


class TestOcrObserver(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.state = DiagnosticState()
        self.state.update(generation=1)
        self.records = []
        self.usage = Mock(return_value={'private_bytes': 100, 'working_set_bytes': 20})
        self.classify = Mock(return_value={'bytes': {'private_committed': 100}})
        self.observer = self.make_observer()

    def emit(self, event, **fields):
        self.records.append({'event': event, **fields})

    def make_observer(self, **kwargs):
        return BoundaryObserver(self.state.snapshot, self.usage, self.classify, self.emit,
                                clock=lambda: self.now, **kwargs)

    def release(self, generation=1):
        self.observer.release(self.now, generation)
        self.observer.step()

    def assert_incomplete(self, observer):
        trace = SimpleNamespace(emit=self.emit)
        with self.assertRaises(RuntimeError), observed_run(trace, observer, lambda: 8):
            complete_recording(trace, observer, 8, self.now)
        self.assertFalse(any(row['event'] == 'completed' for row in self.records))
        terminal = self.records[-1]
        self.assertEqual(terminal['status'], 'incomplete')
        self.assertFalse(terminal['observer_health']['complete'])
        json.dumps(terminal)

    def test_creation_during_memory_query_cannot_set_baseline_or_target(self):
        def query():
            self.state.update(generation=2, creating=True)
            return {'private_bytes': 500 * 1024**2}

        self.usage.side_effect = query
        self.observer.growth_seen = True
        self.release()
        self.assertIsNone(self.observer.first_unloaded_private)
        self.assertEqual(self.observer.categories_taken, set())
        self.assertFalse(self.observer.target_captured.is_set())
        self.assertEqual(self.records[0]['invalid_reason'], 'epoch_changed')
        self.classify.assert_not_called()

    def test_aba_unloaded_state_is_rejected_by_epoch(self):
        def query():
            self.state.update(generation=2, creating=True)
            self.state.update(generation=1, creating=False)
            return {'private_bytes': 100}

        self.usage.side_effect = query
        self.release()
        self.assertIsNone(self.observer.first_unloaded_private)
        self.assertEqual(self.records[0]['invalid_reason'], 'epoch_changed')

    def test_valid_memory_but_invalid_classification_does_not_consume_classification_slot(self):
        self.classify.side_effect = lambda: (self.state.update(creating=True) or {'bytes': {}})
        self.release()
        self.assertTrue(self.records[0]['valid_for_unloaded'])
        self.assertFalse(self.records[1]['valid_for_unloaded'])
        self.assertEqual(self.observer.first_unloaded_private, 100)
        self.assertEqual(self.observer.categories_taken, set())
        self.state.update(creating=False)
        self.classify.side_effect = None
        self.now = 2
        self.observer.step()
        self.assertEqual(self.observer.categories_taken, {'first_unloaded'})

    def test_high_classification_race_does_not_trigger_early_stop(self):
        self.release()
        self.state.update(generation=2)
        self.usage.return_value = {'private_bytes': 200 * 1024**2}
        self.classify.side_effect = lambda: (self.state.update(generation=3, creating=True) or {})
        self.observer.growth_seen = True
        self.release(2)
        self.assertNotIn('high', self.observer.categories_taken)
        self.assertFalse(self.observer.target_captured.is_set())

    def test_next_generation_is_observed_but_not_the_target_releases_unloaded_tail(self):
        self.state.update(generation=2, loaded=True)
        self.release(1)
        self.assertEqual(self.records[0]['invalid_reason'], 'release_generation_mismatch')
        health = self.observer.health()
        self.assertEqual((health['observed'], health['invalid']), (1, 1))
        self.assertEqual(health['missing'], 3)

    def test_duplicate_boundary_keys_are_counted_once_and_all_due_targets_are_drained(self):
        self.observer.release(0, 1)
        self.release()
        self.now = 30
        health = self.observer.finish(30)
        self.assertEqual((health['expected'], health['observed'], health['missing']), (4, 4, 0))
        self.assertTrue(health['complete'])
        self.classify.assert_called_once()

    def test_short_cooldown_is_explicitly_incomplete(self):
        self.release()
        self.assert_incomplete(self.observer)
        self.assertEqual(self.records[-1]['observer_health']['missing_targets'], [[1, 2], [1, 10], [1, 30]])

    def test_worker_query_error_propagates_only_scalar_error_and_has_no_success_terminal(self):
        self.usage.side_effect = OSError('query failed')
        self.observer.release(0, 1)
        self.observer.start()
        self.observer.thread.join(2)
        self.assertFalse(self.observer.thread.is_alive())
        self.assert_incomplete(self.observer)
        self.assertEqual(self.records[-1]['observer_health']['errors'][0]['type'], 'OSError')

    def test_queue_overflow_cannot_complete_successfully(self):
        observer = self.make_observer(queue_limit=1)
        observer.release(0, 1)
        observer.release(0, 2)
        self.assert_incomplete(observer)
        self.assertEqual(self.records[-1]['observer_health']['dropped_events'], 1)

    def test_join_timeout_cannot_complete_successfully(self):
        self.observer.thread = SimpleNamespace(join=Mock(), is_alive=lambda: True)
        self.assert_incomplete(self.observer)
        self.assertEqual(self.records[-1]['observer_health']['errors'][0]['type'], 'JoinTimeout')

    def test_ordinary_non_diagnostic_run_has_no_new_tail_wait_requirement(self):
        observer = self.make_observer(diagnostics=False)
        observer.release(0, 1)
        health = observer.finish(0)
        self.assertTrue(health['complete'])
        self.assertEqual(health['expected'], 0)
        self.usage.assert_not_called()

    def test_legacy_diagnostic_health_remains_unknown_and_conflicting_terminals_fail(self):
        self.assertEqual(completion_status([{'event': 'completed'}])['status'], 'unknown')
        success = {'event': 'completed', 'diagnostic_schema_version': 2,
                   'status': 'success', 'observer_health': self.make_observer(diagnostics=False).finish(0)}
        self.assertEqual(completion_status([success])['status'], 'success')
        self.assertEqual(completion_status([success, {'event': 'incomplete'}])['status'], 'incomplete')

    def test_audit_rejects_missing_tail_even_if_completion_claims_complete(self):
        self.release()
        self.now = 30
        health = self.observer.finish(30)
        terminal = {'event': 'completed', 'diagnostic_schema_version': 2, 'status': 'success', 'observer_health': health}
        records = [{'event': 'release_complete', 'generation': 1}, *self.records, terminal]
        self.assertEqual(completion_status(records)['status'], 'success')
        records = [row for row in records if not (row.get('event') == 'release_tail' and row['target_seconds'] == 10)]
        self.assertEqual(completion_status(records)['status'], 'incomplete')

    def test_required_private_measurements_missing_cannot_complete(self):
        self.usage.return_value = {'private_bytes': None, 'working_set_bytes': 123}
        self.release()
        self.now = 30
        self.assert_incomplete(self.observer)
        health = self.records[-1]['observer_health']
        self.assertEqual((health['observed'], health['invalid'], health['missing']), (4, 0, 0))
        self.assertEqual(health['missing_measurements'], 4)
        self.assertEqual(health['missing_measurement_targets'], [[1, 0], [1, 2], [1, 10], [1, 30]])
        self.assertIsNone(self.observer.first_unloaded_private)
        self.classify.assert_not_called()
        self.assertFalse(self.observer.target_captured.is_set())

    def test_one_missing_required_measurement_cannot_be_hidden_by_later_valid_points(self):
        self.release()
        self.usage.return_value = {'working_set_bytes': 123}
        self.now = 2
        self.observer.step()
        self.usage.return_value = {'private_bytes': 100}
        self.now = 30
        self.assert_incomplete(self.observer)
        health = self.records[-1]['observer_health']
        self.assertEqual((health['observed'], health['missing_measurements']), (4, 1))
        self.assertEqual(health['missing_measurement_targets'], [[1, 2]])

    def test_measured_but_lifecycle_invalid_tails_can_complete(self):
        self.state.update(generation=2, loaded=True)
        self.release(1)
        self.now = 30
        complete_recording(SimpleNamespace(emit=self.emit), self.observer, 8, self.now)
        terminal = self.records[-1]
        self.assertEqual(terminal['observer_health']['invalid'], 4)
        self.assertEqual(terminal['observer_health']['missing_measurements'], 0)
        records = [{'event': 'release_complete', 'generation': 1}, *self.records]
        self.assertEqual(completion_status(records)['status'], 'success')

    def test_offline_completion_rechecks_required_values_instead_of_trusting_health(self):
        self.release()
        self.now = 30
        health = self.observer.finish(30)
        base = [{'event': 'release_complete', 'generation': 1}, *self.records,
                {'event': 'completed', 'diagnostic_schema_version': 2, 'status': 'success', 'observer_health': health}]
        for legacy_fields, missing_count in ((True, 4), (False, 1)):
            records = deepcopy(base)
            for row in records:
                if row['event'] != 'release_tail':
                    continue
                if legacy_fields:
                    row.pop('measurement_available', None)
                    row.pop('lifecycle_valid_for_unloaded', None)
                if missing_count == 4 or row['target_seconds'] == 2:
                    row.update(private_bytes=None, valid_for_unloaded=False,
                               invalid_reason='private_memory_unavailable')
            if legacy_fields:
                records[-1]['observer_health']['invalid'] = 4
                records[-1]['observer_health'].pop('missing_measurements', None)
                records[-1]['observer_health'].pop('missing_measurement_targets', None)
            with self.subTest(legacy_fields=legacy_fields):
                result = completion_status(records)
                self.assertEqual(result['status'], 'incomplete')
                self.assertFalse(result['required_measurements_complete'])
                self.assertEqual(result['missing_measurements'], missing_count)

    def test_failure_protocol_returns_nonzero_in_a_real_subprocess(self):
        code = '''import json,sys
from types import SimpleNamespace
from scripts.ocr_observer import BoundaryObserver,DiagnosticState,observed_run,complete_recording
state = DiagnosticState()
state.update(generation=1)
def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)
def usage():
    if sys.argv[1] == 'query':
        raise OSError('query failed')
    return {'private_bytes': None if sys.argv[1] == 'unavailable' else 100, 'working_set_bytes': 123}
deadline = 30 if sys.argv[1] == 'unavailable' else 0
observer = BoundaryObserver(state.snapshot, usage, lambda: {}, emit, clock=lambda: deadline, queue_limit=1)
observer.release(0, 1)
if sys.argv[1] == 'overflow':
    observer.release(0, 2)
if sys.argv[1] == 'join':
    observer.thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: True)
with observed_run(SimpleNamespace(emit=emit), observer, lambda: 8):
    complete_recording(SimpleNamespace(emit=emit), observer, 8, deadline)
'''
        for mode in ('query', 'overflow', 'join', 'missing', 'unavailable'):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, '-B', '-c', code, mode],
                                        cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                        text=True, encoding='utf-8', timeout=10)
                self.assertNotEqual(result.returncode, 0)
                records = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(records[-1]['event'], 'incomplete')
                self.assertFalse(any(row['event'] == 'completed' for row in records))


if __name__ == '__main__':
    unittest.main()
