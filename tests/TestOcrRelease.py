from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts.prepare_ocr_release import prepare


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
                with self.assertRaisesRegex(RuntimeError, 'Patched release module mismatch'):
                    prepare(app, directory, 'validation')
            run.assert_not_called()
            self.assertEqual((app / 'ok/__init__.py').read_bytes(), b'old')
            self.assertFalse((app / 'ocr-release-provenance.json').exists())


if __name__ == '__main__':
    unittest.main()
