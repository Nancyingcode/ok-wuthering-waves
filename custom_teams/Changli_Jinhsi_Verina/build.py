"""Build the import archive without loading the game automation runtime."""
import argparse
from pathlib import Path
import json
import zipfile


SOURCE = Path(__file__).resolve().parent
MEMBERS = ('team.json', 'Changli.py', 'Jinhsi.py', 'Verina.py')
DEFAULT_OUTPUT = SOURCE.parents[1] / 'dist/Changli_Jinhsi_Verina_1.0.4-candidate.zip'


def build_archive(destination=DEFAULT_OUTPUT):
    destination = Path(destination)
    if destination.suffix.lower() != '.zip':
        raise ValueError('The import archive must have a .zip extension')
    contents = {name: (SOURCE / name).read_bytes() for name in MEMBERS}
    manifest = json.loads(contents['team.json'])
    members = tuple(name.strip() for name in manifest['team'].split(','))
    if members != ('Changli', 'Jinhsi', 'Verina'):
        raise ValueError('The manifest must describe Changli, Jinhsi, Verina in that order')
    for name in MEMBERS[1:]:
        compile(contents[name].decode('utf-8'), name, 'exec')

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w') as archive:
        for name, data in contents.items():
            # 固定元数据，让相同源码可重复构建出逐字节一致的导入包。
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 30, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    print(build_archive(parser.parse_args().output))
