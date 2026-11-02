"""Partition all Python test modules while retaining one process per module."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone


SUMMARY = re.compile(r'^Ran (\d+) tests? in ([\d.]+)s$')
SKIPPED = re.compile(r'^(?:OK|FAILED) \(.*\bskipped=(\d+)\)')
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def discover_modules(test_directory):
    modules = sorted(
        (file for file in test_directory.iterdir()
         if file.is_file() and file.suffix.casefold() == '.py'),
        key=lambda file: (file.name.casefold(), file.name),
    )
    if not modules:
        raise ValueError(f'No Python test modules found in {test_directory}')
    return modules


def select_modules(modules, shard_index, shard_count):
    valid_shard = shard_count > 0 and 0 <= shard_index < shard_count
    if not valid_shard:
        raise ValueError('Require shard-count > 0 and 0 <= shard-index < shard-count')
    selected = modules[shard_index::shard_count]
    if not selected:
        raise ValueError('This shard contains no test modules')
    return selected


def run_module(project_root, module, output):
    started = time.perf_counter()
    tests_run = None
    test_seconds = None
    skipped = 0
    # Qt, OCR and SDK globals are intentionally isolated in a fresh interpreter;
    # sharding happens across runners, never concurrently in this working tree.
    process = subprocess.Popen(
        [sys.executable, '-u', str(project_root / 'run_test.py'), str(module)],
        cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
    )
    try:
        for line in process.stdout:
            print(line, end='', file=output, flush=True)
            summary = SUMMARY.fullmatch(line.strip())
            if summary:
                tests_run = int(summary[1])
                test_seconds = float(summary[2])
            skip_summary = SKIPPED.search(line)
            if skip_summary:
                skipped = int(skip_summary[1])
    finally:
        process.stdout.close()
        return_code = process.wait()
    has_tests = tests_run is not None and tests_run > 0
    succeeded = return_code == 0 and has_tests
    return {
        'module': module.relative_to(project_root).as_posix(),
        'exitCode': return_code, 'passed': succeeded, 'testsRun': tests_run,
        'skipped': skipped, 'testSeconds': test_seconds,
        'wallSeconds': round(time.perf_counter() - started, 3),
    }


def run_suite(project_root, shard_index=0, shard_count=1, output=None):
    project_root = Path(project_root).resolve()
    output = output if output is not None else sys.stdout
    modules = discover_modules(project_root / 'tests')
    selected = select_modules(modules, shard_index, shard_count)
    report = {
        'schema': 1, 'sourceSha': os.environ.get('GITHUB_SHA'),
        'runId': os.environ.get('GITHUB_RUN_ID'),
        'python': platform.python_version(), 'platform': platform.platform(),
        'runnerImage': {'os': os.environ.get('ImageOS'), 'version': os.environ.get('ImageVersion')},
        'startedAt': datetime.now(timezone.utc).isoformat(),
        'shardIndex': shard_index, 'shardCount': shard_count,
        'allModules': {
            module.relative_to(project_root).as_posix(): hashlib.sha256(module.read_bytes()).hexdigest()
            for module in modules
        },
        'selectedModules': [module.relative_to(project_root).as_posix() for module in selected],
        'modules': [],
    }
    started = time.perf_counter()
    print(f'Shard {shard_index + 1}/{shard_count}: {len(selected)}/{len(modules)} modules',
          file=output, flush=True)
    for module in selected:
        print(f'Running tests in {module}', file=output, flush=True)
        result = run_module(project_root, module, output)
        report['modules'].append(result)
        if not result['passed']:
            print(f"FAILED: {result['module']} (exit={result['exitCode']}, tests={result['testsRun']})",
                  file=output, flush=True)
    # A failed module does not hide the remaining modules; the final exit status
    # still fails the entire shard and therefore the validate dependency.
    report['passed'] = all(module['passed'] for module in report['modules'])
    report['testsRun'] = sum(module['testsRun'] or 0 for module in report['modules'])
    report['skipped'] = sum(module['skipped'] for module in report['modules'])
    report['wallSeconds'] = round(time.perf_counter() - started, 3)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_suite(PROJECT_ROOT, args.shard_index, args.shard_count)
        filename = args.report or PROJECT_ROOT / f'build/python-validation/shard-{args.shard_index}.json'
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(f"Shard {args.shard_index + 1}/{args.shard_count}: {report['testsRun']} tests, "
              f"{report['wallSeconds']}s, passed={report['passed']}", flush=True)
        summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary_file:
            with open(summary_file, 'a', encoding='utf-8') as summary:
                summary.write(f"\n### Python tests: shard {args.shard_index + 1}/{args.shard_count}\n\n"
                              f"{len(report['modules'])} modules, {report['testsRun']} tests, "
                              f"{report['skipped']} skipped, {report['wallSeconds']}s; passed={report['passed']}.\n")
        return 0 if report['passed'] else 1
    except (OSError, ValueError) as error:
        print(f'Python validation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
