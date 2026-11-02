import copy
import gc
import importlib.util
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from onnxocr.predict_base import PredictBase


PATCHED = importlib.util.find_spec('onnxocr.openvino_config') is not None


class RetainingQueue:
    def __init__(self):
        self.userdata = []
        self.buffer = np.ones((1, 4), dtype=np.float32)
        self.callback = PredictBase._on_openvino_complete

    def start_async(self, feed, userdata, share_inputs):
        self.userdata.append(userdata)
        self.buffer[:] = feed['input']
        self.callback(SimpleNamespace(output_tensors=[SimpleNamespace(data=self.buffer)]), userdata)


@unittest.skipUnless(PATCHED, 'Build and install scripts/build_ocr_wheels.py outputs first')
class TestOcrBackend(unittest.TestCase):
    def predictor(self):
        predictor = PredictBase.__new__(PredictBase)
        predictor._async_submit_lock = threading.Lock()
        predictor._async_queue = RetainingQueue()
        return predictor

    def test_consumed_outputs_are_not_retained_by_native_userdata(self):
        predictor = self.predictor()
        outputs = predictor._run_openvino_async({'input': 3})
        reference = weakref.ref(outputs[0])
        del outputs
        gc.collect()
        self.assertIsNone(reference())
        self.assertNotIn('outputs', predictor._async_queue.userdata[-1])

    def test_native_buffer_reuse_does_not_overwrite_previous_result(self):
        predictor = self.predictor()
        first = predictor._run_openvino_async({'input': 3})
        predictor._run_openvino_async({'input': 7})
        np.testing.assert_array_equal(first[0], np.full((1, 4), 3))

    def test_concurrent_calls_consume_only_their_own_job(self):
        predictor = self.predictor()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(predictor._run_openvino_async, {'input': 3})
            second = pool.submit(predictor._run_openvino_async, {'input': 7})
            self.assertEqual(first.result()[0][0, 0], 3)
            self.assertEqual(second.result()[0][0, 0], 7)
        self.assertTrue(all('outputs' not in job for job in predictor._async_queue.userdata))

    def test_callback_error_is_consumed_and_wakes_waiter(self):
        predictor = self.predictor()

        class BadRequest:
            @property
            def output_tensors(self):
                raise ValueError('bad native output')

        def submit(feed, userdata, share_inputs):
            predictor._async_queue.userdata.append(userdata)
            PredictBase._on_openvino_complete(BadRequest(), userdata)

        predictor._async_queue.start_async = submit
        with self.assertRaisesRegex(RuntimeError, 'asynchronous inference') as raised:
            predictor._run_openvino_async({'input': 1})
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        job = predictor._async_queue.userdata[-1]
        self.assertNotIn('error', job)
        self.assertNotIn('outputs', job)

    def test_factory_passes_parameters_to_all_enabled_predictors(self):
        from ok.task.TaskExecutor import TaskExecutor
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        node = SimpleNamespace(any_name='input', get_any_name=lambda: 'input')
        session = SimpleNamespace(inputs=[node], outputs=[node], get_property=lambda key: 1)
        core = Mock()
        core.read_model.return_value = SimpleNamespace(inputs=[node], reshape=Mock())
        core.compile_model.return_value = session
        queue = Mock()
        queue.__len__ = Mock(return_value=1)
        config = {'use_openvino': True, 'use_npu': False, 'use_angle_cls': True,
                  'rec_batch_num': 2, 'openvino_num_requests': 1,
                  'openvino_compile_config': {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0},
                                              'NPU': {'PERFORMANCE_HINT': 'LATENCY'}}}
        original = copy.deepcopy(config)
        executor = SimpleNamespace(config={'ocr': {'default': {'lib': 'onnxocr', 'params': config}}})
        with patch('openvino.Core', return_value=core), patch('openvino.AsyncInferQueue', return_value=queue):
            model = TaskExecutor._create_ocr_lib(executor, 'default')
        self.assertIsInstance(model, ONNXPaddleOcr)
        self.assertEqual(model.text_recognizer.rec_batch_num, 2)
        self.assertEqual(core.compile_model.call_count, 3)
        for call in core.compile_model.call_args_list:
            self.assertEqual(call.kwargs['config'], {'CPU_RUNTIME_CACHE_CAPACITY': 0})
            self.assertEqual(call.kwargs['device_name'], 'CPU')
        self.assertEqual(config, original)

    def test_unknown_or_mistyped_parameters_fail_before_loading(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        for params in ({'rec_batch_nmu': 2}, {'rec_batch_num': 0}, {'rec_batch_num': True},
                       {'use_npu': 'false'}, {'openvino_compile_config': {'GPU': {}}},
                       {'openvino_compile_config': {'NPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0}}}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                ONNXPaddleOcr(**params)

    def test_rejected_compile_property_is_not_silently_retried(self):
        from onnxocr.openvino_config import OpenVINOConfigError

        core = Mock()
        core.compile_model.side_effect = RuntimeError('Unsupported property')
        core.read_model.return_value = SimpleNamespace(inputs=[SimpleNamespace(any_name='input')], reshape=Mock())
        with patch('openvino.Core', return_value=core), self.assertRaises(OpenVINOConfigError):
            PredictBase('det.onnx', use_npu=False,
                        openvino_compile_config={'CPU': {'MISSPELLED_PROPERTY': 1}})
        self.assertEqual(core.compile_model.call_count, 1)

    def test_npu_failure_uses_an_independent_cpu_configuration(self):
        node = SimpleNamespace(any_name='input', get_partial_shape=lambda: [-1, 3, -1, -1])
        core = Mock()
        core.get_property.return_value = ['NPU']
        core.read_model.return_value = SimpleNamespace(inputs=[node], reshape=Mock())
        core.compile_model.side_effect = [RuntimeError('NPU unavailable during compile'),
                                          SimpleNamespace(get_property=lambda key: 'CPU')]
        queue = Mock()
        queue.__len__ = Mock(return_value=1)
        config = {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0}, 'NPU': {}}
        with patch('openvino.Core', return_value=core), patch('openvino.AsyncInferQueue', return_value=queue), \
                patch('onnxocr.check_npu.check_npu_driver_valid', return_value=True):
            predictor = PredictBase('det.onnx', use_npu=True, openvino_compile_config=config)
        self.assertEqual(core.compile_model.call_args_list[0].kwargs['config'], {})
        self.assertEqual(core.compile_model.call_args_list[1].kwargs['config'], {'CPU_RUNTIME_CACHE_CAPACITY': 0})
        self.assertEqual(config, {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0}, 'NPU': {}})
        diagnostic = predictor.compile_diagnostics[-1]
        self.assertEqual(diagnostic['requested_device'], 'NPU')
        self.assertEqual(diagnostic['actual_device'], 'CPU')
        self.assertIn('NPU unavailable', diagnostic['fallback_reason'])


if __name__ == '__main__':
    unittest.main()
