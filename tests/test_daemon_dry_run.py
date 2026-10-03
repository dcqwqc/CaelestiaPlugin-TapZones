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
        daemon.audio_detector = Mock()
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

    def test_live_test_stays_active_and_accepts_consecutive_taps(self):
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
        classifier.classify.side_effect = [("BR", 0.86), ("TL", 0.79)]
        daemon = self._daemon_for_finish(classifier, "test")

        def capture():
            return {
                "mode": "test",
                "frames": [np.zeros((960, 2), dtype="<i2")] * 5,
                "source": "microphone",
                "degraded": True,
                "impulse": 0.0,
                "audioRms": 0.03,
                "postFrames": 3,
            }

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=np.ones(30),
        ), patch("tapzoneslib.daemon.run_action") as action:
            daemon._finish_fingerprint_capture({"confidence": 72})

            self.assertEqual(daemon.status["testEvent"]["zone"], "BR")
            self.assertEqual(daemon.status["testEvent"]["serial"], 1)
            self.assertTrue(daemon.status["testActive"])
            self.assertFalse(daemon.status["testArmed"])
            self.assertTrue(daemon.status["testEvent"]["accepted"])

            daemon.fingerprint_capture = capture()
            daemon._finish_fingerprint_capture({"confidence": 72})

        action.assert_not_called()
        self.assertEqual(classifier.classify.call_count, 2)
        self.assertEqual(daemon.status["testEvent"]["zone"], "TL")
        self.assertEqual(daemon.status["testEvent"]["serial"], 2)
        self.assertEqual(daemon.status["testEvent"]["fingerprintVersion"], 2)
        self.assertTrue(daemon.status["testActive"])
        self.assertFalse(daemon.status["testArmed"])
        self.assertTrue(daemon.status["testEvent"]["accepted"])
        self.assertGreaterEqual(daemon.audio_detector.reset.call_count, 2)

    def test_failed_live_test_capture_rearms_instead_of_stopping(self):
        classifier = Mock()
        classifier.profile.return_value = {
            zone: {"count": 6} for zone in ("TL", "TR", "BL", "BR")
        }
        classifier.profile_ready.return_value = True
        daemon = self._daemon_for_finish(classifier, "test")

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=None,
        ):
            daemon._finish_fingerprint_capture({"confidence": 72})

        self.assertTrue(daemon.status["testActive"])
        self.assertFalse(daemon.status["testArmed"])
        self.assertEqual(
            daemon.status["testEvent"]["reason"],
            "fingerprint-failed",
        )
        daemon.audio_detector.reset.assert_called_once_with(
            require_quiet=True
        )

    def test_enhanced_calibration_stores_only_good_v3_sample(self):
        legacy = Mock()
        legacy.profile.return_value = {}
        legacy.profile_ready.return_value = True
        enhanced = Mock()
        enhanced.profile.return_value = {}
        enhanced.profile_ready.return_value = False
        enhanced.validation_accuracy.return_value = None
        enhanced.axis_model_info.return_value = None
        enhanced.sample_is_outlier.return_value = False

        daemon = self._daemon_for_finish(legacy, "enhanced-calibration")
        daemon.enhanced_classifier = enhanced
        daemon.status.update({
            "calibration": {
                "mode": "enhanced-auto",
                "zone": "TL",
                "counts": {"TL": 0, "TR": 0, "BL": 0, "BR": 0},
                "rejected": 0,
                "complete": False,
            },
            "enhancedProfileReady": False,
            "enhancedQuality": None,
        })
        signature = np.linspace(-1, 1, 58)

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=np.zeros(30),
        ), patch(
            "tapzoneslib.daemon.enhanced_location_signature",
            return_value=signature,
        ), patch(
            "tapzoneslib.daemon.capture_quality",
            return_value={"ok": True, "reason": None, "snrDb": 18, "bands": 7},
        ), patch(
            "tapzoneslib.daemon.save_enhanced_classifier",
        ), patch.object(
            daemon, "_advance_enhanced_calibration",
        ) as advance:
            daemon._finish_fingerprint_capture({"confidence": 72})

        enhanced.add.assert_called_once()
        self.assertEqual(enhanced.add.call_args.args[0], "TL")
        np.testing.assert_allclose(enhanced.add.call_args.args[1], signature)
        self.assertEqual(daemon.status["calibration"]["counts"]["TL"], 1)
        legacy.add.assert_not_called()
        advance.assert_called_once()

    def test_enhanced_calibration_rejects_bad_capture_without_training(self):
        legacy = Mock()
        legacy.profile.return_value = {}
        legacy.profile_ready.return_value = True
        enhanced = Mock()
        enhanced.profile.return_value = {}
        enhanced.profile_ready.return_value = False
        enhanced.validation_accuracy.return_value = None
        enhanced.axis_model_info.return_value = None

        daemon = self._daemon_for_finish(legacy, "enhanced-calibration")
        daemon.enhanced_classifier = enhanced
        daemon.audio_detector = Mock()
        daemon.status.update({
            "calibration": {
                "mode": "enhanced-auto",
                "zone": "BR",
                "counts": {"TL": 0, "TR": 0, "BL": 0, "BR": 0},
                "rejected": 0,
                "complete": False,
            },
            "enhancedProfileReady": False,
            "enhancedQuality": None,
        })

        with patch(
            "tapzoneslib.daemon.location_signature",
            return_value=np.zeros(30),
        ), patch(
            "tapzoneslib.daemon.enhanced_location_signature",
            return_value=np.zeros(58),
        ), patch(
            "tapzoneslib.daemon.capture_quality",
            return_value={"ok": False, "reason": "too-noisy"},
        ):
            daemon._finish_fingerprint_capture({"confidence": 72})

        enhanced.add.assert_not_called()
        self.assertEqual(daemon.status["calibration"]["counts"]["BR"], 0)
        self.assertEqual(daemon.status["calibration"]["rejected"], 1)
        self.assertEqual(daemon.status["lastCalibrationReject"], "too-noisy")

    def test_enhanced_resume_sequence_keeps_saved_progress(self):
        daemon = Daemon.__new__(Daemon)
        counts = {"TL": 9, "TR": 10, "BL": 9, "BR": 9}
        remaining = daemon._remaining_enhanced_sequence(counts)
        self.assertEqual(len(remaining), 11)
        self.assertEqual(remaining[0], "BL")
        self.assertEqual(remaining.count("TL"), 3)
        self.assertEqual(remaining.count("TR"), 2)
        self.assertEqual(remaining.count("BL"), 3)
        self.assertEqual(remaining.count("BR"), 3)

    def test_enhanced_sequence_is_balanced_and_interleaved(self):
        sequence = Daemon._enhanced_sequence(8)
        self.assertEqual(len(sequence), 32)
        for zone in ("TL", "TR", "BL", "BR"):
            self.assertEqual(sequence.count(zone), 8)
        for offset in range(0, len(sequence), 4):
            self.assertEqual(
                set(sequence[offset:offset + 4]),
                {"TL", "TR", "BL", "BR"},
            )

    def test_v3_never_promotes_below_v2_validation(self):
        daemon = Daemon.__new__(Daemon)
        daemon.classifier = Mock()
        daemon.enhanced_classifier = Mock()
        daemon.classifier.profile_ready.return_value = True
        daemon.enhanced_classifier.profile_ready.return_value = True
        daemon.status = {
            "profileReady": True,
            "enhancedProfileReady": True,
            "calibrationQuality": {"accuracy": 0.9167},
            "enhancedQuality": {"accuracy": 0.90},
        }
        daemon._refresh_active_model()
        self.assertEqual(daemon.status["activeModel"], "v2-axis")
        self.assertEqual(daemon.status["fingerprintVersion"], 2)

        daemon.status["enhancedQuality"] = {"accuracy": 0.9583}
        daemon._refresh_active_model()
        self.assertEqual(daemon.status["activeModel"], "v3-dispersion")
        self.assertEqual(daemon.status["fingerprintVersion"], 3)

    def test_v3_requires_ninety_percent_even_without_v2(self):
        daemon = Daemon.__new__(Daemon)
        daemon.classifier = Mock()
        daemon.enhanced_classifier = Mock()
        daemon.classifier.profile_ready.return_value = False
        daemon.enhanced_classifier.profile_ready.return_value = True
        daemon.status = {
            "profileReady": False,
            "enhancedProfileReady": True,
            "calibrationQuality": None,
            "enhancedQuality": {"accuracy": 0.875},
        }
        daemon._refresh_active_model()
        self.assertEqual(daemon.status["activeModel"], "v2-axis")
        daemon.status["enhancedQuality"] = {"accuracy": 0.90}
        daemon._refresh_active_model()
        self.assertEqual(daemon.status["activeModel"], "v3-dispersion")

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
