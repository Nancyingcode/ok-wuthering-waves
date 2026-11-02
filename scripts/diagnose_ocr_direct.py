"""Replay recorded generation lengths directly through OnnxOCR for attribution only."""

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import time

from benchmark_ocr import Recorder, ROOT, digest, environment, memory, model_files
from ocr_diagnostics import object_states, virtual_memory_categories, watch_model
from scripts.ocr_observer import BoundaryObserver, DiagnosticState, SCHEMA_VERSION, complete_recording, observed_run


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
    state = DiagnosticState()
    trace.snapshot = state.snapshot
    observer = BoundaryObserver(state.snapshot, lambda: memory(os.getpid()), virtual_memory_categories,
                                trace.emit, object_states)
    context = {}
    index = 0
    original_infer = PredictBase._run_openvino_async

    def infer(predictor, feed):
        started = time.perf_counter()
        outputs = original_infer(predictor, feed)
        trace.emit('native_inference', model=Path(predictor.model_dir).stem, **context,
                   shapes=[list(value.shape) for value in feed.values()],
                   input_dtypes=[str(value.dtype) for value in feed.values()],
                   output_nbytes=sum(value.nbytes for value in outputs), seconds=time.perf_counter() - started)
        return outputs

    PredictBase._run_openvino_async = infer
    try:
        with trace.sampling(1), observed_run(trace, observer, lambda: index):
            trace.emit('environment', **environment(), reference_sha256=digest(args.reference),
                       manifest_sha256=digest(args.manifest), model_files=model_files(params), params=params,
                       generation_lengths=lengths, protocol='manual lifecycle attribution; no TaskExecutor',
                       diagnostic_schema_version=SCHEMA_VERSION,
                       period_seconds=1, max_calls=600, max_seconds=600, max_private_mib=4096,
                       tail_seconds=31, source_sha256=digest(Path(__file__)))
            observer.start()
            origin = time.monotonic()
            for generation, calls in enumerate(lengths, 1):
                state.update(generation=generation, creating=True)
                trace.phase = 'create'
                trace.emit('factory_before', **state.snapshot(), **memory(os.getpid()))
                model = ONNXPaddleOcr(**params)
                watch_model(model, generation)
                state.update(loaded=True, creating=False)
                trace.emit('factory_returned', **state.snapshot(), **memory(os.getpid()))
                trace.phase = 'inference'
                for _ in range(calls):
                    observer.raise_if_failed()
                    if time.monotonic() - origin >= 600 or memory(os.getpid())['private_bytes'] > 4096 * 1024**2:
                        raise RuntimeError('Direct diagnostic safety limit')
                    target = origin + index
                    if trace.stop.wait(max(0, target - time.monotonic())):
                        raise RuntimeError('Sampler stopped')
                    frame = frames[index % len(frames)]
                    context.update(index=index, generation=generation, label=entries[index % len(entries)]['label'],
                                   input_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
                    state.update(active_calls=1)
                    started = time.perf_counter()
                    result = model.ocr(frame)
                    trace.emit('recognition', **context, image_shape=list(frame.shape),
                               result=result, seconds=time.perf_counter() - started)
                    result = None
                    state.update(active_calls=0)
                    index += 1
                trace.phase = 'release'
                state.update(recycling=True)
                for name in ('text_detector', 'text_recognizer', 'text_classifier'):
                    predictor = getattr(model, name, None)
                    native_queue = getattr(predictor, '_async_queue', None)
                    if native_queue is not None:
                        native_queue.wait_all()
                predictor = native_queue = model = None
                gc.collect()
                state.update(loaded=False, recycling=False)
                trace.emit('release_complete', **state.snapshot(), calls=calls, **memory(os.getpid()))
                observer.release(time.monotonic(), generation)
            trace.phase = 'cooldown'
            deadline = time.monotonic() + 31
            observer.set_deadline(deadline)
            while time.monotonic() < deadline:
                observer.raise_if_failed()
                trace.stop.wait(min(.1, max(0, deadline - time.monotonic())))
            complete_recording(trace, observer, index, deadline)
    finally:
        if not observer.closing:
            observer.finish(time.monotonic())
        PredictBase._run_openvino_async = original_infer


if __name__ == '__main__':
    main()
