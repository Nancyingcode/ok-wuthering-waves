"""Exercise real Qt TaskCard buttons, scheduler, OCR and cached notifications."""

import argparse
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

from benchmark_ocr import Recorder, ROOT, environment, model_files
from benchmark_ocr_runtime import backend_trace
sys.argv[0] = str(ROOT / 'main.py')
from ok import BaseTask


class GuiOcrProbe(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = 'OCR lifecycle GUI probe'
        self.description = 'Finite local OCR verification; no game input'

    def run(self):
        self.probe_entered.set()
        self.info['Recognitions'] = 0
        for index in range(6):
            result = self.notification.recognize(self.probe_frame)
            assert any(item[0] == '500' for item in result), result
            self.info['Recognitions'] = index + 1
            self.sleep(1)
        self.probe_completed += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Complete OCR object')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--skip-farm', action='store_true', help='Probe only; does not verify FarmEchoTask GUI completion')
    parser.add_argument('--farm-controls', action='store_true', help='Pause/resume and stop the real FarmEchoTask card')
    args = parser.parse_args()
    if args.skip_farm and args.farm_controls:
        parser.error('--skip-farm and --farm-controls are mutually exclusive')
    os.environ.setdefault('ONEDNN_MAX_CPU_ISA', 'AVX2')
    from config import config
    from ok import OK, og
    from ok.notification.ppocr import NotificationPPOCR
    from ok.task.exceptions import FinishedException
    from ok.ui.qt.tasks.TaskCard import TaskCard
    from src.ocr_memory import ocr_snapshot
    from src.task.FarmEchoTask import FarmEchoTask
    from PySide6.QtCore import QTimer
    import cv2

    options = json.loads(args.config.read_text(encoding='utf-8'))
    app_config = deepcopy(config)
    app_config.update(ocr=options, check_mutex=False, custom_tasks=False, plugin=False,
                      trigger_tasks=[['src.task.MouseResetTask', 'MouseResetTask']], mcp={'enabled': False},
                      config_folder=str(args.output.parent / (args.output.stem + '-configs')))
    app_config['onetime_tasks'].append(['scripts.benchmark_ocr_gui', 'GuiOcrProbe'])
    trace = Recorder(args.output)
    state = {'stage': 'startup', 'outcomes': {}, 'heartbeat_max_gap': 0, 'heartbeat_by_stage': {}}
    runtime = None

    class LifecycleLog(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if 'ocr_memory:' in message:
                trace.emit('lifecycle', message=message)

    handler = LifecycleLog()
    sys.argv = [sys.argv[0]]
    with trace.sampling(1), backend_trace(trace, True):
        env = environment()
        env.update(capture_backend='application runtime; see scalar samples', notifications=True)
        trace.emit('environment', **env, options=options, model_files=model_files(options.get('params')),
                   protocol=('Farm task card pause/resume, completion and early cancellation' if args.farm_controls
                             else 'Qt buttons; Farm completion then independent pause/resume/cancel probe'))
        runtime = OK(app_config)
        logging.getLogger('ok').addHandler(handler)
        executor = runtime.task_executor

        def snapshot():
            capture = executor.method
            return {**ocr_snapshot(executor), 'yolo': og.my_app.yolo_snapshot(),
                    'capture_backend': type(capture).__name__ if capture is not None else None,
                    'capture_size': [getattr(capture, 'width', None), getattr(capture, 'height', None)]}

        trace.snapshot = snapshot
        probe = next(task for task in executor.onetime_tasks if task.__class__.__name__ == 'GuiOcrProbe')
        farm = next(task for task in executor.onetime_tasks if isinstance(task, FarmEchoTask))
        farm.config.update({'Teleport to Boss': 'Boss Challenge', 'Which Boss Challenge to Teleport': 1,
                            'Boss': 'Other', 'Boss Level': '50', 'Repeat Farm Count': 1,
                            'Echo Pickup Method': 'Walk', 'Use Liberation': True,
                            'Switch to Healer before and after Combat': True})
        farm_entered = threading.Event()
        if args.farm_controls:
            original_farm_run = farm.run

            def entered_farm(*values, **keywords):
                farm_entered.set()
                return original_farm_run(*values, **keywords)

            farm.run = entered_farm
        frame = cv2.imread(str(ROOT / 'tests/images/tacet_elimination_final.jpg'))
        h, w = frame.shape[:2]
        frame = frame[int(.6*h):int(.96*h), int(.3*w):int(.7*w)].copy()
        notification = NotificationPPOCR(executor.ocr_lib)
        probe.notification, probe.probe_frame = notification, frame
        probe.probe_entered, probe.probe_completed = threading.Event(), 0
        started = time.monotonic()
        last_tick = started
        stage_at = started
        active_card = None
        notification_proxy = None
        reload_job = None

        def transition(stage, **fields):
            nonlocal stage_at
            state['stage'] = trace.phase = stage
            stage_at = time.monotonic()
            trace.emit('gui_transition', stage=stage, **fields)
            if og.main_window is not None:
                og.main_window.grab().save(str(args.output.parent / f'{args.output.stem}-{stage}.png'))

        def card_for(task):
            return next(card for card in og.main_window.findChildren(TaskCard) if card.task is task)

        def tick():
            nonlocal active_card, notification_proxy, reload_job, last_tick
            now = time.monotonic()
            state['heartbeat_max_gap'] = max(state['heartbeat_max_gap'], now - last_tick)
            stage = state['stage']
            state['heartbeat_by_stage'][stage] = max(state['heartbeat_by_stage'].get(stage, 0), now - last_tick)
            last_tick = now
            try:
                if now - started > args.seconds:
                    raise TimeoutError(f'GUI protocol deadline at {state["stage"]}')
                stage = state['stage']
                if stage == 'startup' and now - started >= 5:
                    assert not executor._ocr_lib, 'Candidate unexpectedly preloaded OCR'
                    task = probe if args.skip_farm else farm
                    active_card = card_for(task)
                    assert not active_card.start_button.isHidden()
                    active_card.start_button.click()
                    transition('start_clicked', task=task.__class__.__name__)
                elif stage == 'start_clicked':
                    if active_card.task.running:
                        if args.farm_controls:
                            active_card.pause_button.click()
                            assert farm.paused
                            state['paused_farm_count'] = farm.info.get('Combat Count', 0)
                            transition('farm_paused', **ocr_snapshot(executor))
                        else:
                            transition('running', task=active_card.task.__class__.__name__, **ocr_snapshot(executor))
                    elif now - stage_at > 45:
                        raise TimeoutError('GUI Start did not enter the task')
                elif stage == 'farm_paused' and now - stage_at >= 2:
                    assert farm.paused and farm.info.get('Combat Count', 0) == state['paused_farm_count']
                    state['outcomes']['paused_farm'] = {'paused': farm.paused, 'info': dict(farm.info)}
                    active_card.start_button.click()
                    assert not farm.paused
                    transition('running', task='FarmEchoTask', resumed=True, **ocr_snapshot(executor))
                elif stage == 'running' and not active_card.task.enabled and executor.current_task is None:
                    if not args.skip_farm:
                        assert farm.info.get('Combat Count', 0) >= 1, dict(farm.info)
                        assert farm.info.get('Echo Count', 0) >= 1, dict(farm.info)
                    else:
                        assert probe.probe_completed == 1
                    state['outcomes']['normal_completion'] = dict(active_card.task.info)
                    if args.farm_controls:
                        state['outcomes']['pause_resume_completed'] = True
                        reload_job = executor.submit(lambda: notification.recognize(frame), name='GUI notification seed')
                        transition('farm_notification_seed')
                    else:
                        probe.probe_entered.clear()
                        active_card = card_for(probe)
                        active_card.start_button.click()
                        transition('pause_run_started')
                elif stage == 'farm_notification_seed' and reload_job.future.done():
                    assert any(item[0] == '500' for item in reload_job.future.result())
                    farm_entered.clear()
                    active_card = card_for(farm)
                    active_card.start_button.click()
                    transition('farm_cancel_run_started')
                elif stage == 'farm_cancel_run_started' and farm_entered.is_set():
                    active_card.stop_button.click()
                    transition('stop_clicked', task='FarmEchoTask')
                elif stage == 'pause_run_started' and probe.probe_entered.is_set():
                    active_card.pause_button.click()
                    assert probe.paused
                    state['paused_calls'] = probe.info.get('Recognitions', 0)
                    state.pop('pause_settled_calls', None)
                    transition('paused', info=dict(probe.info))
                elif stage == 'paused' and now - stage_at >= 1:
                    # One inference may finish at the pause boundary; no native force-cancel.
                    if 'pause_settled_calls' not in state:
                        state['pause_settled_calls'] = probe.info.get('Recognitions', 0)
                    if now - stage_at >= 2:
                        assert probe.info.get('Recognitions', 0) == state['pause_settled_calls']
                        assert not active_card.start_button.isHidden()
                        state['outcomes']['paused'] = {'paused': probe.paused, 'info': dict(probe.info)}
                        active_card.start_button.click()
                        assert not probe.paused
                        transition('resumed')
                elif stage == 'resumed' and not probe.enabled and executor.current_task is None:
                    expected = 2 if args.skip_farm else 1
                    assert probe.probe_completed == expected
                    state['outcomes']['pause_resume_completed'] = True
                    probe.probe_entered.clear()
                    active_card.start_button.click()
                    transition('cancel_run_started')
                elif stage == 'cancel_run_started' and probe.probe_entered.is_set():
                    state['completed_before_cancel'] = probe.probe_completed
                    active_card.stop_button.click()
                    transition('stop_clicked')
                elif stage == 'stop_clicked' and not active_card.task.enabled and executor.current_task is None:
                    if args.farm_controls:
                        assert farm.info.get('Combat Count', 0) == 0, 'Cancellation reached another combat'
                    else:
                        assert probe.probe_completed == state['completed_before_cancel']
                    state['outcomes']['cooperative_cancel'] = True
                    notification_proxy = notification._instance
                    assert notification_proxy is executor.ocr_lib()
                    transition('waiting_for_idle')
                elif stage == 'waiting_for_idle' and not notification_proxy.snapshot()['loaded']:
                    state['outcomes']['idle_release'] = notification_proxy.snapshot()
                    reload_job = executor.submit(lambda: notification.recognize(frame), name='GUI notification reload')
                    transition('notification_reload_started')
                elif stage == 'notification_reload_started' and reload_job.future.done():
                    result = reload_job.future.result()
                    assert any(item[0] == '500' for item in result)
                    assert notification._instance is notification_proxy
                    state['outcomes']['notification_reload'] = {'result': result, 'snapshot': notification_proxy.snapshot()}
                    transition('completed')
                    timer.stop()
                    runtime.quit()
            except BaseException as error:
                state['failure'] = repr(error)
                trace.emit('failed', error=repr(error), **ocr_snapshot(executor))
                timer.stop()
                runtime.quit()

        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(100)
        try:
            runtime.start()
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        finally:
            executor.exit_event.set()
            monitor = getattr(executor, '_ocr_idle_thread', None)
            if monitor is not None:
                monitor.join(5)
            state['outcomes']['monitor_stopped'] = monitor is None or not monitor.is_alive()
            if notification_proxy is not None:
                generation = notification_proxy.snapshot()['generation']
                try:
                    notification.recognize(frame)
                except FinishedException:
                    state['outcomes']['post_exit_call_rejected'] = True
                state['outcomes']['no_post_exit_creation'] = notification_proxy.snapshot()['generation'] == generation
            trace.emit('shutdown', **state)
            trace.snapshot = lambda: {}
            logging.getLogger('ok').removeHandler(handler)
    args.output.with_suffix('.result.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    if 'failure' in state or state['stage'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
