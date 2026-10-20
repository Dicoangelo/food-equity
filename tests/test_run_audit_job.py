"""Process supervision tests: no actual subprocess, socket, or cloud calls."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.audit_postings import manifest_template
from scripts.run_audit_job import JobError, run_job, terminate_child, wait_for_toolbox


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "manifest.json"
        self.path.write_text(json.dumps(manifest_template()))

    def context(self, children):
        stack = ExitStack()
        stack.enter_context(patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project",
                                                     "BIGQUERY_DATASET": "a4i_food"}))
        spawn = stack.enter_context(patch("scripts.run_audit_job.subprocess.Popen", side_effect=children))
        stack.enter_context(patch("scripts.run_audit_job.port_open", return_value=False))
        stack.enter_context(patch("scripts.run_audit_job.wait_for_toolbox"))
        stack.enter_context(patch("scripts.run_audit_job.time.sleep"))
        return stack, spawn

    def test_success_and_nonzero_child_status_are_preserved(self):
        for status in (0, 7, -15):
            toolbox = Mock()
            toolbox.poll.return_value = None
            auditor = Mock(returncode=status)
            auditor.poll.return_value = status
            stack, spawn = self.context([toolbox, auditor])
            with stack:
                result = run_job(self.path, "2026-10-20T16:00:00-07:00")
            self.assertEqual(result, status if status >= 0 else 143)
            toolbox.terminate.assert_called_once()
            auditor.terminate.assert_not_called()
            self.assertIn("--address=127.0.0.1", spawn.call_args_list[0].args[0])
            self.assertEqual(spawn.call_args_list[1].kwargs["env"]["TOOLBOX_URL"], "http://127.0.0.1:5000")
            self.assertNotIn("--output", spawn.call_args_list[1].args[0])
            self.assertIn("--compact", spawn.call_args_list[1].args[0])

    def test_audit_spawn_failure_cleans_up_toolbox(self):
        toolbox = Mock()
        toolbox.poll.return_value = None
        stack, _ = self.context([toolbox, OSError("spawn failed")])
        with stack, self.assertRaises(OSError):
            run_job(self.path, "2026-10-20T16:00:00-07:00")
        toolbox.terminate.assert_called_once()

    def test_toolbox_death_stops_running_audit_only(self):
        toolbox = Mock()
        toolbox.poll.return_value = 3
        auditor = Mock()
        auditor.poll.return_value = None
        stack, spawn = self.context([toolbox, auditor])
        with stack, self.assertRaises(JobError) as error:
            run_job(self.path, "2026-10-20T16:00:00-07:00")
        self.assertEqual(error.exception.code, "toolbox_exited")
        auditor.terminate.assert_called_once()
        toolbox.terminate.assert_not_called()
        self.assertEqual(spawn.call_count, 2)

    def test_timeout_terminates_both_owned_children(self):
        toolbox, auditor = Mock(), Mock()
        toolbox.poll.return_value = auditor.poll.return_value = None
        stack, _ = self.context([toolbox, auditor])
        with stack, patch("scripts.run_audit_job.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(JobError) as error:
                run_job(self.path, "2026-10-20T16:00:00-07:00", timeout_seconds=1)
        self.assertEqual(error.exception.exit_code, 124)
        auditor.terminate.assert_called_once()
        toolbox.terminate.assert_called_once()

    def test_occupied_port_starts_and_stops_nothing(self):
        stack, spawn = self.context([])
        with stack, patch("scripts.run_audit_job.port_open", return_value=True), self.assertRaises(JobError):
            run_job(self.path, "2026-10-20T16:00:00-07:00")
        spawn.assert_not_called()

    def test_manifest_failure_starts_nothing(self):
        self.path.write_text("{}")
        stack, spawn = self.context([])
        with stack, self.assertRaises(ValueError):
            run_job(self.path, "2026-10-20T16:00:00-07:00")
        spawn.assert_not_called()

    def test_startup_timeout_also_cleans_up_owned_toolbox(self):
        toolbox = Mock()
        toolbox.poll.return_value = None
        stack, spawn = self.context([toolbox])
        with stack, patch("scripts.run_audit_job.wait_for_toolbox",
                          side_effect=JobError("toolbox_startup_timeout", "Timed out.", 124)):
            with self.assertRaises(JobError):
                run_job(self.path, "2026-10-20T16:00:00-07:00")
        toolbox.terminate.assert_called_once()
        self.assertEqual(spawn.call_count, 1)

    def test_termination_escalates_to_kill_and_waits(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("owned-child", 5), 0]
        terminate_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_readiness_is_bounded_even_when_process_stays_alive(self):
        child = Mock()
        child.poll.return_value = None
        with patch("scripts.run_audit_job.port_open", return_value=False), \
             patch("scripts.run_audit_job.time.monotonic", side_effect=[0, 0, 2]), \
             patch("scripts.run_audit_job.time.sleep"), self.assertRaises(JobError) as error:
            wait_for_toolbox(child, 1)
        self.assertEqual(error.exception.exit_code, 124)

    def test_readiness_rechecks_process_after_socket_connects(self):
        child = Mock()
        child.poll.side_effect = [None, 4]
        with patch("scripts.run_audit_job.port_open", return_value=True), \
             self.assertRaises(JobError) as error:
            wait_for_toolbox(child, 1)
        self.assertEqual(error.exception.code, "toolbox_exited")


if __name__ == "__main__":
    unittest.main()
