"""Check that requirements.txt pins every pinned package's own runtime dependencies.

The launcher installs this lockfile with ``pip_args: --no-deps``, so each pinned
package must carry its runtime requirements explicitly: installing a package
without them leaves an unusable shell. That is how ``mcp==2.3.0`` lost
``mcp-types`` and the in-app MCP server failed with "The required MCP dependency
is missing or incompatible. Reinstall this project's pinned ok-script
dependencies."
"""

import unittest
from importlib import metadata
from pathlib import Path

try:  # CI installs pip, but not necessarily the standalone packaging project
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name

REQUIREMENTS_FILE = Path(__file__).resolve().parent.parent / 'requirements.txt'
MIN_INSTALLED_PINS = 10


def read_pins(path):
    """Return a lockfile's pins by canonical name, plus any name pinned twice."""
    pins = {}
    duplicates = []
    for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        try:
            requirement = Requirement(line)
        except Exception as error:
            raise ValueError(f'{path.name}:{number}: cannot parse {line!r}: {error}')
        name = canonicalize_name(requirement.name)
        if name in pins:
            duplicates.append(name)
        pins[name] = requirement
    return pins, duplicates


def installed_distribution(name):
    """Return the installed distribution for a canonical name, or None."""
    try:
        return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None


def dependency_applies(marker, extras, environment):
    """Whether a Requires-Dist marker holds for any extra requested by its package."""
    if marker is None:
        return True
    for extra in ('',) + tuple(sorted(extras)):
        scope = dict(environment)
        scope['extra'] = extra
        try:
            if marker.evaluate(scope):
                return True
        except Exception:
            # An unknown marker variable must not excuse a required dependency.
            return True
    return False


def closure_gaps(pins, environment=None):
    """Map each dependency missing from ``pins`` to the pinned packages requiring it."""
    environment = dict(environment or default_environment())
    gaps = {}
    for name, requirement in sorted(pins.items()):
        distribution = installed_distribution(name)
        if distribution is None:
            continue
        for declared in distribution.requires or ():
            dependency = Requirement(declared)
            if not dependency_applies(dependency.marker, requirement.extras, environment):
                continue
            dependency_name = canonicalize_name(dependency.name)
            if dependency_name not in pins:
                gaps.setdefault(dependency_name, []).append(name)
    return gaps


def describe_gaps(gaps, path):
    lines = [f'{path.name} is missing pinned runtime dependencies; '
             'the launcher installs this file with --no-deps:']
    lines.extend(f'  {dependency} <- required by {", ".join(sorted(owners))}'
                 for dependency, owners in sorted(gaps.items()))
    return '\n'.join(lines)


class TestRequirementsClosure(unittest.TestCase):
    def test_lockfiles_pin_each_package_once(self):
        for path in sorted(REQUIREMENTS_FILE.parent.glob('requirements*.txt')):
            with self.subTest(requirements=path.name):
                pins, duplicates = read_pins(path)
                self.assertTrue(pins, f'{path.name} pins no package')
                self.assertEqual(duplicates, [], f'{path.name} pins {duplicates} more than once')

    def test_every_pinned_package_pins_its_runtime_dependencies(self):
        pins, _ = read_pins(REQUIREMENTS_FILE)
        installed = sum(1 for name in pins if installed_distribution(name) is not None)
        if installed < MIN_INSTALLED_PINS:
            self.skipTest(f'only {installed} pinned packages are installed; install '
                          f'{REQUIREMENTS_FILE.name} before running this lockfile check')
        gaps = closure_gaps(pins)
        self.assertEqual(gaps, {}, describe_gaps(gaps, REQUIREMENTS_FILE))


if __name__ == '__main__':
    unittest.main()
