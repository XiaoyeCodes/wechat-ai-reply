"""Behavioral checks for snapshot validity and delivery boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path

from workflow import (KEYS, issue_is_delivered, now, record_delivery, sha256,
                      validate_snapshot, write_json)


class Workflow(unittest.TestCase):
    def snapshot(self):
        rows = [dict(key=key, name=key, price=100., reference=100., change_pct=0.,
                     source_url='https://example.com/quote', asof=now().isoformat(), status='ok')
                for key in KEYS]
        return dict(generated_at=now().isoformat(), edition_date=now().date().isoformat(), rows=rows, available_count=9)

    def test_collection_date_is_not_news_date(self):
        snap = self.snapshot()
        # edition_date is a legacy field, not the newspaper's article date.
        snap['edition_date'] = '2020-01-01'
        self.assertEqual(validate_snapshot(snap, now().date().isoformat())['available_count'], 9)
        with self.assertRaisesRegex(ValueError, 'collection date'):
            validate_snapshot(snap, '2020-01-01')

    def test_duplicate_instrument_rejected(self):
        snap = self.snapshot()
        snap['rows'][-1] = copy.deepcopy(snap['rows'][0])
        with self.assertRaisesRegex(ValueError, 'nine requested'):
            validate_snapshot(snap, now().date().isoformat())

    def test_all_missing_rejected_but_partial_explicitly_allowed(self):
        snap = self.snapshot()
        snap['rows'][0].update(price=None, status='missing')
        snap['available_count'] = 8
        self.assertEqual(validate_snapshot(snap, now().date().isoformat())['available_count'], 8)
        for row in snap['rows']:
            row.update(price=None, status='missing')
        snap['available_count'] = 0
        with self.assertRaisesRegex(ValueError, 'All nine'):
            validate_snapshot(snap, now().date().isoformat())

    def test_tampered_change_rejected(self):
        snap = self.snapshot()
        snap['rows'][0]['change_pct'] = 10
        with self.assertRaisesRegex(ValueError, 'disagree'):
            validate_snapshot(snap, now().date().isoformat())

    def test_delivery_requires_ready_and_untampered_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'report.pdf'
            pdf.write_bytes(b'fixture-artifact')
            issue_path = root / 'issue.json'
            issue = dict(date='2026-10-21', sha256='article-fingerprint')
            write_json(issue_path, issue)
            manifest_path = root / 'run-result.json'
            manifest = dict(status='ready', date=issue['date'], sha256=issue['sha256'],
                            validation={'passed': True}, pdf=str(pdf), issue=str(issue_path),
                            artifact_sha256=sha256(pdf))
            write_json(manifest_path, manifest)
            self.assertFalse(issue_is_delivered(root, issue))
            record_delivery(root, manifest_path)
            self.assertTrue(issue_is_delivered(root, issue))
            pdf.write_bytes(b'modified-after-validation')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                record_delivery(root, manifest_path)
            manifest['status'] = 'failed'
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, 'validated ready'):
                record_delivery(root, manifest_path)


if __name__ == '__main__':
    unittest.main()
