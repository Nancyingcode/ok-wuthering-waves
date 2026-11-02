import gc
import os.path
import threading
import time

import cv2

from ok import Config, Logger, get_path_relative_to_exe, og
from ok.task.exceptions import FinishedException

logger = Logger.get_logger(__name__)


class Globals:

    def __init__(self, exit_event):
        self._yolo_model = None
        self._yolo_lock = threading.Condition(threading.Lock())
        self._yolo_active = 0
        self._yolo_releasing = False
        self._exit_event = exit_event
        self._yolo_last_used = None
        self.mini_map_arrow = None
        self.logged_in = False

    @property
    def yolo_model(self):
        if self._yolo_model is None:
            weights = get_path_relative_to_exe(os.path.join("assets", "echo_model", "echo.onnx"))
            ocr_config = og.config.get('ocr') or {}
            ocr_options = ocr_config.get('default') or ocr_config
            if (ocr_options.get('params') or {}).get('use_openvino'):
                logger.info("yolo_model Using OpenVinoYolo8Detect")
                from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
                self._yolo_model = OpenVinoYolo8Detect(
                    weights=weights)
            else:
                logger.info("yolo_model Using OnnxYolo8Detect")
                from src.OnnxYolo8Detect import OnnxYolo8Detect
                self._yolo_model = OnnxYolo8Detect(
                    weights=weights)
        return self._yolo_model

    def yolo_detect(self, image, threshold=0.6, label=-1):
        with self._yolo_lock:
            while self._yolo_releasing:
                if self._exit_event is not None and self._exit_event.is_set():
                    raise FinishedException()
                self._yolo_lock.wait(.1)
            if self._exit_event is not None and self._exit_event.is_set():
                raise FinishedException()
            model = self.yolo_model
            self._yolo_active += 1
        try:
            return model.detect(image, threshold=threshold, label=label)
        finally:
            model = None
            with self._yolo_lock:
                self._yolo_active -= 1
                self._yolo_last_used = time.monotonic()
                self._yolo_lock.notify_all()

    def release_yolo(self):
        """Explicit idle cleanup; callers must use yolo_detect to share ownership."""
        with self._yolo_lock:
            while self._yolo_releasing:
                self._yolo_lock.wait(.1)
            # Close admission before waiting so repeated detection cannot starve cleanup.
            self._yolo_releasing = True
        try:
            with self._yolo_lock:
                while self._yolo_active:
                    self._yolo_lock.wait(.1)
                model = self._yolo_model
                had_model = model is not None
                self._yolo_model = None
            model = None
            if had_model:
                gc.collect()
                logger.info('Released YOLO model; reload on next detection')
            return had_model
        finally:
            with self._yolo_lock:
                self._yolo_releasing = False
                self._yolo_lock.notify_all()

    def yolo_snapshot(self):
        with self._yolo_lock:
            model = self._yolo_model
            loaded = model is not None
            device = None
            error = None
            if model is not None:
                try:
                    if hasattr(model, 'compiled_model'):
                        device = str(model.compiled_model.get_property('EXECUTION_DEVICES'))
                    elif hasattr(model, 'session'):
                        device = model.session.get_providers()
                except Exception as exception:
                    error = str(exception)
            model = None
            return {'loaded': loaded, 'device': device, 'device_error': error,
                    'active_calls': self._yolo_active, 'releasing': self._yolo_releasing,
                    'idle_seconds': (None if self._yolo_last_used is None else
                                     time.monotonic() - self._yolo_last_used)}


if __name__ == "__main__":
    glbs = Globals(exit_event=None)
