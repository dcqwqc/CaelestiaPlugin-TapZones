import importlib.util
import os
import pathlib
import sys
import unittest


ROOT = pathlib.Path(os.path.join(os.path.dirname(__file__), ".."))
SPEC = importlib.util.spec_from_file_location(
    "yoga_ish_watchdog_safe", ROOT / "extras" / "yoga-ish-watchdog-safe.py"
)
WATCHDOG = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WATCHDOG
SPEC.loader.exec_module(WATCHDOG)


class SafeWatchdogTests(unittest.TestCase):
    def test_stationary_samples_and_missing_accel_never_restart(self):
        restarts = []
        stationary = (0.0, 0.0, 9.81)
        watchdog = WATCHDOG.SafeIioWatchdog(
            discover=lambda: pathlib.Path("/pretend/accel"),
            read=lambda _device: stationary,
            restart=lambda: restarts.append("restart"),
        )
        for _ in range(8):
            self.assertEqual(watchdog.poll().state, "ok")
        self.assertEqual(restarts, [])

        missing = WATCHDOG.SafeIioWatchdog(
            discover=lambda: None,
            restart=lambda: restarts.append("restart"),
        )
        self.assertEqual(missing.poll().state, "accelerometer-unavailable")
        self.assertEqual(restarts, [])

    def test_restarts_only_after_repeated_sysfs_read_failures(self):
        restarts = []

        def fail_read(_device):
            raise OSError("sysfs read failed")

        watchdog = WATCHDOG.SafeIioWatchdog(
            discover=lambda: pathlib.Path("/pretend/accel"),
            read=fail_read,
            restart=lambda: restarts.append("restart"),
            failure_threshold=3,
        )
        self.assertFalse(watchdog.poll().restarted)
        self.assertFalse(watchdog.poll().restarted)
        self.assertTrue(watchdog.poll().restarted)
        self.assertEqual(restarts, ["restart"])


if __name__ == "__main__":
    unittest.main()
