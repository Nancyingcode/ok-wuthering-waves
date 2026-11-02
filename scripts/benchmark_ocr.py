"""Finite OCR replay and process sampling; all evidence is streamed to JSONL."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def memory(pid):
    import psutil

    info = psutil.Process(pid).memory_info()
    private = getattr(info, 'private', None)
    return {'private_bytes': private, 'working_set_bytes': info.rss,
            'private_unavailable': None if private is not None else 'psutil has no private field'}


class Recorder:
    def __init__(self, path, pid=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('x', encoding='utf-8')
        self.started = time.monotonic()
        self.run_id = uuid.uuid4().hex
        self.pid = pid or os.getpid()
        self.phase = 'baseline'
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.snapshot = lambda: {}

    def emit(self, event, **fields):
        row = {'run_id': self.run_id, 'pid': self.pid, 'event': event,
               'timestamp': datetime.now(timezone.utc).isoformat(),
               'elapsed': time.monotonic() - self.started, 'phase': self.phase, **fields}
        with self.lock:
            self.file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            self.file.flush()

    def sample(self):
        import psutil

        try:
            self.emit('sample', **memory(self.pid), **self.snapshot())
        except (psutil.Error, OSError, RuntimeError) as error:
            self.emit('sample_error', error=str(error))

    @contextmanager
    def sampling(self, interval):
        def run():
            while not self.stop.wait(interval):
                self.sample()

        self.sample()
        thread = threading.Thread(target=run, name='OCRMemorySample', daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self.stop.set()
            thread.join()
            self.sample()
            self.file.close()


def environment():
    packages = {}
    for name, module in [('ok-script', 'ok'), ('onnxocr-ppocrv5', 'onnxocr'),
                         ('openvino', 'openvino'), ('psutil', 'psutil'), ('numpy', 'numpy'),
                         ('PySide6', 'PySide6')]:
        dist = importlib.metadata.distribution(name)
        packages[name] = {'version': dist.version,
                          'module_file': importlib.util.find_spec(module).origin}
    import psutil
    commit = None
    repository = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT,
                                text=True, capture_output=True)
    if repository.returncode == 0 and Path(repository.stdout.strip()).resolve() == ROOT:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    return {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
            'cpu': platform.processor(), 'logical_cpus': os.cpu_count(),
            'physical_memory_bytes': psutil.virtual_memory().total,
            'commit': commit, 'isa': os.environ.get('ONEDNN_MAX_CPU_ISA'),
            'packages': packages, 'capture_backend': 'file replay',
            'yolo': {'loaded': False}, 'notifications': False, 'mcp': False}


def model_files(params=None):
    dist = importlib.metadata.distribution('onnxocr-ppocrv5')
    result = {}
    for kind in ('det', 'rec', 'cls'):
        key = f'{kind}_model_dir'
        path = (params or {}).get(key) or dist.locate_file(f'onnxocr/models/ppocrv5/{kind}/{kind}.onnx')
        result[key] = {'path': str(path), 'sha256': digest(path)}
    return result


def expected_results(entry, result, image_shape):
    detections = result[0] if result and result[0] else []
    checks = []
    if entry.get('expected_empty'):
        checks.append({'expected_empty': True, 'passed': not detections})
    for expected in entry.get('expected', []):
        region = expected.get('region', [0, 0, 1, 1])
        matched = False
        for points, (text, confidence) in detections:
            text_matches = expected['text'] in text if expected.get('contains') else expected['text'] == text
            x = sum(point[0] for point in points) / len(points) / image_shape[1]
            y = sum(point[1] for point in points) / len(points) / image_shape[0]
            if text_matches and region[0] <= x <= region[2] and region[1] <= y <= region[3]:
                matched = True
        checks.append({**expected, 'passed': matched})
    return checks


def replay(args):
    import cv2
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    from onnxocr.predict_base import PredictBase

    entries = json.loads(args.manifest.read_text(encoding='utf-8'))
    if not entries or args.repeats * len(entries) * args.cycles > 10000:
        raise ValueError('Use 1..10000 calls per finite replay')
    params = {'use_openvino': True, 'use_npu': False, 'use_angle_cls': False}
    if args.config:
        params.update(json.loads(args.config.read_text(encoding='utf-8')).get('params', {}))
    trace = Recorder(args.output)
    params['use_npu'] = args.production_npu
    model = None
    model_ref = [None]
    generation = 0
    failed_expectations = 0
    trace.snapshot = lambda: {'model_loaded': model_ref[0] is not None, 'generation': generation}
    original = PredictBase._run_openvino_async

    def infer(predictor, feed):
        started = time.perf_counter()
        outputs = original(predictor, feed)
        trace.emit('native_inference', model=Path(predictor.model_dir).stem,
                   device='NPU' if predictor.is_npu else 'CPU',
                   shapes=[list(value.shape) for value in feed.values()],
                   output_nbytes=sum(value.nbytes for value in outputs),
                   seconds=time.perf_counter() - started)
        return outputs

    PredictBase._run_openvino_async = infer
    try:
        with trace.sampling(args.interval):
            trace.emit('environment', **environment(), model_files=model_files(params),
                       config=vars(args), params=params,
                       inputs=[{**entry, 'sha256': digest(ROOT / entry['path'])} for entry in entries],
                       disk_cache='existing cache/openvino; keep warm for all compared runs')
            trace.stop.wait(args.settle)
            for cycle in range(args.cycles):
                trace.phase = 'create'
                started = time.perf_counter()
                model = ONNXPaddleOcr(**params)
                model_ref[0] = model
                generation += 1
                trace.emit('created', generation=generation, seconds=time.perf_counter() - started,
                           self_test_possible=params['use_npu'],
                           diagnostics=[getattr(getattr(model, name, None), 'compile_diagnostics', None)
                                        for name in ('text_detector', 'text_recognizer', 'text_classifier')])
                trace.sample()
                trace.phase = 'inference'
                for index in range(args.repeats * len(entries)):
                    if time.monotonic() - trace.started > args.max_seconds:
                        raise RuntimeError('Replay duration limit exceeded')
                    private = memory(os.getpid())['private_bytes']
                    if private is not None and private > args.max_private_mib * 1024 ** 2:
                        raise RuntimeError('Replay private memory limit exceeded')
                    entry = entries[index % len(entries)]
                    image = cv2.imread(str(ROOT / entry['path']))
                    if image is None:
                        raise ValueError(f'Cannot decode {entry["path"]}')
                    source_shape = list(image.shape)
                    if 'roi' in entry:
                        h, w = image.shape[:2]
                        x1, y1, x2, y2 = entry['roi']
                        image = image[int(y1*h):int(y2*h), int(x1*w):int(x2*w)].copy()
                    started = time.perf_counter()
                    result = model.ocr(image)
                    seconds = time.perf_counter() - started
                    checks = expected_results(entry, result, image.shape)
                    failed_expectations += sum(not check['passed'] for check in checks)
                    trace.emit('recognition', cycle=cycle, index=index, label=entry['label'],
                               cold=index == 0, source_shape=source_shape, image_shape=list(image.shape),
                               seconds=seconds, result=result, expectation_checks=checks)
                    result = image = None
                trace.phase = 'release'
                trace.emit('release_requested', reason='finite replay cycle')
                started = time.perf_counter()
                for name in ('text_detector', 'text_recognizer', 'text_classifier'):
                    predictor = getattr(model, name, None)
                    queue = getattr(predictor, '_async_queue', None)
                    if queue is not None:
                        queue.wait_all()
                predictor = queue = None
                trace.emit('drained')
                model_ref[0] = model = None
                gc.collect()
                trace.emit('released', seconds=time.perf_counter() - started)
                trace.phase = 'after_release'
                trace.sample()
                trace.stop.wait(args.settle)
            trace.emit('completed', cycles=args.cycles, failed_expectations=failed_expectations)
            if args.check_expected and failed_expectations:
                raise AssertionError(f'{failed_expectations} key text/position expectations failed')
    finally:
        PredictBase._run_openvino_async = original


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction + .5))]


def summarize(paths):
    reports = []
    for path in paths:
        warm = []
        cold = []
        loading = []
        lows = {}
        peaks = {'private_bytes': None, 'working_set_bytes': None}
        created = 0
        complete = False
        total = 0
        for line in path.open(encoding='utf-8'):
            row = json.loads(line)
            total = row['elapsed']
            if row['event'] == 'sample':
                for key in peaks:
                    value = row.get(key)
                    if value is not None:
                        peaks[key] = max(peaks[key] or 0, value)
                if row['phase'] == 'after_release' and row.get('private_bytes') is not None:
                    generation = row.get('generation', created)
                    lows[generation] = min(lows.get(generation, float('inf')), row['private_bytes'])
            elif row['event'] == 'recognition':
                target = cold if row['cold'] else warm
                if len(target) >= 10000:
                    raise ValueError('Summary supports at most 10000 calls per run')
                target.append(row['seconds'])
            elif row['event'] == 'created':
                created += 1
                loading.append(row['seconds'])
            elif row['event'] == 'completed':
                complete = True
        reports.append({'file': str(path), 'completed': complete, 'peaks': peaks,
                        'after_release_lows': lows, 'hot_p50': percentile(warm, .5),
                        'hot_p95': percentile(warm, .95), 'first_calls': cold,
                        'load_seconds': loading,
                        'reloads': max(0, created - 1), 'seconds': total})
    return reports


def compare(reference, paths):
    def observations(path):
        with path.open(encoding='utf-8') as source:
            for line in source:
                row = json.loads(line)
                if row['event'] == 'recognition':
                    yield row

    expected = {}
    for row in observations(reference):
        expected.setdefault(row['label'], row['result'])
    comparisons = []
    for path in paths:
        differences = []
        calls = 0
        for row in observations(path):
            calls += 1
            baseline = expected[row['label']][0]
            actual = row['result'][0]
            changed = len(baseline) != len(actual)
            if not changed:
                for old, new in zip(baseline, actual):
                    text_changed = old[1][0] != new[1][0]
                    position_changed = any(abs(a - b) > 2 for p, q in zip(old[0], new[0])
                                           for a, b in zip(p, q))
                    changed |= text_changed or position_changed
            if changed:
                if len(differences) < 100:
                    differences.append({'label': row['label'], 'index': row['index'],
                                        'expected': baseline, 'actual': actual})
        comparisons.append({'file': str(path), 'calls': calls, 'equal': not differences,
                            'differences': differences})
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('replay')
    run.add_argument('--manifest', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--config', type=Path)
    run.add_argument('--repeats', type=int, default=10)
    run.add_argument('--cycles', type=int, default=1)
    run.add_argument('--settle', type=float, default=5)
    run.add_argument('--interval', type=float, default=1)
    run.add_argument('--max-seconds', type=float, default=600)
    run.add_argument('--max-private-mib', type=float, default=4096)
    run.add_argument('--production-npu', action='store_true')
    run.add_argument('--check-expected', action='store_true')
    summary = sub.add_parser('summarize')
    summary.add_argument('paths', nargs='+', type=Path)
    comparison = sub.add_parser('compare')
    comparison.add_argument('--reference', type=Path, required=True)
    comparison.add_argument('paths', nargs='+', type=Path)
    sample = sub.add_parser('sample')
    sample.add_argument('--pid', type=int, required=True)
    sample.add_argument('--output', type=Path, required=True)
    sample.add_argument('--seconds', type=float, default=120)
    sample.add_argument('--interval', type=float, default=1)
    args = parser.parse_args()
    if args.command == 'summarize':
        print(json.dumps(summarize(args.paths), indent=2))
    elif args.command == 'compare':
        result = compare(args.reference, args.paths)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not all(item['calls'] and item['equal'] for item in result):
            raise SystemExit(1)
    elif args.command == 'replay':
        if min(args.repeats, args.cycles, args.interval, args.max_seconds, args.max_private_mib) <= 0:
            parser.error('Replay limits must be positive')
        replay(args)
    else:
        if args.seconds <= 0 or args.interval <= 0:
            parser.error('Sampling limits must be positive')
        trace = Recorder(args.output, args.pid)
        with trace.sampling(args.interval):
            trace.stop.wait(args.seconds)


if __name__ == '__main__':
    main()
