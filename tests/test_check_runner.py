import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.check import CheckRun, DIAGNOSTIC_MARKER


class CheckEvidenceTests(unittest.TestCase):
    def test_partial_results_and_browser_failure_evidence_survive_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            run = CheckRun(directory)
            run.summary['python'] = 'passed'
            diagnostic = {'stage': 'offline-chapter', 'pages': [{'url': 'http://localhost/read/sample', 'html': '<h1>failed</h1>'}], 'requestFailures': [{'error': 'connection reset'}]}
            stdout = '### Error\nError: ' + DIAGNOSTIC_MARKER + json.dumps(diagnostic)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch('scripts.check.subprocess.run', return_value=subprocess.CompletedProcess([], 1, stdout)), self.assertRaises(RuntimeError) as error:
                run.run('reader-search', ['fake-browser'])
            run.finish(error.exception)
            saved = json.loads((Path(directory) / 'summary.json').read_text())
            self.assertEqual(saved['status'], 'failed')
            self.assertEqual(saved['python'], 'passed')
            self.assertEqual(saved['reader-search']['stage'], 'offline-chapter')
            self.assertEqual((Path(directory) / 'reader-search-failure-0.html').read_text(), '<h1>failed</h1>')
            self.assertTrue((Path(directory) / saved['reader-search']['diagnostics']).exists())

    def test_timeout_preserves_output_and_non_success_status(self):
        with tempfile.TemporaryDirectory() as directory:
            run = CheckRun(directory)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch('scripts.check.subprocess.run', side_effect=subprocess.TimeoutExpired(['fixture'], 1, output=b'entered chapter stage')), self.assertRaises(RuntimeError):
                run.run('reader', ['fixture'], timeout=1)
            self.assertEqual(json.loads((Path(directory) / 'summary.json').read_text())['reader']['status'], 'failed')
            self.assertIn('entered chapter stage', (Path(directory) / 'reader.log').read_text())
