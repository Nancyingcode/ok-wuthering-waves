"""Verify patched dependencies before the existing release inlining step."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile


PACKAGES = [('ok-script', 'ok_script', '2.0.22+okww.ocr1', 'ok'),
            ('onnxocr-ppocrv5', 'onnxocr_ppocrv5', '0.0.22+okww.ocr1', 'onnxocr')]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_tree(package_root, expected):
    for name, data in expected.items():
        path = package_root / name
        if not path.is_file() or fingerprint(path) != hashlib.sha256(data).hexdigest():
            raise RuntimeError(f'Patched release module mismatch: {path}')


def prepare(app, wheels, tag):
    if not (app / 'config.py').is_file() or not (app / 'deploy.txt').is_file():
        raise ValueError('--app must be a deployment source directory')
    evidence = {'tag': tag, 'kind': 'verified deploy source; not an EXE installer', 'dependencies': {}}
    contents = {}
    for distribution, wheel_name, version, folder in PACKAGES:
        actual = importlib.metadata.version(distribution)
        if actual != version:
            raise RuntimeError(f'Install patched wheels first: {distribution} expected {version}, got {actual}')
        wheel = wheels / f'{wheel_name}-{version}-py3-none-any.whl'
        with zipfile.ZipFile(wheel) as archive:
            expected = {name[len(folder) + 1:]: archive.read(name) for name in archive.namelist()
                        if name.startswith(folder + '/') and not name.endswith('/')}
        if not expected:
            raise RuntimeError(f'Package missing from wheel: {wheel}')
        installed = Path(importlib.util.find_spec(folder).origin).parent
        verify_tree(installed, expected)
        if (app / folder).exists():
            # The library skips existing source folders. Refuse stale contents instead of packaging them.
            verify_tree(app / folder, expected)
        contents[folder] = expected
        evidence['dependencies'][distribution] = {'version': actual, 'wheel': wheel.name,
                                                  'wheel_sha256': fingerprint(wheel),
                                                  'installed_module': str(installed),
                                                  'verified_members': len(expected)}
    subprocess.run([sys.executable, '-m', 'ok.update.inline_ok_requirements', '--tag', tag,
                    '--add-inlined-requirement', 'onnxocr-ppocrv5=onnxocr'], cwd=app, check=True)
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
