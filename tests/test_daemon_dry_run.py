import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tapzoneslib.daemon import Daemon


class DaemonV2Tests(unittest.TestCase):
    def test_action_dry_run_and_status(self):
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
        env["XDG_STATE_HOME"] = tempfile.mkdtemp()
        env["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts/tapzones.py"),
                "action-test",
                "volume_up",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertTrue(json.loads(proc.stdout)["dry_run"])

        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts/tapzones.py"),
                "status",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        status = json.loads(proc.stdout)
        self.assertFalse(status["running"])
        self.assertFalse(status["profileReady"])
        self.assertEqual(status["fingerprintVersion"], 2)

    def _daemon_for_finish(self, classifier, mode):
        daemon = Daemon.__new__(Daemon)
        daemon.classifier = classifier
        daemon.pending = []
        daemon.last_accept = 0.0
        daemon.test_serial = 0
        daemon.fingerprint_capture = {
            "mode": mode,
            "frames": [np.zeros((960, 2), dtype="<i2")] * 5,
            "source": "microphone",
            "degraded": True,
            "impulse": 0.0,
            "audioRms": 0.03,
            "postFrames": 3,
        }
        daemon.status = {
            "calibration": None,
            "profile": classifier.profile(),
            "profileReady": classifier.profile_ready(),
            "calibrationQuality": None,
            "testActive": mode == "test",
            "testArmed": mode == "test",
            "testEvent": None,
            "capturingFingerprint": True,
            "lastEvent": None,
        }
        return daemon

    def test_calibration_stores_v2_fingerprint(self):
        classifier = Mock()
        classifier.profile.return_value = {}
        classifier.profile_ready.return_value = False
        classifier.validation_accuracy.return_value = None
        daemon = self._daemon_for_finish(classifier, "calibration")
        daemon.status["calibration"] = {
            "zone": "TR",
            "need": 6,
            "have": 0,
            "armed": False,
        }
        signature = np.linspace(-1, 1, 30)

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=signature,
        ), patch("tapzoneslib.daemon.save_classifier"):
            daemon._finish_fingerprint_capture({"confidence": 72})

        classifier.add.assert_called_once()
        self.assertEqual(classifier.add.call_args.args[0], "TR")
        np.testing.assert_allclose(classifier.add.call_args.args[1], signature)
        self.assertEqual(daemon.status["calibration"]["have"], 1)

    def test_live_test_classifies_completed_fingerprint_without_action(self):
        classifier = Mock()
        classifier.profile.return_value = {
            zone: {"count": 6} for zone in ("TL", "TR", "BL", "BR")
        }
        classifier.profile_ready.return_value = True
        classifier.validation_accuracy.return_value = {
            "accuracy": 0.9,
            "correct": 22,
            "total": 24,
        }
        classifier.classify.return_value = ("BR", 0.86)
        daemon = self._daemon_for_finish(classifier, "test")

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=np.ones(30),
        ), patch("tapzoneslib.daemon.run_action") as action:
            daemon._finish_fingerprint_capture({"confidence": 72})

        action.assert_not_called()
        classifier.classify.assert_called_once()
        self.assertEqual(daemon.status["testEvent"]["zone"], "BR")
        self.assertTrue(daemon.status["testEvent"]["accepted"])
        self.assertEqual(
            daemon.status["testEvent"]["fingerprintVersion"],
            2,
        )
        self.assertFalse(daemon.status["testActive"])

    def test_normal_mode_rejects_uncertain_location(self):
        classifier = Mock()
        classifier.profile.return_value = {
            zone: {"count": 6} for zone in ("TL", "TR", "BL", "BR")
        }
        classifier.profile_ready.return_value = True
        classifier.validation_accuracy.return_value = None
        classifier.classify.return_value = (None, 0.55)
        daemon = self._daemon_for_finish(classifier, "normal")

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=np.ones(30),
        ):
            daemon._finish_fingerprint_capture({"confidence": 72})

        self.assertEqual(daemon.pending, [])
        self.assertIsNone(daemon.status["lastEvent"])


if __name__ == "__main__":
    unittest.main()
