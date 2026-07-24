from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.jobs import JobStore


class JobStoreTests(unittest.TestCase):
    def test_incomplete_job_is_recovered_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "jobs.db"
            store = JobStore(path)
            job, created = store.create("pipeline.find")
            self.assertTrue(created)
            store.mark_running(job.id)
            store.close()

            recovered_store = JobStore(path)
            self.assertEqual(recovered_store.recover_incomplete(), [job.id])
            recovered = recovered_store.get(job.id)
            self.assertEqual(recovered.status, "queued")
            self.assertIn("Recovered", recovered.error)
            recovered_store.close()

    def test_dedupe_key_returns_the_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JobStore(Path(tempdir) / "jobs.db")
            first, created = store.create("pipeline.tick", dedupe_key="tick:one")
            self.assertTrue(created)
            second, created = store.create("pipeline.tick", dedupe_key="tick:one")
            self.assertFalse(created)
            self.assertEqual(first.id, second.id)
            store.close()


if __name__ == "__main__":
    unittest.main()
