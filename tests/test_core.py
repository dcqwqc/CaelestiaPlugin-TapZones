import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from tapzoneslib.core import (
    AudioTapDetector,
    CLASSIFIER_MIN_SAMPLES,
    LOCATION_SIGNATURE_SIZE,
    Classifier,
    action_argv,
    audio_features,
    discover_accel,
    feature_vector,
    location_signature,
    enhanced_location_signature,
    capture_quality,
    ENHANCED_SIGNATURE_SIZE,
    EnhancedClassifier,
    microphone_transient_gate,
    trigger_decision,
    tap_gate_features,
)


class CoreTests(unittest.TestCase):
    def test_gate_features_are_lightweight_and_finite(self):
        pcm = np.zeros((960, 2), dtype="<i2")
        pcm[120, :] = 12000
        features = tap_gate_features(pcm)
        self.assertTrue(features["audio_ok"])
        self.assertGreater(features["peak"], 0)
        self.assertGreater(features["crest"], 1)
        self.assertEqual(
            set(features),
            {"audio_ok", "rms", "peak", "crest"},
        )

    def test_features_are_finite_and_stereo_lag(self):
        n = 1024
        left = (np.sin(np.arange(n) * 0.08) * 12000).astype("<i2")
        right = np.roll(left, 4)
        x = np.column_stack((left, right))
        f = audio_features(x)
        self.assertTrue(f["audio_ok"])
        self.assertLess(f["lag"], 0)
        self.assertTrue(all(np.isfinite(list(f.values())[1:])))

    def test_enhanced_signature_adds_dispersion_information(self):
        rate = 48000
        n = 5760

        def dispersive(spread, right_shift):
            x = np.zeros((n, 2), dtype=float)
            bands = [800, 1800, 3500, 6000, 9000, 12500, 16000, 20500]
            base = 2100
            for index, frequency in enumerate(bands):
                delay = (len(bands) - 1 - index) * spread
                start = base + delay
                k = np.arange(max(0, n - start))
                wave = np.sin(2*np.pi*frequency*k/rate) * np.exp(-k/(rate*0.010))
                if len(k):
                    x[start:, 0] += wave
                rstart = start + right_shift
                if rstart < n:
                    kr = np.arange(n-rstart)
                    x[rstart:, 1] += 0.8*np.sin(2*np.pi*frequency*kr/rate) * np.exp(-kr/(rate*0.010))
            x /= max(np.max(np.abs(x)), 1e-9)
            return (x * 18000).astype("<i2")

        near = enhanced_location_signature(dispersive(2, -2))
        far = enhanced_location_signature(dispersive(9, 4))
        self.assertEqual(near.shape, (ENHANCED_SIGNATURE_SIZE,))
        self.assertEqual(far.shape, (ENHANCED_SIGNATURE_SIZE,))
        self.assertTrue(np.all(np.isfinite(near)))
        self.assertGreater(
            np.linalg.norm(near[30:] - far[30:]),
            np.linalg.norm(near[:30] - far[:30]),
        )

    def test_capture_quality_keeps_valid_narrowband_impact(self):
        rate = 48000
        n = 5760
        pcm = np.zeros((n, 2), dtype=float)
        start = int(rate * 0.045)
        k = np.arange(n - start)
        tone = 0.09 * np.sin(2*np.pi*1800*k/rate) * np.exp(-k/(rate*0.012))
        pcm[start:, 0] = tone
        pcm[start:, 1] = tone * 0.85
        pcm = np.clip(pcm * 32768, -32768, 32767).astype("<i2")
        quality = capture_quality(pcm)
        self.assertTrue(quality["ok"])
        self.assertTrue(quality["lowBandCoverage"])
        self.assertEqual(quality["bands"], 1)

    def test_enhanced_classifier_needs_eight_samples_per_corner(self):
        classifier = EnhancedClassifier({})
        sample = np.zeros(ENHANCED_SIGNATURE_SIZE)
        for zone in ("TL", "TR", "BL", "BR"):
            for _ in range(7):
                classifier.add(zone, sample)
        self.assertFalse(classifier.profile_ready())
        for zone in ("TL", "TR", "BL", "BR"):
            classifier.add(zone, sample)
        self.assertTrue(classifier.profile_ready())

    def test_location_signature_is_fixed_size_and_stereo_sensitive(self):
        rate = 48000
        n = int(rate * 0.10)
        base = np.zeros(n)
        base[900] = 0.8
        for k in range(1, 900):
            if 900 + k < n:
                base[900 + k] = 0.8 * np.exp(-k / 260) * np.sin(k * 0.45)

        leftish = np.column_stack((base, np.roll(base, 8) * 0.55))
        rightish = np.column_stack((np.roll(base, 8) * 0.55, base))
        leftish = np.clip(leftish * 26000, -32768, 32767).astype("<i2")
        rightish = np.clip(rightish * 26000, -32768, 32767).astype("<i2")

        a = location_signature(leftish)
        b = location_signature(rightish)
        self.assertEqual(a.shape, (LOCATION_SIGNATURE_SIZE,))
        self.assertEqual(b.shape, (LOCATION_SIGNATURE_SIZE,))
        self.assertTrue(np.all(np.isfinite(a)))
        self.assertGreater(np.linalg.norm(a - b), 0.5)
        self.assertGreater(a[0], b[0])

    def test_end_to_end_acoustic_fingerprints_separate_four_corners(self):
        rng = np.random.default_rng(7)
        rate = 48000
        n = 4800

        def tap(zone, jitter):
            base = np.zeros(n)
            onset = 850 + jitter
            k = np.arange(n - onset)
            frequency = {"TL": 0.32, "TR": 0.38, "BL": 0.52, "BR": 0.60}[zone]
            base[onset:] = np.exp(-k / 300) * np.sin(k * frequency)
            left = base.copy()
            right = base.copy()
            if zone.endswith("L"):
                right = np.roll(right, 7) * 0.62
            else:
                left = np.roll(left, 7) * 0.62
            if zone.startswith("B"):
                left = np.r_[0, np.diff(left)] * 1.8
                right = np.r_[0, np.diff(right)] * 1.8
            pcm = np.column_stack((left, right))
            pcm += rng.normal(0, 0.001, pcm.shape)
            return np.clip(pcm * 24000, -32768, 32767).astype("<i2")

        classifier = Classifier({})
        for zone in ("TL", "TR", "BL", "BR"):
            for jitter in (-8, -4, 0, 4, 8, 12):
                classifier.add(zone, location_signature(tap(zone, jitter)))

        validation = classifier.validation_accuracy()
        self.assertIsNotNone(validation)
        self.assertEqual(validation["correct"], validation["total"])
        for zone in ("TL", "TR", "BL", "BR"):
            predicted, confidence = classifier.classify(
                location_signature(tap(zone, 2)), 0.0
            )
            self.assertEqual(predicted, zone)
            self.assertGreater(confidence, 0.9)

    def test_weighted_location_classifier_and_validation(self):
        rng = np.random.default_rng(42)
        centers = {
            "TL": np.r_[[-1.2, -0.8, -0.6], np.zeros(27)],
            "TR": np.r_[[1.2, 0.8, 0.6], np.zeros(27)],
            "BL": np.r_[[-1.1, -0.7, 0.5], np.ones(27) * 0.25],
            "BR": np.r_[[1.1, 0.7, -0.5], np.ones(27) * 0.25],
        }
        c = Classifier({})
        for zone, center in centers.items():
            for _ in range(7):
                c.add(zone, center + rng.normal(0, 0.035, 30))

        self.assertTrue(c.profile_ready())
        validation = c.validation_accuracy()
        self.assertIsNotNone(validation)
        self.assertGreaterEqual(validation["accuracy"], 0.9)
        for zone, center in centers.items():
            prediction, confidence = c.classify(center, 0.0)
            self.assertEqual(prediction, zone)
            self.assertGreater(confidence, 0.7)

    def test_profile_ready_requires_all_four_zones(self):
        c = Classifier({})
        sample = np.zeros(30)
        for zone in ("TL", "TR", "BL"):
            for _ in range(CLASSIFIER_MIN_SAMPLES):
                c.add(zone, sample)
        self.assertFalse(c.profile_ready())
        for _ in range(CLASSIFIER_MIN_SAMPLES - 1):
            c.add("BR", sample)
        self.assertFalse(c.profile_ready())
        c.add("BR", sample)
        self.assertTrue(c.profile_ready())

    def test_legacy_feature_vector_remains_finite(self):
        v = feature_vector(
            {"rms": 0.02, "ratio": 0.1, "lag": 0.02, "crest": 2.0},
            None,
            0.0,
        )
        self.assertEqual(v.shape, (13,))
        self.assertTrue(np.all(np.isfinite(v)))

    def test_actions_are_argv_only(self):
        self.assertEqual(action_argv("volume_up")[0], "wpctl")
        self.assertIsNone(action_argv("custom", []))
        self.assertIsNone(action_argv("unknown"))

    def test_accel_discovery_is_dynamic(self):
        device = discover_accel()
        self.assertTrue(
            device is None
            or (device / "name").read_text().strip() == "accel_3d"
        )

    def test_accel_policy_uses_one_shared_gate(self):
        transient = {"audio_ok": True, "rms": 0.02, "crest": 3.0}
        quiet = {"audio_ok": True, "rms": 0.02, "crest": 1.5}

        self.assertFalse(
            trigger_decision("required", True, 0, 0.2, transient).triggered
        )
        self.assertEqual(
            trigger_decision("required", False, 0, 0.2, transient).source,
            "unavailable",
        )
        preferred = trigger_decision(
            "preferred", True, 0, 0.2, transient
        )
        self.assertEqual(
            (preferred.triggered, preferred.source, preferred.degraded),
            (True, "microphone", False),
        )
        preferred_fallback = trigger_decision(
            "preferred", True, 0.3, 0.2, {"audio_ok": False}
        )
        self.assertEqual(
            (
                preferred_fallback.triggered,
                preferred_fallback.source,
                preferred_fallback.degraded,
            ),
            (True, "accelerometer", True),
        )
        self.assertFalse(microphone_transient_gate(quiet))

    def test_adaptive_tap_detector_rejects_idle_noise(self):
        detector = AudioTapDetector()
        now = 0.0
        samples = (
            (0.002, 3.2),
            (0.004, 3.8),
            (0.0065, 4.1),
            (0.003, 2.8),
            (0.0055, 3.5),
        ) * 4
        for rms, crest in samples:
            now += 0.02
            self.assertFalse(
                detector.process(
                    {
                        "audio_ok": True,
                        "rms": rms,
                        "crest": crest,
                        "peak": rms * crest,
                    },
                    55,
                    now,
                )
            )

    def test_detector_rearms_on_stable_loud_baseline(self):
        detector = AudioTapDetector()
        detector.reset(require_quiet=True)
        now = 0.0
        ambient = {
            "audio_ok": True,
            "rms": 0.025,
            "crest": 3.0,
            "peak": 0.075,
        }
        for _ in range(4):
            now += 0.02
            self.assertFalse(detector.process(ambient, 55, now))
        self.assertTrue(detector.ready)

        tap = {
            "audio_ok": True,
            "rms": 0.055,
            "crest": 4.0,
            "peak": 0.22,
        }
        now += 0.02
        self.assertTrue(detector.process(tap, 55, now))

    def test_one_physical_impact_emits_one_edge_until_release(self):
        detector = AudioTapDetector()
        now = 0.0
        quiet = {
            "audio_ok": True,
            "rms": 0.0025,
            "crest": 2.2,
            "peak": 0.0055,
        }
        for _ in range(3):
            now += 0.02
            detector.process(quiet, 55, now)

        hit = {
            "audio_ok": True,
            "rms": 0.032,
            "crest": 3.0,
            "peak": 0.096,
        }
        ringing = {
            "audio_ok": True,
            "rms": 0.022,
            "crest": 2.4,
            "peak": 0.053,
        }
        now += 0.02
        self.assertTrue(detector.process(hit, 55, now))
        for frame in (ringing, ringing):
            now += 0.02
            self.assertFalse(detector.process(frame, 55, now))

        for _ in range(10):
            now += 0.02
            detector.process(quiet, 55, now)
        now += 0.02
        self.assertTrue(detector.process(hit, 55, now))


if __name__ == "__main__":
    unittest.main()
