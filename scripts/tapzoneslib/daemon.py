import json
import queue
import subprocess
import threading
import time
from collections import deque

import numpy as np

from .core import (
    AudioTapDetector,
    CLASSIFIER_MIN_SAMPLES,
    DEFAULT_CALIBRATION_SAMPLES,
    ENHANCED_MAX_SAMPLES,
    ENHANCED_MIN_SAMPLES,
    ENHANCED_TARGET_SAMPLES,
    accel_sample,
    audio_features,
    tap_gate_features,
    atomic_json,
    discover_accel,
    effective_config,
    load_classifier,
    load_enhanced_classifier,
    location_signature,
    enhanced_location_signature,
    capture_quality,
    run_action,
    save_classifier,
    save_enhanced_classifier,
    xdg,
)


class Daemon:
    def __init__(self):
        self.accel_device = discover_accel()
        self.classifier = load_classifier()
        self.enhanced_classifier = load_enhanced_classifier()
        self.q = queue.Queue(maxsize=16)
        self.prev_accel = None
        self.next_accel_discovery = 0.0
        self.cached_config = effective_config()
        self.next_config_refresh = 0.0
        self.last_accept = 0.0
        self.pending = []

        self.audio_detector = AudioTapDetector()
        self.accel_latched = False
        self.accel_quiet_frames = 3

        self.calibration_armed_at = 0.0
        self.test_armed_at = 0.0
        self.test_serial = 0

        # One 20 ms pre-roll frame + trigger frame + 3 post frames gives the
        # fingerprint enough propagation/decay information without slowing tap
        # onset detection itself.
        self.audio_history = deque(maxlen=2)
        self.fingerprint_capture = None

        self.status = {
            "running": True,
            "audio": "starting",
            "accel": str(self.accel_device or "unavailable"),
            "calibration": None,
            "lastEvent": None,
            "lastAction": None,
            "profile": self.classifier.profile(),
            "profileReady": self.classifier.profile_ready(),
            "calibrationQuality": self.classifier.validation_accuracy(),
            "axisModel": self.classifier.axis_model_info(),
            "enhancedProfile": self.enhanced_classifier.profile(),
            "enhancedProfileReady": self.enhanced_classifier.profile_ready(),
            "enhancedQuality": self.enhanced_classifier.validation_accuracy(),
            "enhancedAxisModel": self.enhanced_classifier.axis_model_info(),
            "activeModel": "v2-axis",
            "triggerSource": "unknown",
            "degraded": False,
            "testActive": False,
            "testEvent": None,
            "testArmed": False,
            "audioRms": 0.0,
            "noiseFloor": self.audio_detector.noise_floor,
            "tapRmsThreshold": self.audio_detector.last_rms_threshold,
            "tapPeakThreshold": self.audio_detector.last_peak_threshold,
            "detectorReady": False,
            "capturingFingerprint": False,
            "fingerprintVersion": 2,
            "lastCalibrationReject": None,
        }
        self._refresh_active_model()

    def publish(self):
        atomic_json(xdg("runtime", "status.json"), self.status)

    def refresh_profile_status(self, recompute_quality=True):
        self.status["profile"] = self.classifier.profile()
        self.status["profileReady"] = self.classifier.profile_ready()
        if recompute_quality:
            self.status["calibrationQuality"] = self.classifier.validation_accuracy()
            self.status["axisModel"] = self.classifier.axis_model_info()

    def refresh_enhanced_status(self):
        self.status["enhancedProfile"] = self.enhanced_classifier.profile()
        self.status["enhancedProfileReady"] = self.enhanced_classifier.profile_ready()
        self.status["enhancedQuality"] = self.enhanced_classifier.validation_accuracy()
        self.status["enhancedAxisModel"] = self.enhanced_classifier.axis_model_info()
        self._refresh_active_model()

    def _refresh_active_model(self):
        legacy_quality = self.status.get("calibrationQuality") or {}
        enhanced_quality = self.status.get("enhancedQuality") or {}
        legacy_accuracy = float(legacy_quality.get("accuracy", 0.0) or 0.0)
        enhanced_accuracy = float(enhanced_quality.get("accuracy", 0.0) or 0.0)
        enhanced_ready = bool(self.status.get("enhancedProfileReady"))
        legacy_ready = bool(self.status.get("profileReady"))
        required = max(0.90, legacy_accuracy if legacy_ready else 0.0)
        use_enhanced = enhanced_ready and enhanced_accuracy >= required
        self.status["activeModel"] = "v3-dispersion" if use_enhanced else "v2-axis"
        self.status["fingerprintVersion"] = 3 if use_enhanced else 2

    def active_classifier(self):
        if self.status.get("activeModel") == "v3-dispersion":
            return self.enhanced_classifier
        return self.classifier

    def active_profile_ready(self):
        return self.active_classifier().profile_ready()

    @staticmethod
    def _enhanced_sequence(rounds=ENHANCED_MAX_SAMPLES):
        patterns = (
            ("TL", "BR", "TR", "BL"),
            ("TR", "BL", "BR", "TL"),
            ("BL", "TR", "TL", "BR"),
            ("BR", "TL", "BL", "TR"),
        )
        sequence = []
        for index in range(rounds):
            sequence.extend(patterns[index % len(patterns)])
        return sequence

    def _remaining_enhanced_sequence(self, counts):
        seen = {zone: 0 for zone in ("TL", "TR", "BL", "BR")}
        remaining = []
        for zone in self._enhanced_sequence():
            seen[zone] += 1
            if seen[zone] > int(counts.get(zone, 0)):
                remaining.append(zone)
        return remaining

    def _start_enhanced_calibration(self, now):
        counts = {
            zone: len(self.enhanced_classifier.samples.get(zone, []))
            for zone in ("TL", "TR", "BL", "BR")
        }
        # Resume a partial research calibration instead of throwing away good
        # physical taps after a daemon/UI reload. Use Reset v3 explicitly when
        # a completely fresh dataset is desired.
        if all(count >= ENHANCED_MAX_SAMPLES for count in counts.values()):
            self.enhanced_classifier.samples = {}
            self.enhanced_classifier._axis_cache = None
            save_enhanced_classifier(self.enhanced_classifier)
            counts = {zone: 0 for zone in ("TL", "TR", "BL", "BR")}
            self.refresh_enhanced_status()

        self.drain_audio()
        self.audio_detector.reset(require_quiet=True)
        self.calibration_armed_at = now + 0.35
        sequence = self._remaining_enhanced_sequence(counts)
        self.status["lastCalibrationReject"] = None
        self.status["calibration"] = {
            "mode": "enhanced-auto",
            "zone": sequence[0] if sequence else None,
            "sequence": sequence,
            "step": 0,
            "minimumTotal": ENHANCED_TARGET_SAMPLES * 4,
            "maxTotal": ENHANCED_MAX_SAMPLES * 4,
            "counts": counts,
            "rejected": 0,
            "rejectReasons": {},
            "resumed": any(counts.values()),
            "armed": False,
            "complete": not bool(sequence),
        }
        self.status["testActive"] = False
        self.status["testEvent"] = None
        self.pending = []

    def _advance_enhanced_calibration(self):
        calibration = self.status.get("calibration")
        if not calibration or calibration.get("mode") != "enhanced-auto":
            return
        calibration["step"] += 1
        counts = calibration["counts"]
        min_count = min(counts.values())

        if min_count >= ENHANCED_TARGET_SAMPLES:
            self.refresh_enhanced_status()
            quality = self.status.get("enhancedQuality") or {}
            accuracy = float(quality.get("accuracy", 0.0) or 0.0)
            legacy = self.status.get("calibrationQuality") or {}
            target = max(0.90, float(legacy.get("accuracy", 0.0) or 0.0))
            if accuracy >= target or min_count >= ENHANCED_MAX_SAMPLES:
                calibration["complete"] = True
                calibration["armed"] = False
                calibration["quality"] = quality
                self._refresh_active_model()
                return

        if calibration["step"] >= len(calibration["sequence"]):
            calibration["complete"] = True
            calibration["armed"] = False
            self.refresh_enhanced_status()
            calibration["quality"] = self.status.get("enhancedQuality")
            return

        calibration["zone"] = calibration["sequence"][calibration["step"]]
        calibration["armed"] = False
        self.audio_detector.reset(require_quiet=True)
        self.calibration_armed_at = time.monotonic() + 0.28

    def drain_audio(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.audio_history.clear()
        self.fingerprint_capture = None
        self.status["capturingFingerprint"] = False

    def capture(self):
        process = None
        try:
            process = subprocess.Popen(
                [
                    "pw-record",
                    "--raw",
                    "--format",
                    "s16",
                    "--rate",
                    "48000",
                    "--channels",
                    "2",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self.status["audio"] = "capturing-default-source"

            frame_bytes = 3840  # 20 ms, stereo s16 @ 48 kHz
            while True:
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    raise RuntimeError("pw-record ended")
                frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).copy()
                try:
                    self.q.put_nowait(frame)
                except queue.Full:
                    try:
                        self.q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.q.put_nowait(frame)
                    except queue.Full:
                        pass
        except Exception:
            self.status["audio"] = "unavailable"
        finally:
            if process and process.poll() is None:
                process.terminate()

    def command(self):
        path = xdg("runtime", "command.json")
        if not path.exists():
            return

        command = json.loads(path.read_text())
        path.unlink(missing_ok=True)
        kind = command.get("type")
        now = time.monotonic()

        if kind == "calibrate-enhanced":
            self._start_enhanced_calibration(now)

        elif kind == "reset-enhanced":
            self.enhanced_classifier.samples = {}
            self.enhanced_classifier._axis_cache = None
            save_enhanced_classifier(self.enhanced_classifier)
            self.status["calibration"] = None
            self.status["lastCalibrationReject"] = None
            self.drain_audio()
            self.refresh_enhanced_status()

        elif kind == "calibrate":
            zone = command["zone"]
            self.classifier.samples.pop(zone, None)
            self.classifier._axis_cache = None
            save_classifier(self.classifier)
            self.refresh_profile_status()
            self._refresh_active_model()
            self.drain_audio()
            self.audio_detector.reset(require_quiet=True)
            self.calibration_armed_at = now + 0.22
            self.pending = []
            self.status["testActive"] = False
            self.status["testEvent"] = None
            requested = int(
                command.get("count", DEFAULT_CALIBRATION_SAMPLES)
                or DEFAULT_CALIBRATION_SAMPLES
            )
            self.status["calibration"] = {
                "zone": zone,
                "need": max(
                    CLASSIFIER_MIN_SAMPLES + 1,
                    min(30, requested),
                ),
                "have": 0,
                "armed": False,
            }

        elif kind == "reset":
            self.classifier.samples = {}
            self.classifier._axis_cache = None
            save_classifier(self.classifier)
            self.status["calibration"] = None
            self.status["testActive"] = False
            self.status["testEvent"] = None
            self.pending = []
            self.drain_audio()
            self.refresh_profile_status()
            self._refresh_active_model()

        elif kind == "action-test":
            self.status["lastAction"] = run_action(
                command.get("action", "none"),
                True,
                command.get("custom"),
            )

        elif kind == "test-start":
            self.drain_audio()
            self.audio_detector.reset(require_quiet=True)
            self.test_armed_at = now + 0.22
            self.status["testActive"] = True
            self.status["testEvent"] = None
            self.status["calibration"] = None
            self.pending = []

        elif kind == "test-stop":
            self.status["testActive"] = False
            self.pending = []
            self.drain_audio()

    def flush(self, cfg):
        if not self.active_profile_ready():
            self.pending = []
            return
        window = int(cfg.get("multiTapWindowMs", 420)) / 1000
        if not self.pending or time.monotonic() - self.pending[-1][0] < window:
            return

        taps = min(3, len(self.pending))
        zone = self.pending[-1][1]
        self.pending = []
        try:
            action = (
                json.loads(cfg.get("actionsJson", "{}"))
                .get(zone, {})
                .get(str(taps), "none")
            )
        except json.JSONDecodeError:
            action = "none"

        custom = None
        if isinstance(action, dict):
            custom = action.get("argv")
            action = action.get("type", "custom")
        self.status["lastAction"] = {
            "zone": zone,
            "taps": taps,
            "action": action,
            **run_action(action, custom=custom),
        }

    def accel_event(self, impulse, threshold):
        candidate = impulse >= threshold
        if candidate:
            triggered = not self.accel_latched and self.accel_quiet_frames >= 2
            self.accel_latched = True
            self.accel_quiet_frames = 0
            return triggered

        if impulse <= threshold * 0.35:
            self.accel_quiet_frames = min(20, self.accel_quiet_frames + 1)
            if self.accel_quiet_frames >= 2:
                self.accel_latched = False
        else:
            self.accel_quiet_frames = 0
        return False

    def _begin_fingerprint_capture(
        self,
        mode,
        frame,
        source,
        degraded,
        impulse,
        audio_rms,
    ):
        frames = list(self.audio_history)
        if frame is not None:
            frames.append(frame)
        self.fingerprint_capture = {
            "mode": mode,
            "frames": frames,
            "source": source,
            "degraded": degraded,
            "impulse": impulse,
            "audioRms": audio_rms,
            "postFrames": 0,
        }
        self.status["capturingFingerprint"] = True
        if self.status.get("calibration"):
            self.status["calibration"]["armed"] = False
        self.status["testArmed"] = False

    def _finish_fingerprint_capture(self, cfg):
        capture = self.fingerprint_capture
        self.fingerprint_capture = None
        self.status["capturingFingerprint"] = False
        if not capture or not capture["frames"]:
            return

        pcm = np.concatenate(capture["frames"], axis=0)
        legacy_signature = location_signature(pcm)
        need_enhanced = (
            capture["mode"] == "enhanced-calibration"
            or self.status.get("enhancedProfileReady")
        )
        enhanced_signature = (
            enhanced_location_signature(pcm) if need_enhanced else None
        )
        if legacy_signature is None:
            if capture["mode"] == "test":
                self.status["testEvent"] = {
                    "serial": getattr(self, "test_serial", 0),
                    "zone": None,
                    "confidence": 0.0,
                    "accepted": False,
                    "reason": "fingerprint-failed",
                }
                # Test mode is continuous. Keep the last result visible and
                # simply re-arm after a short quiet period.
                self.status["testActive"] = True
                self.status["testArmed"] = False
                self.audio_detector.reset(require_quiet=True)
                self.test_armed_at = time.monotonic()
            return

        mode = capture["mode"]
        source = capture["source"]
        degraded = capture["degraded"]

        if mode == "enhanced-calibration":
            calibration = self.status.get("calibration")
            if (
                not calibration
                or calibration.get("mode") != "enhanced-auto"
                or calibration.get("complete")
            ):
                return
            quality = capture_quality(pcm)
            if enhanced_signature is None or not quality.get("ok"):
                calibration["rejected"] = int(calibration.get("rejected", 0)) + 1
                reason = quality.get("reason") if quality else "fingerprint-failed"
                reasons = calibration.setdefault("rejectReasons", {})
                reasons[reason] = int(reasons.get(reason, 0)) + 1
                self.status["lastCalibrationReject"] = reason
                calibration["lastReject"] = reason
                self.audio_detector.reset(require_quiet=True)
                self.calibration_armed_at = time.monotonic() + 0.30
                return

            zone = calibration["zone"]
            self.enhanced_classifier.add(zone, enhanced_signature)
            calibration["counts"][zone] = int(calibration["counts"].get(zone, 0)) + 1
            calibration["lastQuality"] = quality
            calibration["lastReject"] = None
            self.status["lastCalibrationReject"] = None
            save_enhanced_classifier(self.enhanced_classifier)
            self._advance_enhanced_calibration()
            return

        if mode == "calibration":
            calibration = self.status.get("calibration")
            if not calibration or calibration.get("complete"):
                return
            self.classifier.add(calibration["zone"], legacy_signature)
            calibration["have"] += 1
            save_classifier(self.classifier)
            self.refresh_profile_status()
            if calibration["have"] >= calibration["need"]:
                self.status["calibration"] = {
                    **calibration,
                    "complete": True,
                    "armed": False,
                }
            return

        if mode == "test":
            self.test_serial += 1
            use_enhanced = (
                self.status.get("activeModel") == "v3-dispersion"
                and enhanced_signature is not None
            )
            classifier = self.enhanced_classifier if use_enhanced else self.classifier
            signature = enhanced_signature if use_enhanced else legacy_signature
            zone, confidence = classifier.classify(signature, 0.0)
            limit = float(cfg.get("confidence", 72)) / 100
            profile_for_test = (
                self.status.get("enhancedProfile", {})
                if use_enhanced else self.status.get("profile", {})
            )
            minimum = ENHANCED_MIN_SAMPLES if use_enhanced else CLASSIFIER_MIN_SAMPLES
            trained = [
                zone_name
                for zone_name, profile in profile_for_test.items()
                if int(profile.get("count", 0)) >= minimum
            ]
            self.status["testEvent"] = {
                "serial": self.test_serial,
                "zone": zone,
                "confidence": round(confidence, 3),
                "accepted": bool(zone and confidence >= limit),
                "threshold": round(limit, 3),
                "triggerSource": source,
                "degraded": degraded,
                "trainedZones": trained,
                "profileReady": classifier.profile_ready(),
                "reason": None if zone else "no-trained-zones",
                "rms": round(float(capture["audioRms"]), 4),
                "fingerprintVersion": 3 if use_enhanced else 2,
                "model": self.status.get("activeModel"),
            }
            # Continuous playground: keep listening after every result.
            # The result stays on screen while the detector waits for two
            # quiet frames (~40 ms) before accepting the next physical tap.
            self.status["testActive"] = True
            self.status["testArmed"] = False
            self.pending = []
            self.audio_detector.reset(require_quiet=True)
            self.test_armed_at = time.monotonic()
            return

        if mode == "normal":
            use_enhanced = (
                self.status.get("activeModel") == "v3-dispersion"
                and enhanced_signature is not None
            )
            classifier = self.enhanced_classifier if use_enhanced else self.classifier
            signature = enhanced_signature if use_enhanced else legacy_signature
            zone, confidence = classifier.classify(
                signature,
                float(cfg.get("confidence", 72)) / 100,
            )
            if zone:
                now = time.monotonic()
                self.last_accept = now
                self.pending.append((now, zone))
                self.status["lastEvent"] = {
                    "zone": zone,
                    "confidence": round(confidence, 3),
                    "impulse": round(capture["impulse"], 4),
                    "triggerSource": source,
                    "degraded": degraded,
                    "fingerprintVersion": 3 if use_enhanced else 2,
                    "model": self.status.get("activeModel"),
                }

    def tick(self):
        self.command()
        now = time.monotonic()
        if now >= self.next_config_refresh:
            self.cached_config = effective_config()
            self.next_config_refresh = now + 0.5
        cfg = self.cached_config

        accel = accel_sample(self.accel_device)
        if accel is None and now >= self.next_accel_discovery:
            self.accel_device = discover_accel()
            self.next_accel_discovery = now + 2.0
            accel = accel_sample(self.accel_device)
        self.status["accel"] = (
            str(self.accel_device) if accel is not None else "unavailable"
        )

        impulse = (
            0.0
            if accel is None or self.prev_accel is None
            else float(np.linalg.norm(accel - self.prev_accel))
        )
        self.prev_accel = accel

        try:
            frame = self.q.get_nowait()
            audio = tap_gate_features(frame)
        except queue.Empty:
            frame = None
            audio = {"audio_ok": False}

        policy = str(cfg.get("accelPolicy", "preferred"))
        accel_threshold = 0.12 + (
            100 - int(cfg.get("sensitivity", 55))
        ) * 0.004

        if policy == "off":
            source = "microphone"
            degraded = False
        elif accel is not None:
            source = "accelerometer"
            degraded = False
        elif policy == "preferred":
            source = "microphone"
            degraded = True
        else:
            source = "unavailable"
            degraded = True

        if source == "microphone":
            detector_ready_before = self.audio_detector.ready
            triggered = self.audio_detector.process(
                audio,
                int(cfg.get("sensitivity", 55)),
                now,
            )
            detector_ready_after = self.audio_detector.ready
        elif source == "accelerometer":
            detector_ready_before = (
                self.accel_quiet_frames >= 2 and not self.accel_latched
            )
            triggered = self.accel_event(impulse, accel_threshold)
            detector_ready_after = (
                self.accel_quiet_frames >= 2 and not self.accel_latched
            )
        else:
            detector_ready_before = False
            detector_ready_after = False
            triggered = False

        self.status["triggerSource"] = source
        self.status["degraded"] = degraded
        self.status["audioRms"] = round(float(audio.get("rms", 0.0)), 6)
        self.status["noiseFloor"] = round(self.audio_detector.noise_floor, 6)
        self.status["tapRmsThreshold"] = round(
            self.audio_detector.last_rms_threshold, 6
        )
        self.status["tapPeakThreshold"] = round(
            self.audio_detector.last_peak_threshold, 6
        )
        self.status["detectorReady"] = detector_ready_after

        calibration = self.status.get("calibration")
        if calibration and not calibration.get("complete"):
            calibration["armed"] = (
                now >= self.calibration_armed_at
                and detector_ready_before
                and self.fingerprint_capture is None
            )

        if self.status.get("testActive"):
            self.status["testArmed"] = (
                now >= self.test_armed_at
                and detector_ready_before
                and self.fingerprint_capture is None
            )
        else:
            self.status["testArmed"] = False

        # Finish an already-triggered acoustic fingerprint before considering
        # any new trigger. The onset detector still receives every frame above,
        # so it can unlatch normally while the location snippet is collected.
        if self.fingerprint_capture is not None:
            if frame is not None:
                self.fingerprint_capture["frames"].append(frame)
                self.fingerprint_capture["postFrames"] += 1
                if self.fingerprint_capture["postFrames"] >= 3:
                    self._finish_fingerprint_capture(cfg)
            if frame is not None:
                self.audio_history.append(frame)
            self.flush(cfg)
            return

        if triggered:
            mode = None
            if (
                calibration
                and not calibration.get("complete")
                and calibration.get("armed")
            ):
                mode = (
                    "enhanced-calibration"
                    if calibration.get("mode") == "enhanced-auto"
                    else "calibration"
                )
            elif self.status.get("testActive") and self.status.get("testArmed"):
                mode = "test"
            elif (
                cfg.get("enabled")
                and self.active_profile_ready()
                and now - self.last_accept
                >= int(cfg.get("cooldownMs", 700)) / 1000
            ):
                mode = "normal"

            if mode is not None:
                self._begin_fingerprint_capture(
                    mode,
                    frame,
                    source,
                    degraded,
                    impulse,
                    float(audio.get("rms", 0.0)),
                )

        if frame is not None:
            self.audio_history.append(frame)

        self.flush(cfg)

    def run(self):
        threading.Thread(target=self.capture, daemon=True).start()
        next_publish = 0.0
        while True:
            self.tick()
            now = time.monotonic()
            if now >= next_publish:
                self.publish()
                next_publish = now + 0.1
            time.sleep(0.02)


def run():
    Daemon().run()
