import json, math, os, pathlib, subprocess
from dataclasses import dataclass
from typing import Any
import numpy as np

ZONES = ("TL", "TR", "BL", "BR")
PROFILE_VERSION = 2
CLASSIFIER_MIN_SAMPLES = 5
DEFAULT_CALIBRATION_SAMPLES = 6
LOCATION_SIGNATURE_SIZE = 30
LOCATION_BANDS = ((80,350),(350,800),(800,1600),(1600,3200),(3200,6400),(6400,12000),(12000,20000))
DEFAULT_ACTIONS = {"TL":{"1":"volume_up","2":"media_toggle","3":"next"},"TR":{"1":"volume_down","2":"previous","3":"media_toggle"},"BL":{"1":"previous","2":"volume_down","3":"none"},"BR":{"1":"next","2":"volume_up","3":"none"}}


@dataclass(frozen=True)
class TriggerDecision:
    """The single trigger decision used by both calibration and detection."""

    triggered: bool
    source: str
    degraded: bool


def microphone_transient_gate(audio):
    """Conservative, stateless microphone-only gate for a short PCM window.

    A sustained sound can have sufficient RMS but a low crest factor. Requiring
    both reduces false positives when the accelerometer cannot participate.
    """
    if not isinstance(audio, dict) or not audio.get("audio_ok"):
        return False
    try:
        return float(audio.get("rms", 0.0)) >= 0.015 and float(audio.get("crest", 0.0)) >= 2.5
    except (TypeError, ValueError):
        return False


def trigger_decision(policy, accel_available, impulse, accel_threshold, audio):
    """Select exactly one gate according to the configured accel policy.

    ``preferred`` is deliberately not a logical OR: an available accelerometer
    is authoritative. Its microphone fallback is only active while an accel
    sample cannot be read. ``off`` is intentionally microphone-only.
    """
    policy = policy if policy in ("required", "preferred", "off") else "required"
    accel_gate = accel_available and impulse >= accel_threshold
    mic_gate = microphone_transient_gate(audio)

    if policy == "off":
        return TriggerDecision(mic_gate, "microphone", False)
    if accel_available:
        return TriggerDecision(accel_gate, "accelerometer", False)
    if policy == "preferred":
        return TriggerDecision(mic_gate, "microphone", True)
    return TriggerDecision(False, "unavailable", True)


def xdg(kind, leaf):
    e={"config":"XDG_CONFIG_HOME","state":"XDG_STATE_HOME","runtime":"XDG_RUNTIME_DIR"}[kind]; base=os.environ.get(e) or {"config":os.path.expanduser("~/.config"),"state":os.path.expanduser("~/.local/state"),"runtime":"/tmp"}[kind]
    p=pathlib.Path(base)/"tapzones"/leaf; p.parent.mkdir(parents=True,exist_ok=True); return p
def read_json(path, default):
    try: return json.loads(path.read_text())
    except (OSError,json.JSONDecodeError): return default
def atomic_json(path, value):
    tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(value,sort_keys=True,indent=2)+"\n"); os.replace(tmp,path)
def plugin_settings():
    p=pathlib.Path(os.environ.get("XDG_CONFIG_HOME",os.path.expanduser("~/.config")))/"caelestia/plugins.json"; x=read_json(p,{}).get("settings",{}).get("dcqwqc/tapzones",{}); return x if isinstance(x,dict) else {}
def defaults(): return {"enabled":False,"sensitivity":55,"confidence":72,"accelPolicy":"preferred","cooldownMs":700,"multiTapWindowMs":420,"calibrationCount":DEFAULT_CALIBRATION_SAMPLES,"actionsJson":json.dumps(DEFAULT_ACTIONS)}
def effective_config():
    c=defaults(); c.update(plugin_settings()); c.update(read_json(xdg("config","config.json"),{})); return c
def discover_accel():
    for p in sorted(pathlib.Path("/sys/bus/iio/devices").glob("iio:device*")):
        try:
            if (p/"name").read_text().strip()=="accel_3d": return p
        except OSError: pass
    return None
def accel_sample(device):
    if not device: return None
    try:
        scale=float((device/"in_accel_scale").read_text().strip()); return np.array([float((device/f"in_accel_{a}_raw").read_text().strip())*scale for a in "xyz"],float)
    except (OSError,ValueError): return None
def audio_features(pcm, rate=48000):
    """Feature extraction only; PCM remains solely in the caller's RAM."""
    if pcm.ndim!=2 or pcm.shape[1]!=2 or len(pcm)<32: return {"audio_ok":False}
    x=pcm.astype(np.float64)/32768.; l,r=x[:,0],x[:,1]; m=(l+r)*.5; rl,rr=np.sqrt(np.mean(l*l)),np.sqrt(np.mean(r*r)); rms=np.sqrt(np.mean(m*m)); n=len(m); spec=np.abs(np.fft.rfft(m*np.hanning(n))); f=np.fft.rfftfreq(n,1/rate); total=max(float(spec.sum()),1e-10)
    def band(lo,hi): return float(spec[(f>=lo)&(f<hi)].sum()/total)
    corr=np.correlate(l,r,"full"); lag=int(np.clip(np.argmax(corr)-(n-1),-96,96)); early=np.sqrt(np.mean(m[:n//3]**2)); late=np.sqrt(np.mean(m[-n//3:]**2))
    peak=float(np.max(np.abs(m)))
    decay=float((early-late)/max(early+late,1e-8))
    return {"audio_ok":True,"rms":float(rms),"peak":peak,"ratio":float((rl-rr)/max(rl+rr,1e-8)),"lag":lag/96.,"crest":float(peak/max(rms,1e-8)),"decay":decay,"centroid":float((f*spec).sum()/total/(rate/2)),"low":band(40,500),"mid":band(500,2500),"high":band(2500,10000)}
class AudioTapDetector:
    """Adaptive, edge-triggered detector for percussive chassis/desk taps.

    It learns the current microphone noise floor from quiet 20 ms frames,
    requires both an RMS jump and a peak impulse, emits only on a rising edge,
    then waits for a real release before another event can fire.
    """

    def __init__(self):
        self.noise_floor = 0.003
        self.prev_rms = 0.0
        self.latched = False
        self.quiet_frames = 3
        self.refractory_until = 0.0
        self.last_rms_threshold = 0.012
        self.last_peak_threshold = 0.035

    def reset(self, require_quiet=True):
        self.prev_rms = 0.0
        self.latched = False
        self.quiet_frames = 0 if require_quiet else 3
        self.refractory_until = 0.0

    @property
    def ready(self):
        return self.quiet_frames >= 2 and not self.latched

    def process(self, audio, sensitivity=55, now=None):
        now = now if now is not None else 0.0
        if not isinstance(audio, dict) or not audio.get("audio_ok"):
            return False

        rms = max(0.0, float(audio.get("rms", 0.0)))
        crest = max(0.0, float(audio.get("crest", 0.0)))
        peak = max(0.0, float(audio.get("peak", rms * crest)))

        sens = max(1.0, min(100.0, float(sensitivity)))
        # At the default 55 sensitivity and Mirai's ~0.002–0.006 idle RMS,
        # this lands around 0.010–0.015 RMS and ~0.035 peak.
        rms_ratio = 4.4 - 2.2 * (sens / 100.0)
        peak_ratio = 10.0 - 4.0 * (sens / 100.0)
        gate_floor = min(self.noise_floor, 0.0055)
        rms_threshold = max(0.009, gate_floor * rms_ratio)
        peak_threshold = max(0.032, gate_floor * peak_ratio)
        self.last_rms_threshold = rms_threshold
        self.last_peak_threshold = peak_threshold

        release = max(0.0075, self.noise_floor * 1.9)
        candidate = (
            rms >= rms_threshold
            and peak >= peak_threshold
            and crest >= 1.8
            and rms >= max(self.prev_rms * 1.35, self.noise_floor * 2.2)
        )

        triggered = False
        if candidate:
            if (
                not self.latched
                and self.quiet_frames >= 2
                and now >= self.refractory_until
            ):
                triggered = True
                self.refractory_until = now + 0.16
            self.latched = True
            self.quiet_frames = 0
        else:
            if rms <= release:
                self.quiet_frames = min(20, self.quiet_frames + 1)
                if self.quiet_frames >= 2:
                    self.latched = False
            else:
                self.quiet_frames = 0

            # Learn only from non-impulsive frames so taps do not raise their
            # own future threshold. Faster downward than upward adaptation.
            if rms < max(0.012, self.noise_floor * 2.2):
                alpha = 0.06 if rms < self.noise_floor else 0.015
                self.noise_floor = max(
                    0.0007,
                    min(0.02, (1.0 - alpha) * self.noise_floor + alpha * rms),
                )

        self.prev_rms = rms
        return triggered


def _next_pow2(value):
    return 1 << max(1, int(value - 1).bit_length())


def _gcc_phat_lag(left, right, max_shift=40):
    nfft = _next_pow2(len(left) * 2)
    left_fft = np.fft.rfft(left, n=nfft)
    right_fft = np.fft.rfft(right, n=nfft)
    cross = left_fft * np.conj(right_fft)
    cross /= np.maximum(np.abs(cross), 1e-12)
    corr = np.fft.irfft(cross, n=nfft)
    window = np.concatenate((corr[-max_shift:], corr[:max_shift + 1]))
    return float(np.argmax(window) - max_shift) / max_shift


def location_signature(pcm, rate=48000):
    """Amplitude-robust stereo fingerprint for one detected tap.

    PCM exists only in RAM. Only this normalized numeric signature is saved.
    """
    if pcm is None or pcm.ndim != 2 or pcm.shape[1] != 2 or len(pcm) < 256:
        return None

    x = pcm.astype(np.float64) / 32768.0
    mono = x.mean(axis=1)
    onset = int(np.argmax(np.abs(mono)))
    start = max(0, onset - int(rate * 0.002))
    end = min(len(x), onset + int(rate * 0.042))
    segment = x[start:end]
    if len(segment) < 256:
        return None

    segment = segment - segment.mean(axis=0, keepdims=True)
    left, right = segment[:, 0], segment[:, 1]
    eps = 1e-10

    rms_l = float(np.sqrt(np.mean(left * left)))
    rms_r = float(np.sqrt(np.mean(right * right)))
    peak_l = float(np.max(np.abs(left)))
    peak_r = float(np.max(np.abs(right)))
    log_rms_lr = float(np.clip(np.log((rms_l + eps) / (rms_r + eps)), -4, 4))
    log_peak_lr = float(np.clip(np.log((peak_l + eps) / (peak_r + eps)), -4, 4))
    lag = _gcc_phat_lag(left, right)

    nfft = _next_pow2(len(segment))
    window = np.hanning(len(segment))
    left_power = np.abs(np.fft.rfft(left * window, n=nfft)) ** 2
    right_power = np.abs(np.fft.rfft(right * window, n=nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    total_l = max(float(left_power.sum()), eps)
    total_r = max(float(right_power.sum()), eps)

    band_avg = []
    band_lr = []
    for lo, hi in LOCATION_BANDS:
        mask = (freqs >= lo) & (freqs < hi)
        energy_l = float(left_power[mask].sum() / total_l)
        energy_r = float(right_power[mask].sum() / total_r)
        band_avg.append((energy_l + energy_r) * 0.5)
        band_lr.append(float(np.clip(
            (energy_l - energy_r) / max(energy_l + energy_r, eps), -1, 1
        )))

    centroid_l = float((freqs * left_power).sum() / total_l / (rate / 2))
    centroid_r = float((freqs * right_power).sum() / total_r / (rate / 2))

    post = x[onset:min(len(x), onset + int(rate * 0.030))]
    mono_post = post.mean(axis=1)
    bins = np.array_split(post, 6)
    mono_env = []
    lr_env = []
    for index, block in enumerate(bins):
        if len(block) == 0:
            mono_env.append(0.0)
            if index < 3:
                lr_env.append(0.0)
            continue
        l_rms = float(np.sqrt(np.mean(block[:, 0] ** 2)))
        r_rms = float(np.sqrt(np.mean(block[:, 1] ** 2)))
        m_rms = float(np.sqrt(np.mean(block.mean(axis=1) ** 2)))
        mono_env.append(m_rms)
        if index < 3:
            lr_env.append(float(np.clip(
                (l_rms - r_rms) / max(l_rms + r_rms, eps), -1, 1
            )))

    env_total = max(sum(mono_env), eps)
    mono_env = [value / env_total for value in mono_env]

    split = max(1, len(mono_post) // 3)
    early = float(np.sqrt(np.mean(mono_post[:split] ** 2)))
    late = float(np.sqrt(np.mean(mono_post[-split:] ** 2)))
    decay = float((early - late) / max(early + late, eps))
    mono_rms = float(np.sqrt(np.mean(mono_post * mono_post)))
    crest = float(np.clip(
        np.max(np.abs(mono_post)) / max(mono_rms, eps) / 8.0, 0.0, 1.5
    ))

    signature = np.array([
        log_rms_lr, log_peak_lr, lag,
        *band_avg, *band_lr,
        centroid_l, centroid_r,
        *mono_env, *lr_env,
        decay, crest,
    ], dtype=float)
    if len(signature) != LOCATION_SIGNATURE_SIZE or not np.all(np.isfinite(signature)):
        return None
    return signature


def feature_vector(audio, accel, impulse):
    """Legacy compact vector retained for compatibility/tests."""
    a = accel if accel is not None else np.zeros(3)
    return np.array([
        *a.tolist(), impulse,
        audio.get("rms",0), audio.get("ratio",0), audio.get("lag",0),
        audio.get("crest",0), audio.get("decay",0), audio.get("centroid",0),
        audio.get("low",0), audio.get("mid",0), audio.get("high",0)
    ], float)


@dataclass
class Classifier:
    samples: dict

    def add(self, zone, vector):
        vector = np.asarray(vector, dtype=float)
        if vector.ndim != 1:
            raise ValueError("location signature must be one-dimensional")
        existing = self.samples.get(zone, [])
        if existing and len(existing[0]) != len(vector):
            existing = []
        self.samples[zone] = existing + [vector.tolist()]

    def profile_ready(self):
        return all(
            len(self.samples.get(zone, [])) >= CLASSIFIER_MIN_SAMPLES
            for zone in ZONES
        )

    def profile(self):
        out = {}
        for zone, raw in self.samples.items():
            x = np.asarray(raw, float)
            if len(x):
                out[zone] = {
                    "count": len(x),
                    "mean": x.mean(0).tolist(),
                    "std": np.maximum(x.std(0), 1e-4).tolist(),
                }
        return out

    def _model(self):
        trained = {
            zone: np.asarray(raw, float)
            for zone, raw in self.samples.items()
            if len(raw) >= CLASSIFIER_MIN_SAMPLES
        }
        if not trained:
            return None
        dims = {matrix.shape[1] for matrix in trained.values()}
        if len(dims) != 1:
            return None

        all_samples = np.vstack(list(trained.values()))
        # Median/MAD makes one accidental non-tap calibration sample far less
        # able to drag an entire corner model away from its real cluster.
        means = {zone: np.median(matrix, axis=0) for zone, matrix in trained.items()}
        mean_matrix = np.vstack(list(means.values()))

        within_parts = [
            np.abs(matrix - means[zone])
            for zone, matrix in trained.items()
        ]
        within_mad = np.vstack(within_parts)
        within_scale = np.median(within_mad, axis=0) * 1.4826
        global_var = all_samples.var(0)
        between_var = mean_matrix.var(0)

        scale = np.sqrt(within_scale ** 2 + 0.18 * global_var + 1e-5)
        fisher = between_var / (within_scale ** 2 + 0.12 * global_var + 1e-5)
        weights = np.clip(fisher, 0.08, 8.0)
        weights /= max(float(weights.mean()), 1e-8)
        return trained, means, scale, weights

    def _distances(self, vector):
        model = self._model()
        if model is None:
            return []
        trained, means, scale, weights = model
        vector = np.asarray(vector, float)
        if vector.shape != scale.shape:
            return []

        distances = []
        for zone, matrix in trained.items():
            centroid = float(np.sqrt(np.average(
                ((vector - means[zone]) / scale) ** 2, weights=weights
            )))
            sample_distances = np.sqrt(np.average(
                ((matrix - vector) / scale) ** 2, axis=1, weights=weights
            ))
            local = float(np.mean(np.sort(sample_distances)[:2]))
            distances.append((0.65 * centroid + 0.35 * local, zone))
        return sorted(distances)

    def classify(self, vector, threshold=.72):
        distances = self._distances(vector)
        if not distances:
            return None, 0.0

        best_distance, best_zone = distances[0]
        if len(distances) == 1:
            margin = 0.0
        else:
            second = distances[1][0]
            margin = max(0.0, (second - best_distance) / max(second, 1e-8))

        confidence = 0.5 + 0.5 * math.tanh(3.2 * margin)
        if best_distance > 3.2:
            confidence *= math.exp(-(best_distance - 3.2) / 2.5)
        confidence = float(np.clip(confidence, 0.0, 1.0))
        if threshold > 0 and confidence < threshold:
            return None, confidence
        return best_zone, confidence

    def validation_accuracy(self):
        if not all(
            len(self.samples.get(zone, [])) >= CLASSIFIER_MIN_SAMPLES + 1
            for zone in ZONES
        ):
            return None

        correct = 0
        total = 0
        confusion = {z:{other:0 for other in ZONES} for z in ZONES}
        for zone in ZONES:
            for index, vector in enumerate(self.samples[zone]):
                reduced = {
                    z:[
                        sample for sample_index, sample in enumerate(self.samples[z])
                        if z != zone or sample_index != index
                    ]
                    for z in ZONES
                }
                predicted, _ = Classifier(reduced).classify(vector, 0.0)
                if predicted in ZONES:
                    confusion[zone][predicted] += 1
                correct += predicted == zone
                total += 1
        return {
            "accuracy": correct / max(total, 1),
            "correct": correct,
            "total": total,
            "confusion": confusion,
        }


def load_classifier():
    data = read_json(xdg("state","profile.json"), {})
    if data.get("version") != PROFILE_VERSION:
        return Classifier({})
    samples = data.get("samples", {})
    return Classifier(samples if isinstance(samples, dict) else {})


def save_classifier(c):
    atomic_json(xdg("state","profile.json"), {
        "version": PROFILE_VERSION,
        "samples": c.samples,
        "profile": c.profile(),
    })


def action_argv(action,custom=None):
    fixed={"volume_up":["wpctl","set-volume","@DEFAULT_AUDIO_SINK@","5%+"],"volume_down":["wpctl","set-volume","@DEFAULT_AUDIO_SINK@","5%-"]}
    if action=="custom": return custom if custom and all(isinstance(x,str) and x for x in custom) else None
    return fixed.get(action)
def mpris_argv(action):
    methods={"media_toggle":"PlayPause","next":"Next","previous":"Previous"}
    if action not in methods:return None
    try:
        names=[line.split()[0] for line in subprocess.run(["busctl","--user","list","--no-legend"],capture_output=True,text=True,timeout=1).stdout.splitlines() if line.startswith("org.mpris.MediaPlayer2.")]
    except (OSError,subprocess.SubprocessError): names=[]
    return ["busctl","--user","call",names[0],"/org/mpris/MediaPlayer2","org.mpris.MediaPlayer2.Player",methods[action]] if names else None
def run_action(action,dry_run=False,custom=None):
    argv=mpris_argv(action) if action in ("media_toggle","next","previous") else action_argv(action,custom)
    if action=="quick_settings": return {"ok":False,"reason":"No stable Caelestia IPC action was locally discoverable; use explicit custom argv."}
    if action in ("none",""): return {"ok":True,"skipped":True}
    if not argv: return {"ok":False,"reason":"no running MPRIS player" if action in ("media_toggle","next","previous") else "invalid action"}
    if dry_run: return {"ok":True,"dry_run":True,"argv":argv}
    try: subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); return {"ok":True,"argv":argv}
    except OSError as e: return {"ok":False,"reason":str(e),"argv":argv}
