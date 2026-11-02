import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.run_python_tests import discover_modules, run_suite, select_modules


class TestValidationRunner(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='python-validation-test-')))
        (self.root / 'tests').mkdir()
        (self.root / 'run_test.py').write_text(
            "import runpy, sys\ntarget = sys.argv[1]\nsys.argv = [target]\n"
            "runpy.run_path(target, run_name='__main__')\n", encoding='utf-8')

    def module(self, name, body='self.assertTrue(True)'):
        filename = self.root / 'tests' / name
        filename.write_text(
            'import builtins, unittest\nclass Fixture(unittest.TestCase):\n'
            f'    def test_fixture(self):\n        {body}\n'
            "if __name__ == '__main__':\n    unittest.main(verbosity=2)\n", encoding='utf-8')
        return filename

    def run_suite(self, **kwargs):
        return run_suite(self.root, output=io.StringIO(), **kwargs)

    def test_discovery_keeps_both_naming_conventions_and_all_root_python_files(self):
        self.module('test_lower.py')
        self.module('TestUpper.py')
        self.module('helper.py')
        (self.root / 'tests' / 'data.txt').touch()
        (self.root / 'tests' / 'nested').mkdir()
        (self.root / 'tests' / 'nested' / 'TestNested.py').touch()
        self.assertEqual([module.name for module in discover_modules(self.root / 'tests')],
                         ['helper.py', 'test_lower.py', 'TestUpper.py'])

    def test_every_module_belongs_to_exactly_one_shard_for_different_counts(self):
        modules = [self.module(f'Test{index}.py') for index in range(7)]
        for count in (1, 2, 3, 7):
            with self.subTest(count=count):
                shards = [select_modules(modules, index, count) for index in range(count)]
                combined = [module for shard in shards for module in shard]
                self.assertCountEqual(combined, modules)
                self.assertEqual(len(combined), len(set(combined)))

    def test_invalid_or_empty_shards_fail_instead_of_succeeding_without_tests(self):
        modules = [self.module('TestOne.py')]
        for index, count in ((0, 0), (-1, 2), (2, 2), (1, 2)):
            with self.subTest(index=index, count=count):
                with self.assertRaises(ValueError):
                    select_modules(modules, index, count)

    def test_empty_test_directory_fails(self):
        with self.assertRaises(ValueError):
            self.run_suite()

    def test_modules_keep_separate_interpreters_and_report_all_test_counts(self):
        self.module('TestA.py', 'builtins.validation_marker = True')
        self.module('TestB.py', "self.assertFalse(hasattr(builtins, 'validation_marker'))")
        report = self.run_suite()
        self.assertTrue(report['passed'])
        self.assertEqual(report['testsRun'], 2)
        self.assertEqual(report['selectedModules'], ['tests/TestA.py', 'tests/TestB.py'])
        self.assertEqual([module['testsRun'] for module in report['modules']], [1, 1])
        self.assertEqual(len(report['allModules']), 2)

    def test_module_failure_is_retained_while_later_modules_still_run(self):
        self.module('TestA.py', "self.fail('expected fixture failure')")
        self.module('TestB.py')
        report = self.run_suite()
        self.assertFalse(report['passed'])
        self.assertEqual(report['testsRun'], 2)
        self.assertNotEqual(report['modules'][0]['exitCode'], 0)
        self.assertTrue(report['modules'][1]['passed'])

    def test_zero_exit_without_a_unittest_result_is_not_accepted(self):
        self.module('TestOne.py')
        (self.root / 'run_test.py').write_text('pass\n', encoding='utf-8')
        report = self.run_suite()
        self.assertFalse(report['passed'])
        self.assertIsNone(report['modules'][0]['testsRun'])

    def test_zero_collected_tests_are_not_accepted(self):
        (self.root / 'tests' / 'Empty.py').write_text(
            "import unittest\nif __name__ == '__main__': unittest.main()\n", encoding='utf-8')
        report = self.run_suite()
        self.assertFalse(report['passed'])
        self.assertEqual(report['testsRun'], 0)

    def test_unittest_skips_are_reported_without_changing_existing_skip_policy(self):
        self.module('TestSkip.py', "self.skipTest('fixture skip')")
        report = self.run_suite()
        self.assertTrue(report['passed'])
        self.assertEqual(report['testsRun'], 1)
        self.assertEqual(report['skipped'], 1)

    def test_cli_propagates_failure_and_writes_a_complete_report(self):
        self.module('TestA.py', "self.fail('expected fixture failure')")
        self.module('TestB.py')
        scripts = self.root / 'scripts'
        scripts.mkdir()
        runner = scripts / 'run_python_tests.py'
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/run_python_tests.py', runner)
        environment = os.environ.copy()
        environment.pop('GITHUB_STEP_SUMMARY', None)
        result = subprocess.run([sys.executable, str(runner)], cwd=self.root, env=environment,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads((self.root / 'build/python-validation/shard-0.json').read_text(encoding='utf-8'))
        self.assertFalse(report['passed'])
        self.assertEqual(len(report['modules']), 2)
        self.assertEqual(report['testsRun'], 2)


if __name__ == '__main__':
    unittest.main()
