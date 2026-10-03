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
    accel_sample,
    audio_features,
    tap_gate_features,
    atomic_json,
    discover_accel,
    effective_config,
    load_classifier,
    location_signature,
    run_action,
    save_classifier,
    xdg,
)


class Daemon:
    def __init__(self):
        self.accel_device = discover_accel()
        self.classifier = load_classifier()
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
        }

    def publish(self):
        atomic_json(xdg("runtime", "status.json"), self.status)

    def refresh_profile_status(self, recompute_quality=True):
        self.status["profile"] = self.classifier.profile()
        self.status["profileReady"] = self.classifier.profile_ready()
        if recompute_quality:
            self.status["calibrationQuality"] = self.classifier.validation_accuracy()
            self.status["axisModel"] = self.classifier.axis_model_info()

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

        if kind == "calibrate":
            zone = command["zone"]
            self.classifier.samples.pop(zone, None)
            self.classifier._axis_cache = None
            save_classifier(self.classifier)
            self.refresh_profile_status()
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
        if not self.classifier.profile_ready():
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
        signature = location_signature(pcm)
        if signature is None:
            if capture["mode"] == "test":
                self.status["testEvent"] = {
                    "zone": None,
                    "confidence": 0.0,
                    "accepted": False,
                    "reason": "fingerprint-failed",
                }
                self.status["testActive"] = False
            return

        mode = capture["mode"]
        source = capture["source"]
        degraded = capture["degraded"]

        if mode == "calibration":
            calibration = self.status.get("calibration")
            if not calibration or calibration.get("complete"):
                return
            self.classifier.add(calibration["zone"], signature)
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
            zone, confidence = self.classifier.classify(signature, 0.0)
            limit = float(cfg.get("confidence", 72)) / 100
            trained = [
                zone_name
                for zone_name, profile in self.status.get("profile", {}).items()
                if int(profile.get("count", 0)) >= CLASSIFIER_MIN_SAMPLES
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
                "profileReady": self.status["profileReady"],
                "reason": None if zone else "no-trained-zones",
                "rms": round(float(capture["audioRms"]), 4),
                "fingerprintVersion": 2,
            }
            self.status["testActive"] = False
            self.status["testArmed"] = False
            self.pending = []
            return

        if mode == "normal":
            zone, confidence = self.classifier.classify(
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
                    "fingerprintVersion": 2,
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
                mode = "calibration"
            elif self.status.get("testActive") and self.status.get("testArmed"):
                mode = "test"
            elif (
                cfg.get("enabled")
                and self.status["profileReady"]
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
