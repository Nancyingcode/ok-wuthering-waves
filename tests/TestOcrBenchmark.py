import json
import gc
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import patch

from scripts.benchmark_ocr import expected_results, memory, summarize
from scripts.summarize_ocr_lifecycle import summarize as summarize_lifecycle
from scripts.ocr_diagnostics import object_states, watch_model
from scripts.audit_ocr_evidence import factory_overlap


class TestOcrBenchmark(unittest.TestCase):
    def test_unloaded_wrapper_during_next_factory_is_not_a_pure_unloaded_observation(self):
        records = [{'event': 'factory_before', 'elapsed': 10},
                   {'event': 'factory_returned', 'elapsed': 10.5}]
        self.assertTrue(factory_overlap(records, 10.1, 10.2))
        self.assertTrue(factory_overlap(records, 9.99, 10.01))
        self.assertFalse(factory_overlap(records, 11, 11.1))

    def test_weak_diagnostics_do_not_keep_model_or_predictor_alive(self):
        class Node:
            pass

        model = Node()
        model.text_detector = Node()
        model.text_detector._async_queue = Node()
        model.text_detector.session = []
        reference = weakref.ref(model)
        watch_model(model, 12345)
        model = None
        gc.collect()
        self.assertIsNone(reference())
        statuses = object_states(12345)
        self.assertTrue(statuses)
        self.assertTrue(all(row['alive'] is False for row in statuses if row['weakref_supported']))
        self.assertTrue(any(row['weakref_supported'] is False and row['alive'] is None for row in statuses))

    def test_key_text_requires_the_expected_position(self):
        entry = {'expected': [{'text': '确认', 'region': [.5, .5, 1, 1]}]}
        detection = [[[[[0, 0], [10, 0], [10, 10], [0, 10]], ('确认', .99)]]]
        self.assertFalse(expected_results(entry, detection, [100, 100, 3])[0]['passed'])
        entry['expected'][0]['region'] = [0, 0, .2, .2]
        self.assertTrue(expected_results(entry, detection, [100, 100, 3])[0]['passed'])
        self.assertFalse(expected_results({'expected_empty': True}, detection, [100, 100, 3])[0]['passed'])

    def test_release_tail_with_a_reloaded_model_is_not_an_unloaded_low(self):
        def observation(loaded, generation):
            return {'event': 'release_tail', 'elapsed': generation, 'phase': 'continuous',
                    'private_bytes': 100, 'working_set_bytes': 200,
                    'target_seconds': 2, 'release_generation': 1, 'lateness_seconds': .01,
                    'models': [{'loaded': loaded, 'generation': generation, 'recycling': False}]}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            records = [
                {'event': 'lifecycle', 'transition': 'created', 'elapsed': 0, 'phase': 'baseline', 'generation': 1,
                 'created_at': 10, 'seconds': .1},
                observation(False, 1),
                {'event': 'lifecycle', 'transition': 'created', 'elapsed': 1.5, 'phase': 'continuous', 'generation': 2,
                 'created_at': 20, 'seconds': .1},
                observation(True, 2),
            ]
            path.write_text('\n'.join(json.dumps(row) for row in records), encoding='utf-8')
            result = summarize_lifecycle(path, Path(directory) / 'summary')
        self.assertEqual([row['pure_unloaded'] for row in result['release_tails']], [True, False])
        self.assertEqual([row['latest_created_at'] for row in result['release_tails']], [10, 20])
        self.assertEqual([row['released_model_created_at'] for row in result['release_tails']], [10, 10])
        self.assertEqual([row['interval_validity'] for row in result['release_tails']], ['unknown', 'unknown'])
        self.assertIsNone(result['completed'])

    def test_versioned_summary_uses_full_interval_instead_of_final_unloaded_boolean(self):
        row = {'event': 'release_tail', 'elapsed': 1, 'phase': 'cooldown',
               'diagnostic_schema_version': 2, 'state_after': {'generation': 2, 'loaded': False},
               'valid_for_unloaded': False, 'invalid_reason': 'epoch_changed',
               'target_seconds': 2, 'release_generation': 1, 'lateness_seconds': .01}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            path.write_text(json.dumps(row), encoding='utf-8')
            result = summarize_lifecycle(path, Path(directory) / 'summary')
        self.assertFalse(result['completed'])
        self.assertFalse(result['release_tails'][0]['pure_unloaded'])
        self.assertEqual(result['release_tails'][0]['generation'], 2)

    def test_missing_private_memory_is_not_replaced_by_working_set(self):
        with patch('psutil.Process', return_value=SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=123))):
            result = memory(1)
        self.assertIsNone(result['private_bytes'])
        self.assertEqual(result['working_set_bytes'], 123)

    def test_summary_separates_metrics_cold_calls_and_each_release_generation(self):
        records = [
            {'event': 'created', 'seconds': 2},
            {'event': 'sample', 'private_bytes': 400, 'working_set_bytes': 900},
            {'event': 'recognition', 'cold': True, 'seconds': 10},
            {'event': 'recognition', 'cold': False, 'seconds': 1},
            {'event': 'recognition', 'cold': False, 'seconds': 3},
            {'event': 'sample', 'phase': 'after_release', 'generation': 1,
             'private_bytes': 200, 'working_set_bytes': 800},
            {'event': 'created', 'seconds': 4},
            {'event': 'sample', 'phase': 'after_release', 'generation': 2,
             'private_bytes': 210, 'working_set_bytes': 700},
            {'event': 'completed'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            path.write_text('\n'.join(json.dumps({'elapsed': index, 'phase': 'inference', **row})
                                      for index, row in enumerate(records)), encoding='utf-8')
            result = summarize([path])[0]
        self.assertEqual(result['peaks'], {'private_bytes': 400, 'working_set_bytes': 900})
        self.assertEqual(result['after_release_lows'], {1: 200, 2: 210})
        self.assertEqual(result['hot_p95'], 3)
        self.assertEqual(result['first_calls'], [10])
        self.assertEqual(result['load_seconds'], [2, 4])
        self.assertEqual(result['reloads'], 1)
        self.assertTrue(result['completed'])


if __name__ == '__main__':
    unittest.main()
