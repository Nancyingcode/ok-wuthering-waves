"""Verify dependency capabilities with real TaskExecutor OCR in an isolated venv."""

import argparse
from copy import deepcopy
import importlib.metadata
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from benchmark_ocr import Recorder, ROOT, environment, model_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('compat', 'lazy', 'missing-factory', 'missing-compile',
                                          'unknown-parameter'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect-ok', required=True)
    parser.add_argument('--expect-onnx', required=True)
    parser.add_argument('--production-npu', action='store_true', help='Keep production use_npu=true; record actual fallback')
    args = parser.parse_args()
    import cv2
    from config import config
    from ok.notification.ppocr import NotificationPPOCR
    from ok.task.TaskExecutor import TaskExecutor
    from src.ocr_memory import ocr_snapshot

    assert importlib.metadata.version('ok-script') == args.expect_ok
    assert importlib.metadata.version('onnxocr-ppocrv5') == args.expect_onnx
    options = deepcopy(config['ocr'])
    options['params'].update(use_npu=args.production_npu)
    if args.case != 'compat':
        options['preload'] = False
        options['lifecycle']['idle_policy'] = 'ocr_idle'
    if args.case == 'missing-factory':
        options['params']['rec_batch_num'] = 1
    elif args.case == 'missing-compile':
        options['params']['openvino_compile_config'] = {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0}, 'NPU': {}}
    elif args.case == 'unknown-parameter':
        options['params']['rec_batch_nmu'] = 1
    event = threading.Event()
    trace = Recorder(args.output)
    executor = None

    class LifecycleLog(logging.Handler):
        def emit(self, record):
            if 'ocr_memory:' in record.getMessage():
                trace.emit('lifecycle', message=record.getMessage())

    handler = LifecycleLog()
    logging.getLogger('ok').addHandler(handler)
    try:
        with trace.sampling(1):
            trace.emit('environment', **environment(), case=args.case, options=options,
                       model_files=model_files(options['params']))
            try:
                executor = TaskExecutor(SimpleNamespace(interaction=None), exit_event=event,
                                        global_config=SimpleNamespace(get_config=lambda option: {}),
                                        config={'ocr': options})
            except (RuntimeError, ValueError) as error:
                expected = {'missing-factory': 'patched ok-script', 'missing-compile': 'patched onnxocr-ppocrv5',
                            'unknown-parameter': 'Unknown OCR parameters'}
                assert args.case in expected and expected[args.case] in str(error), repr(error)
                trace.emit('capability_rejected', error=str(error), before_model_creation=True)
                trace.emit('completed', expected_rejection=True)
                return
            assert args.case in ('compat', 'lazy'), 'Missing capability was silently accepted'
            trace.snapshot = lambda: ocr_snapshot(executor)
            if args.case == 'lazy':
                assert executor._ocr_init_thread is None and not executor._ocr_lib
                trace.emit('lazy_start', **ocr_snapshot(executor))
            else:
                executor._ocr_init_thread.join(30)
                assert not executor._ocr_init_thread.is_alive()
                assert executor.ocr_lib().snapshot()['loaded']
                trace.emit('preloaded', **ocr_snapshot(executor))
            notification = NotificationPPOCR(executor.ocr_lib)
            frame = cv2.imread(str(ROOT / 'tests/images/tacet_elimination_final.jpg'))
            h, w = frame.shape[:2]
            frame = frame[int(.6*h):int(.96*h), int(.3*w):int(.7*w)].copy()
            result = notification.recognize(frame)
            assert any(box[0] == '500' for box in result), result
            proxy = notification._instance
            trace.emit('first_notification', result=result, **ocr_snapshot(executor))
            until = time.monotonic() + 80
            while time.monotonic() < until and proxy.snapshot()['loaded']:
                time.sleep(.1)
            assert not proxy.snapshot()['loaded'], 'Default idle release not observed'
            trace.emit('idle_released', **ocr_snapshot(executor))
            assert notification.recognize(frame) == result
            assert notification._instance is proxy and proxy.snapshot()['generation'] == 2
            trace.emit('notification_reloaded', result=result, **ocr_snapshot(executor))
            trace.emit('completed', expected_rejection=False)
            event.set()
            monitor = getattr(executor, '_ocr_idle_thread', None)
            if monitor is not None:
                monitor.join(5)
                assert not monitor.is_alive()
    finally:
        event.set()
        logging.getLogger('ok').removeHandler(handler)


if __name__ == '__main__':
    main()
