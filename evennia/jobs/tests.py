"""
Tests for whitelisted job queue.
"""

from django.test import override_settings

from evennia.jobs.queue import (enqueue_job, process_pending_jobs,
                                register_job_type)
from evennia.utils.test_resources import BaseEvenniaTest


def _sample_job(payload):
    _sample_job.last_payload = payload


_sample_job.last_payload = None


class TestJobQueue(BaseEvenniaTest):
    @override_settings(
        JOB_QUEUE_ENABLED=True,
        JOB_QUEUE_BACKEND="postgres",
        JOB_QUEUE_REGISTRY={"sample": "evennia.jobs.tests._sample_job"},
    )
    def test_enqueue_and_process(self):
        register_job_type("sample", "evennia.jobs.tests._sample_job")
        job_id = enqueue_job("sample", {"n": 3})
        self.assertTrue(job_id)
        n = process_pending_jobs(max_jobs=5)
        self.assertEqual(n, 1)
        self.assertEqual(_sample_job.last_payload, {"n": 3})

    def test_rejects_unknown_job_type(self):
        self.assertIsNone(enqueue_job("not_registered", {}))
