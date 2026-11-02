"""Thin MCP harness for one registered FarmEchoTask run with streamed diagnostics."""

import json
import os
import time
import cv2

from ok import BaseTask, og
from onnxocr.onnx_paddleocr import ONNXPaddleOcr
from onnxocr.predict_base import PredictBase
from scripts.benchmark_ocr import Recorder, ROOT, digest, environment, model_files
from src.ocr_memory import ocr_snapshot
from src.task.FarmEchoTask import FarmEchoTask


class Task(BaseTask):
    def reset_to_start(self):
        self.farm = self.get_task_by_class(FarmEchoTask)
        self.farm.ensure_main(time_out=15)
        self.farm.teleport_to_heal()
        self.sleep(2)
        self.yolo_state = os.environ.get('OCR_BENCHMARK_YOLO_STATE')
        self.yolo_prepare_seconds = None
        if self.yolo_state == 'loaded':
            started = time.perf_counter()
            og.my_app.yolo_detect(self.frame, threshold=.3)
            self.yolo_prepare_seconds = time.perf_counter() - started
            assert og.my_app.yolo_snapshot()['loaded']
        elif self.yolo_state == 'unloaded':
            assert not og.my_app.yolo_snapshot()['loaded']
        elif self.yolo_state is not None:
            raise ValueError('OCR_BENCHMARK_YOLO_STATE must be loaded or unloaded')

    def verify_start_state(self):
        assert self.farm.in_team_and_world(), 'Healing recovery did not reach the open world'
        cv2.imwrite(str(self.artifact_directory / 'healed-start.png'), self.frame)

    def run(self):
        farm = self.farm
        options = {'Teleport to Boss': 'Boss Challenge', 'Which Boss Challenge to Teleport': 1,
                   'Boss': 'Other', 'Boss Level': '50', 'Repeat Farm Count': 1,
                   'Echo Pickup Method': 'Walk', 'Use Liberation': True,
                   'Switch to Healer before and after Combat': True}
        previous = {key: farm.config.get(key) for key in options}
        farm.config.update(options)
        farm.info.clear()
        was_enabled, was_running = farm._enabled, farm.running
        farm._enabled, farm.running = True, True
        self.executor.current_task = farm
        trace = Recorder(self.artifact_directory / 'memory.jsonl')
        trace.snapshot = lambda: {**ocr_snapshot(self.executor), 'yolo': og.my_app.yolo_snapshot()}
        old_infer = PredictBase._run_openvino_async
        old_ocr = ONNXPaddleOcr.ocr

        def infer(predictor, feed):
            started = time.perf_counter()
            outputs = old_infer(predictor, feed)
            trace.emit('native_inference', model=predictor.model_dir, shapes=[list(v.shape) for v in feed.values()],
                       output_nbytes=sum(v.nbytes for v in outputs), seconds=time.perf_counter() - started)
            return outputs

        def ocr(model, image, *args, **kwargs):
            started = time.perf_counter()
            result = old_ocr(model, image, *args, **kwargs)
            trace.emit('recognition', label='FarmEchoTask.ocr', image_shape=list(image.shape),
                       seconds=time.perf_counter() - started, result=result)
            return result

        PredictBase._run_openvino_async, ONNXPaddleOcr.ocr = infer, ocr
        try:
            with trace.sampling(1):
                runtime_environment = environment()
                runtime_environment.update(mcp=True, notifications=False,
                                           capture_backend=type(self.executor.method).__name__)
                trace.emit('environment', **runtime_environment, task_options=options,
                           capture_shape=list(self.frame.shape),
                           yolo_initial=og.my_app.yolo_snapshot(), yolo_protocol=self.yolo_state,
                           yolo_prepare_seconds=self.yolo_prepare_seconds,
                           model_files=model_files(self.executor.config['ocr']['default'].get('params')),
                           yolo_weights_sha256=digest(ROOT / 'assets/echo_model/echo.onnx'),
                           ocr_config={k:v for k,v in self.executor.config['ocr']['default'].items() if k != 'default'},
                           notification_queue='fresh isolated settings', mcp_enabled=True,
                           yolo_fixed_backend='OpenVINO, production device selection')
                trace.phase = 'boss_task'
                started = time.perf_counter()
                farm.run(retries=0)
                seconds = time.perf_counter() - started
                self.next_frame()
                cv2.imwrite(str(self.artifact_directory / 'boss-finished.png'), self.frame)
                assert farm.info.get('Combat Count', 0) >= 1, 'No observed combat round'
                assert farm.info.get('Echo Count', 0) >= 1, 'No observed echo pickup'
                assert not farm.in_combat(), 'Task returned while still fighting'
                assert farm.in_team_and_world(), 'Task did not return to an observable world state'
                team = [char.__class__.__name__ if char is not None else None for char in farm.chars]
                trace.emit('task_completed', seconds=seconds, info=dict(farm.info), team=team)
                trace.phase = 'task_idle'
                self.sleep(3)
                trace.emit('completed')
                (self.artifact_directory / 'boss-result.json').write_text(
                    json.dumps({'seconds':seconds, 'info':dict(farm.info), 'team':team}, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            PredictBase._run_openvino_async, ONNXPaddleOcr.ocr = old_infer, old_ocr
            self.executor.current_task = self
            farm._enabled, farm.running = was_enabled, was_running
            farm.config.update(previous)
