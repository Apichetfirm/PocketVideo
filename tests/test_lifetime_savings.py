"""Regression tests for persistent lifetime metrics and deleted source files."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.models import JobResultComparison, OperationMode
from pocketvideo.core.savings import SavingsStats, share_summary


def completed(job_id: str, source: Path, output: Path, before: int, after: int) -> JobResultComparison:
    return JobResultComparison(
        job_id=job_id,
        input_path=source,
        output_path=output,
        operation_mode=OperationMode.COMPRESS,
        original_size_bytes=before,
        output_size_bytes=after,
        space_saved_bytes=before - after,
        reduction_percent=100.0 * (before - after) / before if before else 0.0,
        original_resolution="1920x1080",
        output_resolution="1920x1080",
        original_codec="h264", output_codec="hevc",
        processing_duration_seconds=2.0,
    )


class SavingsPersistenceTests(unittest.TestCase):
    def test_history_survives_deleted_original_and_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mgr = SettingsManager(root / "settings")
            src, dst = root / "original.mp4", root / "smaller.mp4"
            src.write_bytes(b"x" * 100)
            dst.write_bytes(b"y" * 40)
            mgr.append_history(completed("job-1", src, dst, 100, 40))
            src.unlink()
            dst.unlink()
            refreshed = SettingsManager(root / "settings")
            self.assertEqual(len(refreshed.load_history()), 1)
            stats = refreshed.load_savings_stats()
            self.assertEqual(stats.completed_jobs, 1)
            self.assertEqual(stats.net_saved_bytes, 60)
            self.assertAlmostEqual(stats.reduction_percent, 60.0)
            self.assertEqual(stats.smaller_jobs, 1)

    def test_weighted_percent_and_negative_results(self):
        stats = SavingsStats()
        root = Path("/tmp")
        self.assertTrue(stats.add(completed("a", root / "a", root / "b", 1000, 500)))
        self.assertTrue(stats.add(completed("b", root / "c", root / "d", 1000, 1500)))
        self.assertFalse(stats.add(completed("a", root / "a", root / "b", 1000, 500)))
        self.assertEqual(stats.completed_jobs, 2)
        self.assertEqual(stats.smaller_jobs, 1)
        self.assertEqual(stats.larger_jobs, 1)
        self.assertEqual(stats.net_saved_bytes, 0)
        self.assertEqual(stats.reduction_percent, 0.0)

    def test_history_retention_clear_and_replay_do_not_reset_lifetime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mgr = SettingsManager(root / "settings")
            for n in range(7):
                mgr.append_history(completed(
                    f"job-{n}", root / f"original{n}.mp4",
                    root / f"output{n}.mp4", 300, 200,
                ), max_entries=2)
            self.assertEqual(len(mgr.load_history()), 2)
            self.assertEqual(mgr.load_savings_stats().completed_jobs, 7)
            self.assertEqual(mgr.load_savings_stats().net_saved_bytes, 700)
            mgr.clear_history()
            self.assertEqual(mgr.load_history(), [])
            self.assertEqual(SettingsManager(root / "settings").load_savings_stats().completed_jobs, 7)

    def test_malformed_history_entry_does_not_hide_healthy_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mgr = SettingsManager(root / "settings")
            valid = completed("good", root / "gone.mp4", root / "also-gone.mp4", 200, 150)
            mgr.history_path.write_text(json.dumps([
                valid.to_dict(), {"job_id": "bad", "original_size_bytes": "not a number"},
                {"other": ["junk"]}, "junk",
            ]), encoding="utf-8")
            rows = mgr.load_history()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].job_id, "good")
            self.assertEqual(mgr.load_savings_stats().net_saved_bytes, 50)

    def test_concurrent_completions_do_not_lose_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mgr = SettingsManager(root / "settings")
            records = [
                completed(str(i), root / f"s{i}", root / f"o{i}", 400, 300)
                for i in range(15)
            ]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(mgr.append_history, records))
            totals = SettingsManager(root / "settings").load_savings_stats()
            self.assertEqual(totals.completed_jobs, 15)
            self.assertEqual(totals.net_saved_bytes, 1500)
            mgr.append_history(records[0])  # event replay should not double count
            self.assertEqual(mgr.load_savings_stats().completed_jobs, 15)

    def test_share_text_is_honest_about_free_space(self):
        stats = SavingsStats()
        stats.add(completed("one", Path("lost.mp4"), Path("after.mp4"), 100, 20))
        thai = share_summary(stats, "th", "80 B")
        self.assertIn("More Space. More Possibilities.", thai)
        self.assertIn("ต้นฉบับ", thai)
        self.assertIn("80 B", thai)
        self.assertIn("80.0%", thai)
        english = share_summary(stats, "en", "80 B")
        self.assertIn("More Space. More Possibilities.", english)


if __name__ == "__main__":
    unittest.main()
