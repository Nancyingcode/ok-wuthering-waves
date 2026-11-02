"""Verify patched dependencies before the existing release inlining step."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import zipfile


PACKAGES = [('ok-script', 'ok_script', '2.0.22+okww.ocr1', 'ok'),
            ('onnxocr-ppocrv5', 'onnxocr_ppocrv5', '0.0.22+okww.ocr1', 'onnxocr')]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_tree(package_root, expected, exact=True):
    if exact:
        actual = set()
        directories = {parent.as_posix() for name in expected for parent in PurePosixPath(name).parents
                       if parent != PurePosixPath('.')}
        for path in package_root.rglob('*'):
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or not (path.is_dir() or path.is_file()):
                raise RuntimeError(f'Non-regular release member: {path}')
            if path.is_dir() and path.relative_to(package_root).as_posix() not in directories:
                raise RuntimeError(f'Release member set mismatch: extra directory {path}')
            if path.is_file():
                actual.add(path.relative_to(package_root).as_posix())
        if actual != set(expected):
            raise RuntimeError(f'Release member set mismatch: missing={sorted(set(expected) - actual)}, '
                               f'extra={sorted(actual - set(expected))}')
    for name, data in expected.items():
        path = package_root / name
        if path.is_symlink() or not path.is_file() or fingerprint(path) != hashlib.sha256(data).hexdigest():
            raise RuntimeError(f'Patched release module mismatch: {path}')


def package_members(wheel, folder):
    members = {}
    normalized = set()
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            if not item.filename.startswith(folder + '/'):
                continue
            path = PurePosixPath(item.filename)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or ':' in item.filename or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or path.as_posix() != item.filename.rstrip('/')
                    or (mode == stat.S_IFDIR and not item.is_dir())):
                raise RuntimeError(f'Invalid wheel member: {item.filename}')
            if item.is_dir():
                continue
            name = '/'.join(path.parts[1:])
            if not name or name.casefold() in normalized:
                raise RuntimeError(f'Duplicate or invalid wheel member: {item.filename}')
            normalized.add(name.casefold())
            members[name] = archive.read(item)
    if not members:
        raise RuntimeError(f'Package missing from wheel: {wheel}')
    return members


IMPORT_PROBE = '''import importlib,inspect,json
from pathlib import Path
modules = {name: str(Path(importlib.import_module(name).__file__).resolve())
           for name in ('ok', 'ok.task.TaskExecutor', 'onnxocr', 'onnxocr.predict_base')}
from ok.task.TaskExecutor import TaskExecutor
from onnxocr.predict_base import PredictBase
assert TaskExecutor.ocr_params_passthrough
assert 'openvino_compile_config' in inspect.signature(PredictBase.__init__).parameters
print('OCR_RELEASE_IMPORTS:' + json.dumps(modules))
'''


def prepare(app, wheels, tag):
    app = app.resolve()
    for name in ('ok', 'onnxocr', 'ocr-release-provenance.json'):
        if os.path.lexists(app / name):
            raise RuntimeError(f'Target already exists: {app / name}; use a clean staging directory')
    if not (app / 'config.py').is_file() or not (app / 'deploy.txt').is_file():
        raise ValueError('--app must be a deployment source directory')
    evidence = {'tag': tag, 'kind': 'verified deploy source; not an EXE installer', 'dependencies': {}}
    contents = {}
    for distribution, wheel_name, version, folder in PACKAGES:
        actual = importlib.metadata.version(distribution)
        if actual != version:
            raise RuntimeError(f'Install patched wheels first: {distribution} expected {version}, got {actual}')
        wheel = wheels / f'{wheel_name}-{version}-py3-none-any.whl'
        expected = package_members(wheel, folder)
        installed = Path(importlib.util.find_spec(folder).origin).parent
        # Installed caches are allowed here; only verified wheel bytes enter staging.
        verify_tree(installed, expected, exact=False)
        contents[folder] = expected
        evidence['dependencies'][distribution] = {'version': actual, 'wheel': wheel.name,
                                                  'wheel_sha256': fingerprint(wheel),
                                                  'installed_module': str(installed),
                                                  'target_source': 'verified wheel members',
                                                  'verified_members': len(expected)}
    with tempfile.TemporaryDirectory(prefix='ocr-release-', dir=app.parent) as temporary:
        owned = Path(temporary).resolve()
        for folder, expected in contents.items():
            package = owned / folder
            for name, data in expected.items():
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            verify_tree(package, expected)
            target = app / folder
            if not package.resolve().is_relative_to(owned) or not target.resolve().is_relative_to(app):
                raise RuntimeError('Staging path escaped its owned directory')
            package.rename(target)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable, '-B', '-m', 'ok.update.inline_ok_requirements', '--tag', tag,
                    '--add-inlined-requirement', 'onnxocr-ppocrv5=onnxocr'], cwd=app, env=env, check=True)
    result = subprocess.run([sys.executable, '-B', '-c', IMPORT_PROBE], cwd=app, env=env,
                            check=True, capture_output=True, text=True, encoding='utf-8')
    lines = [line for line in result.stdout.splitlines() if line.startswith('OCR_RELEASE_IMPORTS:')]
    if len(lines) != 1:
        raise RuntimeError('Release import probe did not return one result')
    evidence['imports'] = json.loads(lines[0].split(':', 1)[1])
    for name, filename in evidence['imports'].items():
        expected_file = app / name.replace('.', '/')
        expected_file = expected_file / '__init__.py' if name in ('ok', 'onnxocr') else expected_file.with_suffix('.py')
        if Path(filename).resolve() != expected_file.resolve():
            raise RuntimeError(f'Release import resolved outside its wheel member: {name}: {filename}')
    for folder, expected in contents.items():
        verify_tree(app / folder, expected)
        evidence['dependencies'][PACKAGES[0][0] if folder == 'ok' else PACKAGES[1][0]][
            'inlined_module'] = str(app / folder)
    evidence['source_hashes'] = {name: fingerprint(app / name) for name in
                                 ('config.py', 'src/ocr_memory.py', 'src/globals.py', 'src/task/ChangeEchoTask.py')}
    destination = app / 'ocr-release-provenance.json'
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
    deploy = app / 'deploy.txt'
    text = deploy.read_text(encoding='utf-8')
    if destination.name not in text.splitlines():
        deploy.write_text(text.rstrip() + '\n' + destination.name + '\n', encoding='utf-8')
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path.cwd())
    parser.add_argument('--wheels', type=Path, default=Path('build/ocr-wheels/patched'))
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.app.resolve(), args.wheels.resolve(), args.tag), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
