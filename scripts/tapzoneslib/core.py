import json, math, os, pathlib, subprocess
from dataclasses import dataclass
from typing import Any
import numpy as np

ZONES = ("TL", "TR", "BL", "BR")
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
def defaults(): return {"enabled":False,"sensitivity":55,"confidence":72,"accelPolicy":"required","cooldownMs":700,"multiTapWindowMs":420,"calibrationCount":12,"actionsJson":json.dumps(DEFAULT_ACTIONS)}
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
    return {"audio_ok":True,"rms":float(rms),"ratio":float((rl-rr)/max(rl+rr,1e-8)),"lag":lag/96.,"crest":float(np.max(np.abs(m))/max(rms,1e-8)),"decay":float((early-late)/max(early,1e-8)),"centroid":float((f*spec).sum()/total/(rate/2)),"low":band(40,500),"mid":band(500,2500),"high":band(2500,10000)}
def feature_vector(audio, accel, impulse):
    a=accel if accel is not None else np.zeros(3); return np.array([*a.tolist(),impulse,audio.get("rms",0),audio.get("ratio",0),audio.get("lag",0),audio.get("crest",0),audio.get("decay",0),audio.get("centroid",0),audio.get("low",0),audio.get("mid",0),audio.get("high",0)],float)
@dataclass
class Classifier:
    samples: dict
    def add(self,z,v): self.samples.setdefault(z,[]).append(v.tolist())
    def profile_ready(self): return all(len(self.samples.get(zone,[])) >= 3 for zone in ZONES)
    def profile(self):
        out={}
        for z,raw in self.samples.items():
            x=np.asarray(raw,float)
            if len(x): out[z]={"count":len(x),"mean":x.mean(0).tolist(),"std":np.maximum(x.std(0),.02).tolist()}
        return out
    def classify(self,v,threshold=.72):
        choices=[]
        for z,p in self.profile().items():
            if p["count"]>=3: choices.append((float(np.sqrt(np.mean(((v-np.array(p["mean"]))/np.array(p["std"]))**2))),z))
        if not choices: return None,0.
        d,z=min(choices); c=float(math.exp(-d/2)); return (z,c) if c>=threshold else (None,c)
def load_classifier(): return Classifier(read_json(xdg("state","profile.json"),{"samples":{}}).get("samples",{}))
def save_classifier(c): atomic_json(xdg("state","profile.json"),{"version":1,"samples":c.samples,"profile":c.profile()})
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
