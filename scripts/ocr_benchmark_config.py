"""Isolated CPU-only application configuration for paired MCP experiments."""

from copy import deepcopy
import json
import os
from pathlib import Path

from config import config as application_config

config = deepcopy(application_config)
config.update(check_mutex=False, custom_tasks=False, plugin=False,
              config_folder='logs/ocr-memory/application-configs', debug=False,
              mcp={'enabled': True, 'port': 0, 'artifact_directory': 'logs/ocr-memory/mcp'})
filename = os.environ.get('OCR_BENCHMARK_CONFIG')
if filename:
    options = json.loads(Path(filename).read_text(encoding='utf-8'))
    params = options.pop('params', {})
    config['ocr'].update(options)
    config['ocr']['params'].update(params)
config['ocr']['params'].update(use_openvino=True, use_npu=False)
config['trigger_tasks'] = [['src.task.MouseResetTask', 'MouseResetTask']]
