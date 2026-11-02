"""Build versioned patched wheels from the two fixed upstream release wheels."""

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPECS = [('ok_script', '2.0.22'), ('onnxocr_ppocrv5', '0.0.22')]
SOURCE_HASHES = {
    'ok_script': 'e043009920ed7b311d4cfccc04e6e6faa16dca8c2dcbb38bd273bf16fee3b7e2',
    'onnxocr_ppocrv5': 'b98271ce876d9720c788c437d69f63469cad53cb19945da843dbbe46f8a1271a',
}


def build(source, output, package, version):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != SOURCE_HASHES[package]:
        raise ValueError(f'Unexpected upstream wheel hash: {source.name}: {source_hash}')
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    old_info = f'{package}-{version}.dist-info'
    new_version = f'{version}+okww.ocr1'
    new_info = f'{package}-{new_version}.dist-info'
    patch = ROOT / 'patches/ocr' / f'{package}-{version}.patch'
    patch_data = patch.read_text(encoding='utf-8').encode('utf-8')
    patched_paths = [line[6:] for line in patch_data.decode('utf-8').splitlines()
                     if line.startswith('--- a/')]
    for name in patched_paths:
        if name in files:
            files[name] = files[name].replace(b'\r\n', b'\n')
    with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='ocr-wheel-') as temporary:
        directory = Path(temporary)
        # Only upstream wheel members are extracted, never arbitrary paths supplied by a patch.
        for name, data in files.items():
            target = directory / name
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError(f'Unsafe wheel member: {name}')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        relative = directory.relative_to(ROOT).as_posix()
        for check in (True, False):
            command = ['git', 'apply', '--whitespace=nowarn', f'--directory={relative}']
            if check:
                command.append('--check')
            subprocess.run([*command, '-'], input=patch_data, cwd=ROOT, check=True)
        files = {path.relative_to(directory).as_posix(): path.read_bytes()
                 for path in directory.rglob('*') if path.is_file()}
    files.pop(f'{old_info}/RECORD')
    files = {name.replace(old_info + '/', new_info + '/', 1): data for name, data in files.items()}
    metadata = files[f'{new_info}/METADATA'].decode('utf-8')
    if f'Version: {version}\n' not in metadata:
        raise ValueError(f'Unexpected wheel metadata version: {source.name}')
    files[f'{new_info}/METADATA'] = metadata.replace(
        f'Version: {version}\n', f'Version: {new_version}\n', 1).encode('utf-8')
    provenance = {'source_wheel': source.name, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'patch_sha256': hashlib.sha256(patch_data).hexdigest()}
    files[f'{new_info}/okww-ocr-source.json'] = json.dumps(provenance, indent=2).encode('utf-8')
    records = io.StringIO(newline='')
    writer = csv.writer(records, lineterminator='\n')
    for name, data in sorted(files.items()):
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        writer.writerow([name, f'sha256={encoded}', len(data)])
    writer.writerow([f'{new_info}/RECORD', '', ''])
    files[f'{new_info}/RECORD'] = records.getvalue().encode('utf-8')
    destination = output / source.name.replace(f'-{version}-', f'-{new_version}-', 1)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            member = zipfile.ZipInfo(name, date_time=(2026, 11, 2, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    print(destination)
    return destination


def bundle(destination, wheels):
    """Stage the deploy.txt source layout and relative, hash-pinned local wheels."""
    members = {}
    for folder in ('src', 'assets', 'icons', 'i18n'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                members[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ('main.py', 'config.py', 'pyappify.yml', 'LICENSE.txt'):
        members[name] = (ROOT / name).read_bytes()
    for path in (ROOT / 'scripts').glob('*.py'):
        members[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for path in (ROOT / 'scripts').glob('ocr_*inputs.json'):
        members[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ('9.png', '13.png'):
        members[f'ok_templates/{name}'] = (ROOT / 'ok_templates' / name).read_bytes()
    for name in ('tacet_elimination_final.jpg', 'tacet_elimination_board.jpg', 'echo_enhance.png',
                 'find_confirm.png', 'find_add_mat.png'):
        members[f'tests/images/{name}'] = (ROOT / 'tests/images' / name).read_bytes()
    requirements = [line for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                    if not line.startswith(('ok-script', 'onnxocr-ppocrv5'))]
    for wheel in wheels:
        data = wheel.read_bytes()
        members[f'wheels/{wheel.name}'] = data
        requirements.append(f'./wheels/{wheel.name}')
    members['requirements.txt'] = ('\n'.join(requirements) + '\n').encode('utf-8')
    members['main_validation.py'] = b'''from copy import deepcopy
import sys
from config import config
from ok import OK

options = deepcopy(config)
options.update(check_mutex=False, mcp={'enabled': False}, config_folder='validation-configs')
if '--candidate' in sys.argv:
    sys.argv.remove('--candidate')
    options['ocr']['preload'] = False
    options['ocr']['lifecycle']['idle_policy'] = 'ocr_idle'
OK(options).start()
'''
    members['install-validation.ps1'] = b'''param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& $Python verify_bundle.py
if ($LASTEXITCODE -ne 0) { throw 'bundle integrity validation failed' }
if (Test-Path -LiteralPath '.runtime') { throw 'Use a new unpack directory; .runtime already exists.' }
& $Python -m venv .runtime
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
& ./.runtime/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }
& ./.runtime/Scripts/python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'dependency validation failed' }
'''
    members['verify_bundle.py'] = b'''import hashlib
import json
from pathlib import Path
root = Path(__file__).resolve().parent
manifest = json.loads((root / 'bundle-manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['files'].items():
    path = (root / name).resolve()
    if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'Bundle hash mismatch: {name}')
print('Bundle content hashes verified')
'''
    members['VERIFY.txt'] = (
        'Local source/update validation payload, following deploy.txt. Not a PyAppify installer.\n'
        'Use a new unpack directory and Python 3.12: ./install-validation.ps1 -Python <absolute-python.exe>\n'
        'Launch: ./.runtime/Scripts/pythonw.exe main_validation.py [--candidate]\n'
        'Settings and mutex are isolated. Existing installations are not overwritten.\n'
        'Before installing, verify every wheel SHA-256 against bundle-manifest.json.\n'
        'The generated relative requirements select the bundled patched wheels.\n'
    ).encode('utf-8')
    manifest = {'format': 1, 'kind': 'local update/source validation payload',
                'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(members.items())},
                'wheels': [wheel.name for wheel in wheels]}
    members['bundle-manifest.json'] = json.dumps(manifest, indent=2).encode('utf-8')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            member = zipfile.ZipInfo(name, date_time=(2026, 11, 2, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    print(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, default=ROOT / 'build/ocr-wheels/upstream')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/ocr-wheels/patched')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--bundle', type=Path, help='Also build an isolated local source/update validation ZIP')
    args = parser.parse_args()
    args.wheelhouse.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (ROOT / 'build').mkdir(exist_ok=True)
    wheels = []
    for package, version in SPECS:
        if args.download:
            subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
                            '--dest', str(args.wheelhouse), f'{package}=={version}'], check=True)
        source = args.wheelhouse / f'{package}-{version}-py3-none-any.whl'
        wheels.append(build(source, args.output, package, version))
    if args.bundle:
        bundle(args.bundle, wheels)


if __name__ == '__main__':
    main()
