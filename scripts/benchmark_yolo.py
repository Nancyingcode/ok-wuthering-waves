"""Paired YOLO-only controls, holding and releasing an idle synchronous detector."""

import argparse
from pathlib import Path
import threading
import time

from benchmark_ocr import Recorder, ROOT, digest, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--image', type=Path, default=ROOT / 'tests/images/absorb.png')
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    import cv2
    from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
    from src.globals import Globals

    trace = Recorder(args.output)
    manager = Globals(threading.Event())
    device = None
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError(f'Cannot decode {args.image}')
    trace.snapshot = lambda: {'yolo': manager.yolo_snapshot(), 'device': device}
    weights = ROOT / 'assets/echo_model/echo.onnx'
    with trace.sampling(1):
        env = environment()
        env['capture_backend'] = 'file replay, YOLO only; OCR never created'
        trace.emit('environment', **env, image_sha256=digest(args.image), weights_sha256=digest(weights),
                   load=args.load, image_shape=list(image.shape), protocol=3)
        trace.phase = 'unloaded'
        trace.stop.wait(3)
        if args.load:
            trace.phase = 'first_detect'
            manager._yolo_model = OpenVinoYolo8Detect(weights=str(weights))
            device = str(manager._yolo_model.compiled_model.get_property('EXECUTION_DEVICES'))
            for index in range(6):
                started = time.perf_counter()
                result = manager.yolo_detect(image, threshold=.3)
                trace.emit('detection', index=index, seconds=time.perf_counter() - started,
                           boxes=[{'name': box.name, 'xywh': [box.x, box.y, box.width, box.height]} for box in result])
            expected = [(box.name, box.x, box.y, box.width, box.height) for box in result]
            result = None
        trace.phase = 'holding'
        trace.sample()
        trace.stop.wait(3)
        trace.phase = 'release'
        released = manager.release_yolo()
        trace.emit('released', had_model=released)
        trace.phase = 'after_release'
        trace.sample()
        trace.stop.wait(3)
        if args.load and args.reload:
            trace.phase = 'reload'
            started = time.perf_counter()
            manager._yolo_model = OpenVinoYolo8Detect(weights=str(weights))
            result = manager.yolo_detect(image, threshold=.3)
            actual = [(box.name, box.x, box.y, box.width, box.height) for box in result]
            trace.emit('reloaded', seconds=time.perf_counter() - started, equal=expected == actual,
                       boxes=len(actual))
            if expected != actual:
                raise AssertionError('YOLO outputs changed after release and reload')
            result = None
            manager.release_yolo()
        trace.emit('completed')


if __name__ == '__main__':
    main()
