"""Run the existing FarmEchoTask MCP harness in six fresh, isolated runtimes."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from benchmark_ocr import ROOT, digest


async def run_pair_member(args, label, ordinal, options):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from ok.core.mcp_server import discover_mcp_servers

    directory = args.output / f'{ordinal:02d}-{label}'
    directory.mkdir(parents=True, exist_ok=False)
    config_path = directory / 'ocr.json'
    config_path.write_text(json.dumps(options, ensure_ascii=False, indent=2), encoding='utf-8')
    command = [sys.executable, str(ROOT / 'scripts/benchmark_ocr_runtime.py'),
               '--mode', 'service', '--config', str(config_path),
               '--trace-inference',
               '--seconds', str(args.timeout), '--output', str(directory / 'runtime.jsonl')]
    metadata = {'label': label, 'ordinal': ordinal, 'command': command,
                'start_utc': datetime.now(timezone.utc).isoformat(),
                'protocol': {'order': args.order, 'post_task_seconds': args.post_task,
                             'yolo_initial_state': args.yolo_state,
                             'task_boundary': 'FarmEchoTask.run, recovery excluded',
                             'invalid_run_rule': 'retain and stop; no automatic replacement'},
                'config_sha256': digest(config_path)}
    with (directory / 'runtime.log').open('x', encoding='utf-8') as log:
        child_env = dict(os.environ, OCR_BENCHMARK_YOLO_STATE=args.yolo_state)
        process = subprocess.Popen(command, cwd=ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 45
            servers = []
            while time.monotonic() < deadline and process.poll() is None:
                servers = discover_mcp_servers(ROOT, artifact_directory=directory / 'mcp')
                if servers:
                    break
                await asyncio.sleep(.5)
            if len(servers) != 1:
                raise RuntimeError('Owned server did not become ready; inspect runtime.log')
            server = servers[0]
            metadata['server_pid'] = server['pid']
            if Path(server['project_directory']).resolve() != ROOT:
                raise RuntimeError('Discovered server belongs to another project')
            async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + server['token']},
                                          trust_env=False, timeout=45) as client:
                async with streamable_http_client(server['url'], http_client=client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()

                        async def call(name, arguments=None):
                            result = (await session.call_tool(name, arguments or {})).model_dump(mode='json')
                            if result.get('is_error'):
                                raise RuntimeError(f'MCP {name} failed: {result}')
                            value = result.get('structured_content')
                            if value is None:
                                value = json.loads(result['content'][0]['text'])
                            return value['result'] if isinstance(value, dict) and set(value) == {'result'} else value

                        skills = await call('list_skills')
                        (directory / 'skills.json').write_text(json.dumps(skills, ensure_ascii=False), encoding='utf-8')
                        skill = await call('get_skill', {'skill_id': 'develop'})
                        (directory / 'develop-skill.json').write_text(json.dumps(skill, ensure_ascii=False), encoding='utf-8')
                        tasks = await call('list_tasks')
                        (directory / 'tasks.json').write_text(json.dumps(tasks, ensure_ascii=False), encoding='utf-8')
                        metadata['task_submitted_utc'] = datetime.now(timezone.utc).isoformat()
                        submitted = await call('run_task', {'file_path': str(ROOT / 'scripts/benchmark_farm_echo.py')})
                        metadata['submitted'] = submitted
                        run_id = submitted['run_id']
                        deadline = time.monotonic() + args.timeout - 100
                        while time.monotonic() < deadline:
                            status = await call('get_run_status', {'run_id': run_id})
                            if status['state'] in ('succeeded', 'failed', 'cancelled'):
                                break
                            await asyncio.sleep(2)
                        else:
                            await call('stop_task', {'run_id': run_id})
                            raise TimeoutError('Task deadline exceeded; runtime deadline owns final cleanup')
                        metadata['status'] = status
                        print(json.dumps({'ordinal': ordinal, 'label': label, 'state': status['state'],
                                          'run_id': run_id}, ensure_ascii=False), flush=True)
                        if status['state'] != 'succeeded':
                            raise RuntimeError(f'Task {run_id} ended {status["state"]}')
                        metadata['post_task_start_utc'] = datetime.now(timezone.utc).isoformat()
                        await asyncio.sleep(args.post_task)
                        metadata['exit'] = await call('exit_process', {'reason': 'Completed owned OCR paired experiment'})
            await asyncio.to_thread(process.wait, 20)
            metadata['exit_code'] = process.returncode
        except BaseException as error:
            metadata['failure'] = repr(error)
            raise
        finally:
            metadata['end_utc'] = datetime.now(timezone.utc).isoformat()
            (directory / 'run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            # An incomplete attempt remains owned until the finite service deadline.
            # Do not kill a user GUI or launch another attempt over an active task.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--order', default='A,B,B,A,A,B')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--post-task', type=float, default=95)
    parser.add_argument('--yolo-state', choices=('loaded', 'unloaded'), default='loaded',
                        help='Prepare the same YOLO state before each formal task timer')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.timeout < 180 or args.post_task < 0 or set(args.order.split(',')) - {'A', 'B'}:
        parser.error('Use A/B labels, timeout >=180 and nonnegative post-task observation')
    from config import config
    baseline = deepcopy(config['ocr'])
    baseline['params'].update(use_openvino=True, use_npu=False, rec_batch_num=6,
                              openvino_num_requests=1, openvino_compile_config={'CPU': {}, 'NPU': {}})
    candidate = deepcopy(baseline)
    candidate.update(preload=False)
    candidate['lifecycle']['idle_policy'] = 'ocr_idle'
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('ONEDNN_MAX_CPU_ISA', 'AVX2')

    async def run():
        for ordinal, label in enumerate(args.order.split(','), 1):
            await run_pair_member(args, label, ordinal, baseline if label == 'A' else candidate)

    asyncio.run(run())


if __name__ == '__main__':
    main()
