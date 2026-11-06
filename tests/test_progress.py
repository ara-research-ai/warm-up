from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.today = datetime.now(timezone.utc).date().isoformat()
        self.snapshot = {
            'source': 'live', 'capturedAt': datetime.now(timezone.utc).isoformat(),
            'rows': [
                {'name': 'Warming', 'number': '601100000001', 'accountId': 'a' * 24, 'group': 'warmup'},
                {'name': 'Ready', 'number': '601100000002', 'accountId': 'b' * 24, 'group': 'ready'},
                {'name': 'Cooling', 'number': '601100000003', 'accountId': 'c' * 24, 'group': 'cooldown'},
            ],
        }
        self.phone = {
            '_id': 'test-phone', 'phoneNumber': '601100000001', 'name': 'Warming',
            'source': 'adacash_new_sim', 'sourceSimExternalId': 'a' * 24,
            'isActive': True, 'warmupState': 'warming', 'warmupMinimumDays': 7,
            'warmupCompletedDays': 2, 'dayNumber': 3, 'startedAt': 1793758867408,
        }
        self.calls = []
        self.credited = False

    def manager(self, kind, path, args):
        self.calls.append((kind, path, args))
        if path == 'queries/phones:list':
            return [deepcopy(self.phone)]
        if path == 'actions/warmup:markManualWarmupDone':
            self.assertEqual(kind, 'action')
            self.assertEqual(args, {'phoneIds': ['test-phone'], 'dateKey': self.today})
            already = self.credited
            self.credited = True
            return {'dateKey': self.today, 'credited': int(not already),
                    'alreadyCredited': int(already), 'skipped': 0, 'failed': []}
        if path == 'queries/phones:getById':
            self.assertEqual(args, {'id': 'test-phone'})
            return {**self.phone, 'dayNumber': 4, 'warmupCompletedDays': 3,
                    'lastDayIncrementDateKey': self.today}
        self.fail('Unexpected manager operation')

    def test_only_currently_warming_listed_account_gets_confirmed(self):
        with patch.object(progress, 'manager_call', side_effect=self.manager):
            result = progress.advance_warmup_days(self.snapshot)
        self.assertEqual(result['credited'], 1)
        self.assertEqual(result['eligible'], 1)
        self.assertEqual(result['excluded'], 2)

    def test_same_date_retry_uses_existing_deduplication(self):
        with patch.object(progress, 'manager_call', side_effect=self.manager):
            first = progress.advance_warmup_days(self.snapshot)
            retry = progress.advance_warmup_days(self.snapshot)
        self.assertEqual(first['credited'], 1)
        self.assertEqual(retry['credited'], 0)
        self.assertEqual(retry['alreadyCredited'], 1)

    def test_dry_run_never_calls_an_action(self):
        with patch.object(progress, 'manager_call', side_effect=self.manager):
            result = progress.advance_warmup_days(self.snapshot, dry_run=True)
        self.assertEqual(result['pending'], 1)
        self.assertTrue(all(kind == 'query' for kind, _, _ in self.calls))

    def test_account_identity_must_match_before_any_action(self):
        self.phone['sourceSimExternalId'] = 'd' * 24
        with patch.object(progress, 'manager_call', side_effect=self.manager):
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                progress.advance_warmup_days(self.snapshot)
        self.assertTrue(all(kind == 'query' for kind, _, _ in self.calls))

    def test_missing_or_inactive_manager_account_fails_before_any_action(self):
        for phones in [[], [{**self.phone, 'isActive': False}]]:
            with self.subTest(phones=bool(phones)), patch.object(progress, 'manager_call', return_value=phones) as call:
                with self.assertRaises(RuntimeError):
                    progress.advance_warmup_days(self.snapshot)
                self.assertEqual(call.call_count, 1)

    def test_duplicate_manager_number_is_ambiguous_and_never_credited(self):
        with patch.object(progress, 'manager_call', return_value=[self.phone, {**self.phone, '_id': 'other'}]) as call:
            with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                progress.advance_warmup_days(self.snapshot)
            self.assertEqual(call.call_count, 1)

    def test_failed_or_skipped_confirmation_is_not_reported_as_success(self):
        for key, value in [('failed', [{'phoneId': 'test-phone', 'name': 'private'}]), ('skipped', 1)]:
            response = {'dateKey': self.today, 'credited': 0, 'alreadyCredited': 0, 'skipped': 0, 'failed': []}
            response[key] = value
            with self.subTest(key=key), patch.object(progress, 'manager_call', side_effect=[[self.phone], response]):
                with self.assertRaisesRegex(RuntimeError, 'confirmation failed'):
                    progress.advance_warmup_days(self.snapshot)

    def test_unacknowledged_progress_or_cycle_change_fails_readback(self):
        for field, value in [('warmupCompletedDays', 2), ('startedAt', 1793758867409)]:
            after = {**self.phone, 'dayNumber': 4, 'warmupCompletedDays': 3,
                     'lastDayIncrementDateKey': self.today, field: value}
            result = {'dateKey': self.today, 'credited': 1, 'alreadyCredited': 0, 'skipped': 0, 'failed': []}
            with self.subTest(field=field), patch.object(progress, 'manager_call', side_effect=[[self.phone], result, after]):
                with self.assertRaisesRegex(RuntimeError, 'read-back'):
                    progress.advance_warmup_days(self.snapshot)

    def test_reaching_minimum_days_accepts_ready_and_inactive_readback(self):
        self.phone.update(dayNumber=7, warmupCompletedDays=6)
        after = {**self.phone, 'dayNumber': 8, 'warmupCompletedDays': 7,
                 'warmupState': 'ready_to_activate', 'isActive': False,
                 'lastDayIncrementDateKey': self.today}
        result = {'dateKey': self.today, 'credited': 1, 'alreadyCredited': 0, 'skipped': 0, 'failed': []}
        with patch.object(progress, 'manager_call', side_effect=[[self.phone], result, after]):
            self.assertEqual(progress.advance_warmup_days(self.snapshot)['credited'], 1)

    def test_historical_or_failed_source_data_cannot_credit_anything(self):
        for change in [{'source': 'historical'}, {'capturedAt': '2000-01-01T01:00:00Z'}]:
            with self.subTest(change=change), patch.object(progress, 'manager_call') as call:
                with self.assertRaises(RuntimeError):
                    progress.advance_warmup_days({**self.snapshot, **change})
                call.assert_not_called()

    def test_successful_empty_live_list_requires_no_action(self):
        with patch.object(progress, 'manager_call') as call:
            result = progress.advance_warmup_days({**self.snapshot, 'rows': []})
        self.assertEqual(result['eligible'], 0)
        self.assertEqual(result['credited'], 0)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
