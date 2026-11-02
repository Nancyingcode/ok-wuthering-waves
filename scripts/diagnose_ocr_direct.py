"""Replay recorded generation lengths directly through OnnxOCR for attribution only."""

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time

from benchmark_ocr import Recorder, ROOT, digest, environment, memory, model_files
from ocr_diagnostics import object_states, virtual_memory_categories, watch_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True, help='Completed managed diagnostic JSONL')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'scripts/ocr_real_inputs.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import cv2
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    from onnxocr.predict_base import PredictBase

    reference = [json.loads(line) for line in args.reference.open(encoding='utf-8')]
    config = next(row for row in reference if row['event'] == 'environment')['options']
    lengths = [row['calls'] for row in reference
               if row['event'] == 'lifecycle' and row['transition'] == 'release_complete']
    if not lengths or sum(lengths) > 600:
        parser.error('Reference must contain 1..600 calls and completed releases')
    frames = []
    entries = json.loads(args.manifest.read_text(encoding='utf-8'))
    for entry in entries:
        image = cv2.imread(str(ROOT / entry['path']))
        if image is None:
            raise ValueError(entry['path'])
        if 'roi' in entry:
            h, w = image.shape[:2]
            x1, y1, x2, y2 = entry['roi']
            image = image[int(y1*h):int(y2*h), int(x1*w):int(x2*w)].copy()
        frames.append(image)
    params = dict(config['params'], use_angle_cls=False)
    trace = Recorder(args.output)
    state = {'generation': 0, 'loaded': False, 'creating': False, 'active_calls': 0}
    trace.snapshot = lambda: dict(state)
    boundaries = queue.Queue(maxsize=128)
    finished = threading.Event()
    first_private = None
    categories = set()
    context = {}
    original_infer = PredictBase._run_openvino_async

    def infer(predictor, feed):
        started = time.perf_counter()
        outputs = original_infer(predictor, feed)
        trace.emit('native_inference', model=Path(predictor.model_dir).stem, **context,
                   shapes=[list(value.shape) for value in feed.values()],
                   input_dtypes=[str(value.dtype) for value in feed.values()],
                   output_nbytes=sum(value.nbytes for value in outputs), seconds=time.perf_counter() - started)
        return outputs

    def observe():
        nonlocal first_private
        pending = []
        while not finished.is_set():
            try:
                released, generation = boundaries.get(timeout=.02)
                pending.extend((released + delay, delay, generation) for delay in (0, 2, 10, 30))
            except queue.Empty:
                pass
            now = time.monotonic()
            due = [item for item in pending if item[0] <= now]
            pending = [item for item in pending if item[0] > now]
            for target, delay, generation in due:
                current, usage = dict(state), memory(os.getpid())
                trace.emit('release_tail', release_generation=generation, target_seconds=delay,
                           lateness_seconds=now - target, weak_objects=object_states(generation),
                           **current, **usage)
                if not current['loaded'] and not current['creating'] and usage['private_bytes'] is not None:
                    if first_private is None:
                        first_private = usage['private_bytes']
                    high = usage['private_bytes'] - first_private >= 128 * 1024**2
                    category = 'high' if high else 'first_unloaded'
                    if category not in categories and (high or not categories):
                        categories.add(category)
                        result = virtual_memory_categories()
                        trace.emit('memory_categories', category=category, state_before=current,
                                   state_after=dict(state), release_generation=generation, **result)

    observer = threading.Thread(target=observe, daemon=True)
    PredictBase._run_openvino_async = infer
    try:
        with trace.sampling(1):
            trace.emit('environment', **environment(), reference_sha256=digest(args.reference),
                       manifest_sha256=digest(args.manifest), model_files=model_files(params), params=params,
                       generation_lengths=lengths, protocol='manual lifecycle attribution; no TaskExecutor',
                       period_seconds=1, max_calls=600, max_seconds=600, max_private_mib=4096,
                       tail_seconds=31, source_sha256=digest(Path(__file__)))
            observer.start()
            origin = time.monotonic()
            index = 0
            for generation, calls in enumerate(lengths, 1):
                state.update(generation=generation, creating=True)
                trace.phase = 'create'
                trace.emit('factory_before', **state, **memory(os.getpid()))
                model = ONNXPaddleOcr(**params)
                watch_model(model, generation)
                state.update(loaded=True, creating=False)
                trace.emit('factory_returned', **state, **memory(os.getpid()))
                trace.phase = 'inference'
                for _ in range(calls):
                    if time.monotonic() - origin >= 600 or memory(os.getpid())['private_bytes'] > 4096 * 1024**2:
                        raise RuntimeError('Direct diagnostic safety limit')
                    target = origin + index
                    if trace.stop.wait(max(0, target - time.monotonic())):
                        raise RuntimeError('Sampler stopped')
                    frame = frames[index % len(frames)]
                    context.update(index=index, generation=generation, label=entries[index % len(entries)]['label'],
                                   input_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
                    state['active_calls'] = 1
                    started = time.perf_counter()
                    result = model.ocr(frame)
                    trace.emit('recognition', **context, image_shape=list(frame.shape),
                               result=result, seconds=time.perf_counter() - started)
                    result = None
                    state['active_calls'] = 0
                    index += 1
                trace.phase = 'release'
                for name in ('text_detector', 'text_recognizer', 'text_classifier'):
                    predictor = getattr(model, name, None)
                    native_queue = getattr(predictor, '_async_queue', None)
                    if native_queue is not None:
                        native_queue.wait_all()
                predictor = native_queue = model = None
                gc.collect()
                state['loaded'] = False
                trace.emit('release_complete', **state, calls=calls, **memory(os.getpid()))
                boundaries.put_nowait((time.monotonic(), generation))
            trace.phase = 'cooldown'
            trace.stop.wait(31)
            finished.set()
            observer.join(5)
            trace.emit('completed', calls=index, stop_reason='fixed reference exhausted', observer_alive=observer.is_alive())
    finally:
        finished.set()
        PredictBase._run_openvino_async = original_infer


if __name__ == '__main__':
    main()
