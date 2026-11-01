"""Build the team import archive without loading the game runtime."""
import argparse
import json
import zipfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
MEMBERS = ('team.json', 'Camellya.py', 'Sanhua.py', 'ShoreKeeper.py')
DEFAULT_OUTPUT = SOURCE.parents[1] / 'dist/ChunSanShou_Video_Codex_1.2.9.zip'


def build_archive(destination=DEFAULT_OUTPUT):
    destination = Path(destination)
    if destination.suffix.lower() != '.zip':
        raise ValueError('The import archive must have a .zip extension')
    contents = {name: (SOURCE / name).read_bytes() for name in MEMBERS}
    manifest = json.loads(contents['team.json'])
    if manifest['team'] != 'Camellya, Sanhua, Shorekeeper':
        raise ValueError('The manifest must describe Camellya, Sanhua, Shorekeeper')
    for name in MEMBERS[1:]:
        compile(contents[name].decode('utf-8'), name, 'exec')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w') as archive:
        for name, data in contents.items():
            # 固定元数据，使同一源码的导入包可以逐字节复核。
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 31, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    print(build_archive(parser.parse_args().output))
