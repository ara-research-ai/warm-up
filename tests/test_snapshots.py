import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('snapshots', ROOT / 'scripts' / 'snapshots.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def sample(self, time='2026-10-12T01:00:00+00:00'):
        return {'capturedAt': time, 'source': 'live', 'coverage': 'All three groups; inactive clients excluded.', 'runKey': 'test-'+time, 'rows': [{'name':'Example 1', 'number':'601100000000', 'group':'warmup', 'day':1, 'message':"Hello! How's your day?"}]}

    def test_archiving_same_run_does_not_duplicate_or_overwrite_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.module.store_snapshot(root, self.sample())
            self.module.store_snapshot(root, self.sample())
            self.assertEqual(len(list((root/'data').glob('*.json'))), 1)
            changed = self.sample(); changed['rows'][0]['message']='Different'
            with self.assertRaises(ValueError):
                self.module.store_snapshot(root, changed)
            self.assertEqual(json.loads(first.read_text())['rows'][0]['message'], "Hello! How's your day?")

    def test_old_backfill_does_not_replace_latest_and_dates_are_malaysia_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.module.store_snapshot(root, self.sample('2026-10-11T23:30:00+00:00'))
            self.module.store_snapshot(root, self.sample('2026-08-20T02:09:43+00:00'))
            self.module.build(root)
            archive=json.loads((root/'docs'/'archive.json').read_text())
            self.assertEqual(archive['snapshots'][0]['date'], '2026-10-12')
            self.assertEqual(archive['snapshots'][0]['time'], '07:30:00')
            self.assertEqual(len(list((root/'docs'/'snapshots').glob('*/*.html'))), 2)
            home=(root/'docs'/'index.html').read_text()
            self.assertIn('12 October 2026', home)
            self.assertIn('601100000000?text=Hello%21%20How', home)

    def test_successful_empty_list_is_kept_as_an_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sample=self.sample(); sample['rows']=[]
            self.module.store_snapshot(root,sample)
            self.module.build(root)
            self.assertIn('No phones in this snapshot', (root/'docs'/'index.html').read_text())

    def test_invalid_phone_fails_before_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample=self.sample(); sample['rows'][0]['number']='invalid'
            with self.assertRaises(ValueError):
                self.module.store_snapshot(Path(tmp),sample)


if __name__=='__main__':
    unittest.main()
