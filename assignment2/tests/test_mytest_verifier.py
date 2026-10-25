"""Check that missing, skipped or failed held-out tests never count as resolved."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mytest_verify', Path(__file__).parents[1] / 'mytest/verify.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ScoringTests(unittest.TestCase):
    def test_all_required_tests_must_pass(self):
        criteria = {'fail_to_pass': ['tests/a.ts::fix'], 'pass_to_pass': ['tests/a.ts::regression']}
        for status, expected in [('passed', True), ('failed', False), ('pending', False)]:
            report = {'success': True, 'testResults': [{'name': '/app/tests/a.ts', 'assertionResults': [
                {'fullName': 'fix', 'status': status}, {'fullName': 'regression', 'status': 'passed'}]}]}
            self.assertEqual(module.score(report, criteria)[0], expected)
        self.assertFalse(module.score({'success': True, 'testResults': []}, criteria)[0])
        report['testResults'][0]['assertionResults'][0]['status'] = 'passed'
        report['success'] = False
        self.assertFalse(module.score(report, criteria)[0])


if __name__ == '__main__':
    unittest.main()
