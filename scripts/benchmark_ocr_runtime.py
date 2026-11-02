"""Observe an owned executor or GUI without sending game input or replacing the installed app."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from benchmark_ocr import Recorder, ROOT, digest, environment, memory, model_files


@contextmanager
def backend_trace(trace, enabled):
    if not enabled:
        yield
        return
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    from onnxocr.predict_base import PredictBase
    old_infer, old_ocr = PredictBase._run_openvino_async, ONNXPaddleOcr.ocr

    def infer(predictor, feed):
        started = time.perf_counter()
        outputs = old_infer(predictor, feed)
        trace.emit('native_inference', model=Path(predictor.model_dir).stem,
                   device='NPU' if predictor.is_npu else 'CPU',
                   shapes=[list(value.shape) for value in feed.values()],
                   output_nbytes=sum(value.nbytes for value in outputs),
                   seconds=time.perf_counter() - started)
        return outputs

    def ocr(model, image, *args, **kwargs):
        from ok import og
        started = time.perf_counter()
        result = old_ocr(model, image, *args, **kwargs)
        trace.emit('recognition', image_shape=list(image.shape), result=result,
                   task=getattr(getattr(og, 'executor', None), 'current_task', None).__class__.__name__,
                   seconds=time.perf_counter() - started)
        return result

    PredictBase._run_openvino_async, ONNXPaddleOcr.ocr = infer, ocr
    try:
        yield
    finally:
        PredictBase._run_openvino_async, ONNXPaddleOcr.ocr = old_infer, old_ocr


def natural_protocol(args):
    """Submit real OCR to TaskExecutor; only its production policy may unload it."""
    import cv2
    from ok.task.TaskExecutor import TaskExecutor
    from onnxocr.predict_base import PredictBase
    from src.ocr_memory import install_ocr_idle_release, ocr_snapshot

    install_ocr_idle_release()
    from config import config
    options = deepcopy(config['ocr'])
    options['params'].update(use_npu=False)
    if args.config:
        options = json.loads(args.config.read_text(encoding='utf-8'))
    trace = Recorder(args.output)
    exit_event = threading.Event()
    executor = None
    original_infer = PredictBase._run_openvino_async
    original_factory = TaskExecutor._create_ocr_lib
    call_context = {}
    count = 0
    factory_generations = {}
    if args.diagnostics:
        from ocr_diagnostics import object_states, virtual_memory_categories, watch_model
    from scripts.ocr_observer import BoundaryObserver, SCHEMA_VERSION, complete_recording, observed_run

    def scalar_state():
        if executor is None:
            return {}
        with executor._ocr_lib_lock:
            proxy = executor._ocr_lib.get('default')
        return proxy.diagnostic_state() if proxy is not None else {}

    observer = BoundaryObserver(scalar_state, lambda: memory(os.getpid()),
                                virtual_memory_categories if args.diagnostics else lambda: {}, trace.emit,
                                object_states if args.diagnostics else lambda generation: [],
                                diagnostics=args.diagnostics)

    def factory(owner, name):
        model = original_factory(owner, name)
        generation = factory_generations.get(name, 0) + 1
        factory_generations[name] = generation
        watch_model(model, generation)
        return model

    class LifecycleLog(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            for marker, kind in [('OCR_LIFECYCLE ', 'lifecycle'), ('OCR_DIAGNOSTIC ', 'diagnostic')]:
                if marker in message:
                    observer.publish(kind, json.loads(message.split(marker, 1)[1]))

    handler = LifecycleLog()
    logging.getLogger('ok').addHandler(handler)

    def infer(predictor, feed):
        started = time.perf_counter()
        outputs = original_infer(predictor, feed)
        trace.emit('native_inference', model=Path(predictor.model_dir).stem,
                   device='NPU' if predictor.is_npu else 'CPU',
                   shapes=[list(value.shape) for value in feed.values()],
                   output_nbytes=sum(value.nbytes for value in outputs),
                   input_dtypes=[str(value.dtype) for value in feed.values()],
                   **call_context,
                   seconds=time.perf_counter() - started)
        return outputs

    def wait_until(target):
        while time.monotonic() < target:
            observer.raise_if_failed()
            if trace.stop.wait(min(.1, max(0, target - time.monotonic()))):
                raise RuntimeError('Sampler stopped')

    try:
        if args.trace_inference or args.diagnostics:
            PredictBase._run_openvino_async = infer
        if args.diagnostics:
            TaskExecutor._create_ocr_lib = factory
        with trace.sampling(1), observed_run(trace, observer, lambda: count):
            trace.emit('environment', **environment(), protocol=args.protocol, protocol_version=1,
                       diagnostic_schema_version=SCHEMA_VERSION,
                       options=options, limits=vars(args), model_files=model_files(options.get('params')),
                       manifest_sha256=digest(args.manifest),
                       source_sha256=digest(ROOT / 'src/ocr_memory.py'))
            executor = TaskExecutor(SimpleNamespace(interaction=None), exit_event=exit_event,
                                    global_config=SimpleNamespace(get_config=lambda option: {}),
                                    config={'ocr': options})
            trace.snapshot = lambda: ocr_snapshot(executor)
            observer.start()
            if executor._ocr_init_thread is not None:
                executor._ocr_init_thread.join(30)
                if executor._ocr_init_thread.is_alive():
                    raise TimeoutError('OCR preload did not finish')
            entries = json.loads(args.manifest.read_text(encoding='utf-8'))
            frames = []
            for entry in entries:
                frame = cv2.imread(str(ROOT / entry['path']))
                if frame is None:
                    raise ValueError(f'Cannot decode {entry["path"]}')
                if 'roi' in entry:
                    h, w = frame.shape[:2]
                    x1, y1, x2, y2 = entry['roi']
                    frame = frame[int(y1*h):int(y2*h), int(x1*w):int(x2*w)].copy()
                frames.append(frame)
            proxy = executor.ocr_lib()
            proxy_id = id(proxy)
            expected = {}

            def recognize(index, target):
                nonlocal count
                private = memory(os.getpid())['private_bytes']
                if private is not None and private > args.max_private_mib * 1024**2:
                    raise RuntimeError('Private memory safety limit exceeded')
                if time.monotonic() - trace.started > args.seconds + args.cooldown + 120:
                    raise RuntimeError('Duration safety limit exceeded')
                assert id(executor.ocr_lib()) == proxy_id, 'Stable proxy changed'
                before = proxy.snapshot()
                call_context.clear()
                call_context.update(index=index, label=entries[index % len(entries)]['label'],
                                    generation=before['generation'] + int(not before['loaded']))
                if args.diagnostics:
                    import hashlib
                    call_context['input_sha256'] = hashlib.sha256(frames[index % len(frames)].tobytes()).hexdigest()
                started = time.perf_counter()
                lag = time.monotonic() - target
                result = proxy.ocr(frames[index % len(frames)])
                label = entries[index % len(entries)]['label']
                canonical = [[item[0], item[1][0]] for item in result[0]]
                if label in expected:
                    assert canonical == expected[label], f'Result changed for {label}'
                expected[label] = canonical
                trace.emit('recognition', index=index, label=label, result=result,
                           image_shape=list(frames[index % len(frames)].shape),
                           seconds=time.perf_counter() - started, scheduling_lag_seconds=lag,
                           scheduled_monotonic=target, before=before, after=proxy.snapshot(),
                           cold=not before['loaded'], same_proxy=True)
                count += 1

            def job():
                trace.phase = 'continuous' if args.protocol == 'continuous' else args.protocol
                origin = time.monotonic()
                if args.protocol == 'age':
                    recognize(0, origin)
                    created = proxy.snapshot()['created_at']
                    for index, offset in enumerate(range(10, 300, 10), 1):
                        wait_until(created + offset)
                        recognize(index, created + offset)
                    wait_until(created + 301)
                    trace.emit('age_observation', offset_seconds=301, **ocr_snapshot(executor))
                    wait_until(created + 307)
                    recognize(30, created + 307)
                    recognize(31, time.monotonic())
                else:
                    maximum = args.calls if args.protocol == 'calls' else min(args.calls, int(args.seconds / args.period))
                    for index in range(maximum):
                        target = origin + index * args.period
                        wait_until(target)
                        recognize(index, target)
                        if args.stop_on_high_tail and observer.target_captured.is_set():
                            break
                    if args.protocol == 'continuous' and not args.diagnostics:
                        wait_until(origin + args.seconds)
                trace.emit('load_completed', calls=count,
                           stop_reason='growth and high unloaded tail captured' if observer.target_captured.is_set()
                           else 'protocol bound', **ocr_snapshot(executor))

            submitted = executor.submit(job, name=f'OCR {args.protocol} protocol')
            submitted.future.result(timeout=args.seconds + 120)
            trace.phase = 'cooldown'
            trace.emit('cooldown_start', **ocr_snapshot(executor))
            deadline = time.monotonic() + args.cooldown
            observer.set_deadline(deadline)
            wait_until(deadline)
            exit_event.set()
            executor.thread.join(5)
            monitor = getattr(executor, '_ocr_idle_thread', None)
            if monitor is not None:
                monitor.join(5)
            trace.emit('shutdown', monitor_alive=monitor.is_alive() if monitor else False,
                       executor_alive=executor.thread.is_alive())
            complete_recording(trace, observer, count, deadline,
                               producers_stopped=not executor.thread.is_alive() and not (monitor and monitor.is_alive()))
    finally:
        exit_event.set()
        if not observer.closing:
            observer.finish(time.monotonic())
        PredictBase._run_openvino_async = original_infer
        TaskExecutor._create_ocr_lib = original_factory
        logging.getLogger('ok').removeHandler(handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('executor', 'gui', 'service'), default='executor')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=70)
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--trace-inference', action='store_true')
    parser.add_argument('--diagnostics', action='store_true', help='Opt in to scalar budget and bounded weak-object diagnostics')
    parser.add_argument('--stop-on-high-tail', action='store_true', help='Diagnostic only: stop after growth and a tail 128 MiB above the first unloaded tail')
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--protocol', choices=('calls', 'age', 'continuous'))
    parser.add_argument('--manifest', type=Path, default=ROOT / 'scripts/ocr_fixed_inputs.json')
    parser.add_argument('--calls', type=int, default=10000)
    parser.add_argument('--period', type=float, default=1)
    parser.add_argument('--cooldown', type=float, default=0)
    parser.add_argument('--max-private-mib', type=float, default=4096)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    if (args.diagnostics or args.stop_on_high_tail) and not args.protocol:
        parser.error('Diagnostics require a natural executor protocol')
    # Resource discovery applies to file protocols as well as GUI/service modes.
    sys.argv = [str(ROOT / 'main.py')]
    if args.protocol:
        if args.diagnostics:
            os.environ['OKWW_OCR_DIAGNOSTICS'] = '1'
        if args.stop_on_high_tail and not args.diagnostics:
            parser.error('--stop-on-high-tail requires --diagnostics')
        if args.legacy or args.mode != 'executor':
            parser.error('Natural protocols require the current executor implementation')
        if not 1 <= args.calls <= 10000 or args.period <= 0 or args.cooldown < 0 or args.max_private_mib <= 0:
            parser.error('Natural protocol limits must be positive; cooldown may be zero')
        return natural_protocol(args)
    if args.legacy:
        import subprocess
        import types
        source = subprocess.check_output(['git', 'show', 'c6e4cfb:src/ocr_memory.py'], cwd=ROOT, text=True)
        module = types.ModuleType('src.ocr_memory')
        exec(compile(source, 'c6e4cfb:src/ocr_memory.py', 'exec'), module.__dict__)
        sys.modules['src.ocr_memory'] = module
    from src.ocr_memory import install_ocr_idle_release
    install_ocr_idle_release()
    from ok.task.TaskExecutor import TaskExecutor
    from ok.notification.ppocr import NotificationPPOCR

    options = {'lib': 'onnxocr', 'params': {'use_openvino': True, 'use_npu': False}}
    if args.config:
        options.update(json.loads(args.config.read_text(encoding='utf-8')))
    trace = Recorder(args.output)
    runtime = None

    class LifecycleLog(logging.Handler):
        def emit(self, record):
            if 'ocr_memory:' in record.getMessage():
                trace.emit('lifecycle', message=record.getMessage())

    handler = LifecycleLog()
    logging.getLogger('ok').addHandler(handler)
    exit_event = threading.Event()
    release_job = threading.Event()
    try:
        with trace.sampling(1), backend_trace(trace, args.trace_inference):
            env = environment()
            env.update(mcp=args.mode == 'service', notifications=args.exercise)
            trace.emit('environment', **env, config=options, mode=args.mode, legacy=args.legacy,
                       model_files=model_files(options.get('params')))
            if args.mode in ('gui', 'service'):
                from config import config
                from ok import OK
                from PySide6.QtCore import QTimer

                app_config = deepcopy(config)
                app_config.update(ocr=options, check_mutex=False, custom_tasks=False, plugin=False,
                                  config_folder=str(args.output.parent / (args.output.stem + '-configs')),
                                  mcp={'enabled': False})
                if args.mode == 'service':
                    app_config.update(gui=None, use_gui=False, mcp={
                        'enabled': True, 'port': 0, 'retention_days': 0,
                        'artifact_directory': str(args.output.parent / 'mcp')})
                # Isolate saved settings and scheduling; this run only observes startup.
                app_config['trigger_tasks'] = ([['src.task.MouseResetTask', 'MouseResetTask']]
                                               if args.mode == 'service' else [])
                sys.argv = [str(ROOT / 'main.py')]
                runtime = OK(app_config)
                # OK configures its logging handlers during construction.
                logging.getLogger('ok').addHandler(handler)
                executor = runtime.task_executor
                if args.mode == 'gui':
                    QTimer.singleShot(int(args.seconds * 1000), runtime.quit)
                else:
                    def deadline():
                        if not runtime.exit_event.wait(args.seconds):
                            runtime.quit()
                    threading.Thread(target=deadline, name='BenchmarkDeadline', daemon=True).start()
                if args.exercise:
                    notification = NotificationPPOCR(executor.ocr_lib)
                    first_notification_done = threading.Event()

                    def recognize():
                        import cv2
                        frame = cv2.imread(str(ROOT / 'tests/images/find_confirm.png'))
                        started = time.perf_counter()
                        result = notification.recognize(frame)
                        trace.emit('notification', seconds=time.perf_counter() - started, result=result)
                        first_notification_done.set()

                    first_at = min(10, args.seconds / 4)
                    QTimer.singleShot(int(first_at * 1000), lambda: executor.submit(recognize, name='OCR notification'))
                    reload_timer = QTimer()

                    def reload_after_release():
                        if not first_notification_done.is_set():
                            return
                        with executor._ocr_lib_lock:
                            proxy = executor._ocr_lib.get('default')
                        if proxy is not None and hasattr(proxy, 'snapshot') and not proxy.snapshot()['loaded']:
                            reload_timer.stop()
                            executor.submit(recognize, name='OCR notification reload')

                    reload_timer.timeout.connect(reload_after_release)
                    reload_timer.start(1000)
            else:
                class GlobalConfig:
                    def get_config(self, option):
                        return {}

                executor = TaskExecutor(SimpleNamespace(interaction=None), exit_event=exit_event,
                                        global_config=GlobalConfig(), config={'ocr': options})

            def snapshot():
                from src import ocr_memory
                if hasattr(ocr_memory, 'ocr_snapshot'):
                    result = ocr_memory.ocr_snapshot(executor)
                else:
                    result = {'models': [{'name': name, 'loaded': proxy._model is not None,
                                          'active_calls': proxy._active, 'calls': proxy._calls}
                                         for name, proxy in executor._ocr_lib.items()]}
                if runtime is not None:
                    from ok import og
                    capture = executor.method
                    result['capture_backend'] = type(capture).__name__ if capture is not None else None
                    result['capture_size'] = [getattr(capture, 'width', None), getattr(capture, 'height', None)]
                    globals_instance = og.my_app
                    if hasattr(globals_instance, 'yolo_snapshot'):
                        result['yolo'] = globals_instance.yolo_snapshot()
                return result

            trace.snapshot = snapshot
            trace.phase = 'startup'
            if runtime is not None:
                try:
                    runtime.start()
                except SystemExit as error:
                    if error.code not in (None, 0):
                        raise
            else:
                until = time.monotonic() + args.seconds
                exercised = False
                while time.monotonic() < until:
                    if args.exercise and not exercised and time.monotonic() >= until - args.seconds / 2:
                        import cv2
                        frame = cv2.imread(str(ROOT / 'tests/images/find_confirm.png'))
                        notification = NotificationPPOCR(executor.ocr_lib)
                        started = time.perf_counter()
                        result = notification.recognize(frame)
                        trace.emit('notification', seconds=time.perf_counter() - started, result=result)
                        frame = result = None
                        # Hold real scheduler ownership without further OCR, so
                        # ocr_idle can be checked independently of task activity.
                        executor.submit(lambda: release_job.wait(args.seconds), name='non-OCR wait')
                        exercised = True
                    trace.stop.wait(.1)
                if args.exercise and exercised:
                    import cv2
                    trace.emit('notification_reload', result=notification.recognize(
                        cv2.imread(str(ROOT / 'tests/images/find_confirm.png'))))
            trace.emit('completed')
            trace.snapshot = lambda: {}
            release_job.set()
            exit_event.set()
            executor.exit_event.set()
            if executor.thread is not None:
                executor.thread.join(5)
            monitor = getattr(executor, '_ocr_idle_thread', None)
            if monitor is not None:
                monitor.join(5)
    finally:
        release_job.set()
        exit_event.set()
        logging.getLogger('ok').removeHandler(handler)


if __name__ == '__main__':
    main()
