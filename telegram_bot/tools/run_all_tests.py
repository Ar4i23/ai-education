"""Run all project tests despite root/test_runner.py name collision.

The script imports tests using unique names; never starts polling or HTTP.
Run using the project's installed Python environment.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
files = sorted((ROOT / 'tests').glob('test_*.py'))
loader = unittest.TestLoader()
suite = unittest.TestSuite()
print(f'FOUND TEST FILES: {len(files)}', flush=True)

for index, path in enumerate(files):
    module_name = f'_isolated_antonova_test_{index}'
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Could not load {path.name}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    suite.addTests(loader.loadTestsFromModule(module))

result = unittest.TextTestRunner(verbosity=1).run(suite)
print('TOTAL TESTS:', result.testsRun)
print('FAILURES:', len(result.failures))
print('ERRORS:', len(result.errors))
print('RESULT:', 'OK' if result.wasSuccessful() else 'FAILED')
sys.exit(0 if result.wasSuccessful() else 1)
