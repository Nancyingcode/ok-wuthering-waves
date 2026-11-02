from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts.prepare_ocr_release import PACKAGES, package_members, prepare, verify_tree


class TestOcrRelease(unittest.TestCase):
    def make_app(self, directory):
        app = Path(directory) / 'app'
        app.mkdir()
        (app / 'config.py').write_text('version = "unchanged"\n', encoding='utf-8')
        (app / 'deploy.txt').write_text('src\nconfig.py\n', encoding='utf-8')
        return app

    def test_original_dependency_is_rejected_before_inlining_or_version_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            before = (app / 'config.py').read_bytes()
            with patch('scripts.prepare_ocr_release.importlib.metadata.version', return_value='2.0.22'), \
                    patch('scripts.prepare_ocr_release.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Install patched wheels first'):
                    prepare(app, Path(directory), 'validation')
            run.assert_not_called()
            self.assertEqual((app / 'config.py').read_bytes(), before)

    def test_stale_inlined_source_cannot_bypass_the_installed_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            app = self.make_app(directory)
            installed = directory / 'installed'
            installed.mkdir()
            (installed / '__init__.py').write_bytes(b'patched')
            (app / 'ok').mkdir()
            (app / 'ok/__init__.py').write_bytes(b'old')
            wheel = directory / 'ok_script-2.0.22+okww.ocr1-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('ok/__init__.py', b'patched')
            with patch('scripts.prepare_ocr_release.importlib.metadata.version', return_value='2.0.22+okww.ocr1'), \
                    patch('scripts.prepare_ocr_release.importlib.util.find_spec',
                          return_value=SimpleNamespace(origin=str(installed / '__init__.py'))), \
                    patch('scripts.prepare_ocr_release.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Target already exists'):
                    prepare(app, directory, 'validation')
            run.assert_not_called()
            self.assertEqual((app / 'ok/__init__.py').read_bytes(), b'old')
            self.assertFalse((app / 'ocr-release-provenance.json').exists())

    def test_extra_shadow_package_bytecode_or_native_member_is_rejected(self):
        for extra in ('predict_base/__init__.py', '__pycache__/predict_base.pyc', 'stale.pyd', 'old.dll'):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'predict_base.py').write_bytes(b'patched')
                added = root / extra
                added.parent.mkdir(parents=True, exist_ok=True)
                added.write_bytes(b'old')
                with self.assertRaisesRegex(RuntimeError, 'member set'):
                    verify_tree(root, {'predict_base.py': b'patched'})

    def test_existing_target_or_provenance_is_rejected_without_any_mutation(self):
        for name in ('ok', 'onnxocr', 'ocr-release-provenance.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                app = self.make_app(directory)
                (app / 'requirements.txt').write_bytes(b'original requirements')
                (app / name).write_bytes(b'existing content')
                before = {p.name: p.read_bytes() for p in app.iterdir()}
                with patch('scripts.prepare_ocr_release.subprocess.run') as run, \
                        patch('scripts.prepare_ocr_release.importlib.metadata.version') as version:
                    with self.assertRaisesRegex(RuntimeError, 'Target already exists'):
                        prepare(app, Path(directory), 'different-version')
                self.assertEqual({p.name: p.read_bytes() for p in app.iterdir()}, before)
                run.assert_not_called()
                version.assert_not_called()

    def test_missing_or_changed_wheel_member_is_rejected(self):
        for content in (None, b'changed'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if content is not None:
                    (root / 'module.py').write_bytes(content)
                with self.assertRaises(RuntimeError):
                    verify_tree(root, {'module.py': b'patched'})

    def test_unsafe_or_duplicate_wheel_member_is_rejected(self):
        for member in ('ok/../escaped.py', 'ok/module.py'):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                wheel = Path(directory) / 'test.whl'
                with zipfile.ZipFile(wheel, 'w') as archive:
                    archive.writestr('ok/MODULE.py', b'first')
                    archive.writestr(member, b'other')
                with self.assertRaisesRegex(RuntimeError, 'wheel member'):
                    package_members(wheel, 'ok')

    def test_clean_staging_copies_only_wheel_bytes_and_preserves_installed_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            app = self.make_app(directory)
            for name in ('src/ocr_memory.py', 'src/globals.py', 'src/task/ChangeEchoTask.py'):
                path = app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'source')
            (app / 'requirements.txt').write_text('ok-script\nonnxocr-ppocrv5\nnumpy\n', encoding='utf-8')
            installed = {}
            versions = {}
            for distribution, wheel_name, version, folder in PACKAGES:
                versions[distribution] = version
                package = directory / 'installed' / folder
                package.mkdir(parents=True)
                installed[folder] = package
                with zipfile.ZipFile(directory / f'{wheel_name}-{version}-py3-none-any.whl', 'w') as archive:
                    for name, data in [('__init__.py', b'patched'), ('resources/model.bin', b'model')]:
                        archive.writestr(folder + '/' + name, data)
                        target = package / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                (package / '__pycache__').mkdir()
                (package / '__pycache__/old.pyc').write_bytes(b'installed cache')
            source_before = {p.as_posix(): p.read_bytes() for p in (directory / 'installed').rglob('*') if p.is_file()}

            def subprocess_run(command, **kwargs):
                self.assertIn('-B', command)
                self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
                if '-m' in command:
                    (app / 'config.py').write_text('version = "validation"\n', encoding='utf-8')
                    (app / 'requirements.txt').write_text('numpy\n', encoding='utf-8')
                    (app / 'deploy.txt').write_text('ok\nonnxocr\n', encoding='utf-8')
                    return SimpleNamespace(returncode=0)
                paths = {name: str(app / name.replace('.', '/') / '__init__.py') if name in ('ok', 'onnxocr')
                         else str((app / name.replace('.', '/')).with_suffix('.py'))
                         for name in ('ok', 'ok.task.TaskExecutor', 'onnxocr', 'onnxocr.predict_base')}
                return SimpleNamespace(stdout='OCR_RELEASE_IMPORTS:' + json.dumps(paths), returncode=0)

            with patch('scripts.prepare_ocr_release.importlib.metadata.version', side_effect=versions.get), \
                    patch('scripts.prepare_ocr_release.importlib.util.find_spec',
                          side_effect=lambda name: SimpleNamespace(origin=str(installed[name] / '__init__.py'))), \
                    patch('scripts.prepare_ocr_release.subprocess.run', side_effect=subprocess_run):
                result = prepare(app, directory, 'validation')
            self.assertEqual(source_before, {p.as_posix(): p.read_bytes() for p in (directory / 'installed').rglob('*') if p.is_file()})
            self.assertFalse(list(app.glob('*/__pycache__')))
            self.assertTrue((app / 'ocr-release-provenance.json').is_file())
            self.assertIn('onnxocr', (app / 'deploy.txt').read_text(encoding='utf-8'))
            self.assertEqual((app / 'requirements.txt').read_text(encoding='utf-8'), 'numpy\n')
            self.assertEqual(result['dependencies']['ok-script']['target_source'], 'verified wheel members')


if __name__ == '__main__':
    unittest.main()
