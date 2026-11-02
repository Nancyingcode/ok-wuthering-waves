from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.benchmark_ocr import compare
from scripts.audit_ocr_evidence import diagnostic_tables
from scripts.ocr_observer import completion_status


class TestOcrReplayCompare(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)

    def rows(self, run_id, count=3):
        rows = []
        for index in range(count):
            common = {'run_id': run_id, 'index': index, 'label': 'fixed', 'phase': 'inference'}
            rows.append({**common, 'event': 'native_inference', 'input_sha256': 'a' * 64,
                         'shapes': [[1, 3, 48, 320]]})
            rows.append({**common, 'event': 'recognition', 'image_shape': [100, 100, 3],
                         'result': [[[[[0, 0], [10, 0], [10, 10], [0, 10]], ['文字', .99]]]]})
        return rows + [{'event': 'completed', 'run_id': run_id, 'calls': count},
                       {'event': 'shutdown', 'run_id': run_id}]

    def save(self, name, rows):
        path = self.root / name
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        return path

    def check(self, expected, actual):
        return compare(self.save('reference.jsonl', expected), [self.save('candidate.jsonl', actual)], strict=True)[0]

    def diagnostic_rows(self, healthy=True):
        rows = self.rows('candidate')
        rows.insert(0, {'event': 'environment', 'run_id': 'candidate', 'diagnostic_schema_version': 2})
        rows[-2].update(diagnostic_schema_version=2, status='success', observer_health={
            'required_tails': False, 'expected': 0, 'observed': 0, 'invalid': 0, 'missing': 0,
            'dropped_events': 0, 'errors': [], 'observer_alive': False, 'complete': healthy})
        return rows

    def test_complete_runs_with_distinct_ids_and_native_hash_backfill_pass(self):
        self.assertEqual(completion_status(self.rows('candidate'))['status'], 'unknown')
        result = self.check(self.rows('reference'), self.rows('candidate'))
        self.assertTrue(result['equal'])
        self.assertEqual(result['scope'], 'strict_fixed_sequence')
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['max_coordinate_difference_px'], 0)

    def test_complete_schema_two_diagnostic_passes(self):
        rows = self.diagnostic_rows()
        self.assertEqual(completion_status(rows)['status'], 'success')
        self.assertTrue(self.check(self.rows('reference'), rows)['equal'])

    def test_missing_terminal_version_cannot_bypass_whole_log_incomplete_status(self):
        rows = self.diagnostic_rows(healthy=False)
        del rows[-2]['diagnostic_schema_version']
        self.assertEqual(completion_status(rows)['status'], 'incomplete')
        result = self.check(self.rows('reference'), rows)
        self.assertFalse(result['run_integrity'])
        self.assertFalse(result['equal'])
        self.assertTrue(result['output_equal'])

    def test_downgraded_terminal_version_cannot_bypass_whole_log_incomplete_status(self):
        rows = self.diagnostic_rows(healthy=False)
        rows[-2]['diagnostic_schema_version'] = 1
        self.assertEqual(completion_status(rows)['status'], 'incomplete')
        result = self.check(self.rows('reference'), rows)
        self.assertFalse(result['run_integrity'])
        self.assertFalse(result['equal'])
        self.assertTrue(result['output_equal'])

    def test_incomplete_failed_mixed_or_reordered_runs_fail(self):
        baseline = self.rows('reference')
        for case in ('first_only', 'no_completion', 'bad_count', 'duplicate_completion', 'failed',
                     'mixed_id', 'duplicate_index', 'missing_call', 'out_of_order', 'empty'):
            rows = self.rows('candidate')
            if case == 'first_only':
                rows = [rows[1]]
            elif case == 'no_completion':
                rows = rows[:-2]
            elif case == 'bad_count':
                rows[-2]['calls'] = 1
            elif case == 'duplicate_completion':
                rows.append(deepcopy(rows[-2]))
            elif case == 'failed':
                rows.append({'event': 'incomplete', 'run_id': 'candidate', 'status': 'incomplete'})
            elif case == 'mixed_id':
                rows[0]['run_id'] = 'other'
            elif case == 'duplicate_index':
                rows[3]['index'] = 0
            elif case == 'missing_call':
                rows.pop(3)
            elif case == 'out_of_order':
                rows[1], rows[3] = rows[3], rows[1]
            else:
                rows = []
            with self.subTest(case=case):
                result = self.check(baseline, rows)
                self.assertFalse(result['equal'])
                self.assertFalse(result['run_integrity'])

    def test_wrong_missing_or_conflicting_input_identity_fails(self):
        for case in ('hash', 'shape', 'missing', 'native_conflict', 'native_index_missing'):
            rows = self.rows('candidate')
            if case == 'hash':
                rows[1]['input_sha256'] = 'b' * 64
            elif case == 'shape':
                rows[1]['image_shape'] = [1, 1, 3]
            elif case == 'missing':
                del rows[0]['input_sha256']
            elif case == 'native_index_missing':
                del rows[0]['index']
                rows[1]['input_sha256'] = 'a' * 64
            else:
                extra = deepcopy(rows[0])
                extra['input_sha256'] = 'b' * 64
                rows.insert(1, extra)
            with self.subTest(case=case):
                result = self.check(self.rows('reference'), rows)
                self.assertFalse(result['equal'])
                self.assertFalse(result['input_identity_verified'])

    def test_truncated_boxes_and_nonfinite_coordinates_fail(self):
        for case in ('point', 'coordinate', 'string', 'nan', 'infinity', 'extra_image'):
            rows = self.rows('candidate')
            box = rows[1]['result'][0][0][0]
            if case == 'point':
                box.pop()
            elif case == 'coordinate':
                box[0].pop()
            elif case == 'extra_image':
                rows[1]['result'].append([])
            else:
                box[0][0] = {'string': 'bad', 'nan': float('nan'), 'infinity': float('inf')}[case]
            with self.subTest(case=case):
                result = self.check(self.rows('reference'), rows)
                self.assertFalse(result['equal'])
                self.assertTrue(result['validation_error_count'])

    def test_exact_text_and_two_pixel_tolerance_allow_confidence_and_tensor_shape_changes(self):
        for delta, text, equal in [(2, '文字', True), (3, '文字', False), (0, '文字 ', False)]:
            rows = self.rows('candidate')
            rows[0]['shapes'] = [[6, 3, 48, 960]]
            rows[1]['result'][0][0][1] = [text, .1]
            for point in rows[1]['result'][0][0][0]:
                point[0] += delta
            with self.subTest(delta=delta, text=text):
                self.assertEqual(self.check(self.rows('reference'), rows)['equal'], equal)
        expected, actual = self.rows('reference'), self.rows('candidate')
        for row in (expected[1], actual[1]):
            row['result'] = [[]]
        self.assertTrue(self.check(expected, actual)['equal'])

    def test_difference_total_is_not_truncated_with_detailed_examples(self):
        rows = self.rows('candidate', 121)
        for row in rows:
            if row['event'] == 'recognition':
                row['result'][0][0][1][0] = 'changed'
        result = self.check(self.rows('reference', 121), rows)
        self.assertEqual(result['difference_count'], 121)
        self.assertEqual(len(result['differences']), 100)

    def test_malformed_json_cli_returns_nonzero_and_structured_failure(self):
        expected = self.save('reference.jsonl', self.rows('reference'))
        actual = self.root / 'broken.jsonl'
        actual.write_text('{"event":', encoding='utf-8')
        command = [sys.executable, 'scripts/benchmark_ocr.py', 'compare', '--strict', '--reference',
                   str(expected), str(actual)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)[0]['equal'])

    def test_enabled_expectation_failures_are_rejected_in_old_and_new_logs(self):
        for enabled_at in ('environment', 'completion', 'conflicting_flags', 'per_call'):
            rows = self.rows('candidate')
            if enabled_at == 'completion':
                rows[-2]['check_expected'] = True
            else:
                rows.insert(0, {'event': 'environment', 'run_id': 'candidate', 'config': {'check_expected': True}})
            rows[-2]['failed_expectations'] = 1
            if enabled_at == 'conflicting_flags':
                rows[-2]['check_expected'] = False
            if enabled_at == 'per_call':
                rows[-2]['failed_expectations'] = 0
                next(row for row in rows if row['event'] == 'recognition')['expectation_checks'] = [{'passed': False}]
            with self.subTest(enabled_at=enabled_at):
                result = self.check(self.rows('reference'), rows)
                self.assertFalse(result['equal'])
                self.assertFalse(result['run_integrity'])
                self.assertTrue(result['output_equal'])
        rows = self.rows('candidate')
        rows[-2].update(check_expected=False, failed_expectations=1)
        rows[1]['expectation_checks'] = [{'passed': False}]
        self.assertTrue(self.check(self.rows('reference'), rows)['equal'])

    def test_diagnostic_success_with_unmeasured_required_tails_is_rejected(self):
        for private, passed in ((None, False), (0, True), (100, True)):
            rows = self.rows('candidate')
            tails = [{'event': 'release_tail', 'run_id': 'candidate', 'release_generation': 1,
                      'target_seconds': delay, 'private_bytes': private, 'working_set_bytes': 123,
                      'valid_for_unloaded': False, 'diagnostic_schema_version': 2,
                      'state_after': {'generation': 2, 'loaded': True},
                      'elapsed': delay, 'lateness_seconds': 0} for delay in (0, 2, 10, 30)]
            rows[-2].update(diagnostic_schema_version=2, status='success', observer_health={
                'required_tails': True, 'expected': 4, 'observed': 4, 'invalid': 4,
                'missing': 0, 'dropped_events': 0, 'errors': [], 'observer_alive': False, 'complete': True})
            rows[-2:-2] = [{'event': 'release_complete', 'run_id': 'candidate', 'generation': 1}, *tails]
            for row in rows:
                row.setdefault('elapsed', 0)
                if row['event'] == 'native_inference':
                    row['model'] = 'det'
            with self.subTest(private=private):
                self.assertEqual(self.check(self.rows('reference'), rows)['equal'], passed)
                output = self.root / f'audit-{private}'
                output.mkdir()
                audit = diagnostic_tables(self.root / 'candidate.jsonl', output, 'managed')
                self.assertEqual(audit['completed'], passed)
                if private is None:
                    self.assertTrue(all(tail['private_mib'] is None for tail in audit['tails']))

    def test_real_replay_cli_checks_expectations_before_success(self):
        code = '''from types import ModuleType
from unittest.mock import patch
import sys
from scripts import benchmark_ocr as benchmark
class Image:
    shape = (100, 100, 3)
    def tobytes(self): return b'fixed-review-input'
class Predictor:
    def _run_openvino_async(self, feed): return []
class OCR:
    def __init__(self, **kwargs): pass
    def ocr(self, image):
        return [[[[[0, 0], [10, 0], [10, 10], [0, 10]], ['actual', .99]]]]
modules = {name: ModuleType(name) for name in (
    'cv2', 'onnxocr', 'onnxocr.onnx_paddleocr', 'onnxocr.predict_base', 'psutil')}
modules['cv2'].imread = lambda path: Image()
modules['onnxocr.onnx_paddleocr'].ONNXPaddleOcr = OCR
modules['onnxocr.predict_base'].PredictBase = Predictor
modules['psutil'].Error = OSError
with patch.dict(sys.modules, modules), patch.object(benchmark, 'environment', return_value={}), \\
        patch.object(benchmark, 'model_files', return_value={}), \\
        patch.object(benchmark, 'memory', return_value={'private_bytes': 100, 'working_set_bytes': 200}):
    benchmark.main()
'''
        image = self.root / 'image.bin'
        image.write_bytes(b'fixture-decoding-is-stubbed')
        for case, enabled, expected_text, exit_code in (
                ('unchecked', False, 'expected', 0), ('failed', True, 'expected', 1),
                ('passed', True, 'actual', 0)):
            manifest = self.root / (case + '.json')
            manifest.write_text(json.dumps([{'path': str(image), 'label': 'fixed',
                                            'expected': [{'text': expected_text}]}]), encoding='utf-8')
            output = self.root / (case + '.jsonl')
            argv = [sys.executable, '-B', '-c', code, 'replay', '--manifest', str(manifest),
                    '--output', str(output), '--repeats', '1', '--settle', '0']
            if enabled:
                argv.append('--check-expected')
            run = subprocess.run(argv, cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, encoding='utf-8', timeout=10)
            rows = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
            terminals = [row for row in rows if row['event'] in ('failed', 'completed')]
            with self.subTest(case=case):
                self.assertEqual(run.returncode, exit_code, run.stderr)
                self.assertEqual(len(terminals), 1)
                self.assertEqual(terminals[0]['event'], 'failed' if exit_code else 'completed')
                self.assertEqual(terminals[0]['check_expected'], enabled)
        reference = self.root / 'unchecked.jsonl'
        failed = self.root / 'failed.jsonl'
        result = subprocess.run([sys.executable, '-B', 'scripts/benchmark_ocr.py', 'compare', '--strict',
                                 '--reference', str(reference), str(failed)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)[0]['equal'])


if __name__ == '__main__':
    unittest.main()
