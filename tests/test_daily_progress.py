import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import daily


class DailyProgressTests(unittest.TestCase):
    def run_daily(self, arguments, existing=None, credit_error=None, failed_collection=None):
        events = []
        self.events = events
        snapshot = {'id': 'test', 'runKey': 'daily-test', 'rows': []}
        def collect(run_key):
            events.append('collect')
            if events.count('collect') == failed_collection:
                raise RuntimeError('source query failed')
            return {**snapshot, 'runKey': run_key, 'capturedAt': '2026-11-06T01:00:00Z'}
        def advance(value):
            events.append('advance')
            if credit_error:
                raise RuntimeError(credit_error)
            return {'credited': 1, 'alreadyCredited': 0}
        def git(*args):
            return {('branch', '--show-current'): 'production',
                    ('status', '--porcelain'): '',
                    ('rev-list', '--left-right', '--count', 'HEAD...origin/production'): '0 0',
                    ('config', 'user.name'): 'Aravind',
                    ('config', 'user.email'): 'this.aravind@gmail.com'}[args]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'data').mkdir()
            if existing:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                snapshot.update(existing)
                snapshot['runKey'] = 'daily-' + datetime.now(ZoneInfo('Asia/Kuala_Lumpur')).strftime('%Y-%m-%d') + '-0900'
                (state / 'data' / 'test.json').write_text(json.dumps(snapshot))
            with patch.object(sys, 'argv', ['daily.py', *arguments]), \
                    patch.object(daily, 'STATE', state), \
                    patch.object(daily, 'git', side_effect=git), \
                    patch.object(daily.subprocess, 'run'), \
                    patch.object(daily, 'collect', side_effect=collect), \
                    patch.object(daily, 'advance_warmup_days', side_effect=advance, create=True), \
                    patch.object(daily, 'store_snapshot', side_effect=lambda *args: events.append('store')), \
                    patch.object(daily, 'build', return_value={'snapshots': [{'id': 'test', 'path': 'test.html'}]}), \
                    patch.object(daily, 'deploy', side_effect=lambda: events.append('deploy')), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    daily.main()
                except SystemExit as error:
                    self.fail('Daily runner did not accept the requested counter options: ' + str(error))
        return events

    def test_credits_before_recollecting_and_publishing(self):
        self.assertEqual(self.run_daily(['--daily', '--advance-days', '--publish']),
                         ['collect', 'advance', 'collect', 'store', 'deploy'])

    def test_existing_daily_snapshot_is_reused_without_another_credit(self):
        self.assertEqual(self.run_daily(['--daily', '--advance-days', '--publish'],
                                        existing={'capturedAt': '2026-11-06T01:00:00Z'}),
                         ['store', 'deploy'])

    def test_manual_refresh_never_credits_a_day(self):
        self.assertEqual(self.run_daily(['--publish']), ['collect', 'store', 'deploy'])

    def test_credit_failure_does_not_store_or_deploy_a_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, 'confirmation failed'):
            self.run_daily(['--daily', '--advance-days', '--publish'], credit_error='confirmation failed')
        self.assertEqual(self.events, ['collect', 'advance'])

    def test_failed_source_query_prevents_credits_and_publication(self):
        with self.assertRaisesRegex(RuntimeError, 'source query failed'):
            self.run_daily(['--daily', '--advance-days', '--publish'], failed_collection=1)
        self.assertEqual(self.events, ['collect'])

    def test_failed_recollection_preserves_credits_without_publishing_old_data(self):
        with self.assertRaisesRegex(RuntimeError, 'source query failed'):
            self.run_daily(['--daily', '--advance-days', '--publish'], failed_collection=2)
        self.assertEqual(self.events, ['collect', 'advance', 'collect'])

    def test_automatic_credits_require_daily_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'requires --daily'):
            self.run_daily(['--advance-days', '--publish'])


if __name__ == '__main__':
    unittest.main()
