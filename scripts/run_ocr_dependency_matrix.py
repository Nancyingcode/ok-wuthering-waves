"""Run the finite dependency/rollback matrix against an installed validation payload."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from benchmark_ocr import ROOT, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upstream', type=Path, default=ROOT / 'build/ocr-wheels/upstream')
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    python = app / '.runtime/Scripts/python.exe'
    if not python.is_file():
        parser.error('Install the payload with install-validation.ps1 first')
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, ONEDNN_MAX_CPU_ISA='AVX2', PYTHONIOENCODING='utf-8')
    temporary = ROOT / 'build/ocr-tmp'
    temporary.mkdir(exist_ok=True)
    env.update(TEMP=str(temporary), TMP=str(temporary))
    patched = [app / 'wheels' / name for name in
               ('ok_script-2.0.22+okww.ocr1-py3-none-any.whl', 'onnxocr_ppocrv5-0.0.22+okww.ocr1-py3-none-any.whl')]
    original = [args.upstream.resolve() / name for name in
                ('ok_script-2.0.22-py3-none-any.whl', 'onnxocr_ppocrv5-0.0.22-py3-none-any.whl')]
    steps = (output / 'steps.jsonl').open('x', encoding='utf-8')

    def run(name, arguments):
        started = time.monotonic()
        row = {'name': name, 'command': [str(python), *map(str, arguments)],
               'cwd': str(app), 'started_utc': datetime.now(timezone.utc).isoformat()}
        with (output / f'{name}.log').open('x', encoding='utf-8') as log:
            result = subprocess.run(row['command'], cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT)
        row.update(exit_code=result.returncode, seconds=time.monotonic() - started,
                   ended_utc=datetime.now(timezone.utc).isoformat())
        steps.write(json.dumps(row, ensure_ascii=False) + '\n')
        steps.flush()
        print(json.dumps({'name': name, 'exit_code': result.returncode, 'seconds': row['seconds']}), flush=True)
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect {output / (name + ".log")}')

    def case(name, mode, ok_version, onnx_version):
        run(name, [app / 'scripts/benchmark_ocr_dependencies.py', '--case', mode, '--production-npu',
                   '--expect-ok', ok_version, '--expect-onnx', onnx_version,
                   '--output', output / f'{name}.jsonl'])

    def install(name, wheels):
        run(name, ['-m', 'pip', 'install', '--no-index', '--no-deps', '--force-reinstall', *wheels])
        run(name + '-check', ['-m', 'pip', 'check'])

    try:
        provenance = {str(path): digest(path) for path in (*patched, *original)}
        (output / 'wheel-hashes.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
        case('patched-A', 'compat', '2.0.22+okww.ocr1', '0.0.22+okww.ocr1')
        case('patched-B', 'lazy', '2.0.22+okww.ocr1', '0.0.22+okww.ocr1')
        case('rollback-config', 'compat', '2.0.22+okww.ocr1', '0.0.22+okww.ocr1')
        install('install-original', original)
        case('original-A', 'compat', '2.0.22', '0.0.22')
        case('original-B', 'lazy', '2.0.22', '0.0.22')
        case('original-missing-factory', 'missing-factory', '2.0.22', '0.0.22')
        run('original-shapes', [app / 'scripts/benchmark_ocr.py', 'replay',
                               '--manifest', app / 'scripts/ocr_followup_inputs.json', '--repeats', '20',
                               '--cycles', '1', '--settle', '2', '--max-seconds', '300',
                               '--output', output / 'original-shapes.jsonl'])
        install('install-partial', patched[:1])
        case('partial-missing-compile', 'missing-compile', '2.0.22+okww.ocr1', '0.0.22')
        case('partial-unknown-parameter', 'unknown-parameter', '2.0.22+okww.ocr1', '0.0.22')
        install('restore-patched', patched)
        case('restored-patched-A', 'compat', '2.0.22+okww.ocr1', '0.0.22+okww.ocr1')
        install('rollback-dependencies-install', original)
        case('rollback-dependencies', 'compat', '2.0.22', '0.0.22')
        install('final-patched', patched)
        run('final-freeze', ['-m', 'pip', 'freeze'])
    finally:
        steps.close()


if __name__ == '__main__':
    main()
