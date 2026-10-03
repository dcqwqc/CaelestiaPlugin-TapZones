import json, queue, subprocess, threading, time
import numpy as np
from .core import accel_sample,audio_features,discover_accel,effective_config,feature_vector,load_classifier,run_action,save_classifier,trigger_decision,xdg,atomic_json
class Daemon:
 def __init__(self):
  self.accel_device=discover_accel(); self.classifier=load_classifier(); self.status={"running":True,"audio":"starting","accel":str(self.accel_device or "unavailable"),"calibration":None,"lastEvent":None,"lastAction":None,"profile":self.classifier.profile(),"profileReady":self.classifier.profile_ready(),"triggerSource":"unknown","degraded":False}; self.q=queue.Queue(maxsize=20); self.prev=None; self.last_accept=0.; self.pending=[]
 def publish(self): atomic_json(xdg("runtime","status.json"),self.status)
 def refresh_profile_status(self):
  self.status["profile"]=self.classifier.profile();self.status["profileReady"]=self.classifier.profile_ready()
 def capture(self):
  try:
   # PipeWire dynamically resolves the default source; numeric node IDs are never used.
   p=subprocess.Popen(["pw-record","--raw","--format","s16","--rate","48000","--channels","2","-"],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL); self.status["audio"]="capturing-default-source"
   while True:
    b=p.stdout.read(9600)
    if not b: raise RuntimeError("pw-record ended")
    try:self.q.put_nowait(np.frombuffer(b,dtype="<i2").reshape(-1,2).copy())
    except queue.Full:pass
  except Exception:self.status["audio"]="unavailable"
 def command(self):
  p=xdg("runtime","command.json")
  if not p.exists():return
  cmd=json.loads(p.read_text()); p.unlink(missing_ok=True)
  if cmd.get("type")=="calibrate":
   # A new guided run replaces this corner's old feature vectors; it never
   # mixes a changed laptop position with a previous calibration.
   self.classifier.samples.pop(cmd["zone"],None);save_classifier(self.classifier);self.status["calibration"]={"zone":cmd["zone"],"need":max(3,min(30,int(cmd.get("count",12)))),"have":0};self.pending=[];self.refresh_profile_status()
  elif cmd.get("type")=="reset":self.classifier.samples={};save_classifier(self.classifier);self.status["calibration"]=None;self.pending=[];self.refresh_profile_status()
  elif cmd.get("type")=="action-test":self.status["lastAction"]=run_action(cmd.get("action","none"),True,cmd.get("custom"))
 def flush(self,cfg):
  # A partial profile must not flush a gesture accepted before recalibration.
  if not self.classifier.profile_ready():self.pending=[];return
  if not self.pending or time.monotonic()-self.pending[-1][0]<int(cfg.get("multiTapWindowMs",420))/1000:return
  taps=min(3,len(self.pending));zone=self.pending[-1][1];self.pending=[]
  try:action=json.loads(cfg.get("actionsJson","{}")).get(zone,{}).get(str(taps),"none")
  except json.JSONDecodeError:action="none"
  custom=None
  if isinstance(action,dict): custom=action.get("argv");action=action.get("type","custom")
  self.status["lastAction"]={"zone":zone,"taps":taps,"action":action,**run_action(action,custom=custom)}
 def tick(self):
  cfg=effective_config();self.command();a=accel_sample(self.accel_device)
  if self.accel_device is None or a is None:
   self.accel_device=discover_accel();a=accel_sample(self.accel_device)
  self.status["accel"]=str(self.accel_device) if a is not None else "unavailable"
  impulse=0. if a is None or self.prev is None else float(np.linalg.norm(a-self.prev));self.prev=a
  try:af=audio_features(self.q.get_nowait())
  except queue.Empty:af={"audio_ok":False}
  threshold=.12+(100-int(cfg.get("sensitivity",55)))*.004;trigger=trigger_decision(cfg.get("accelPolicy"),a is not None,impulse,threshold,af)
  self.status["triggerSource"]=trigger.source;self.status["degraded"]=trigger.degraded;self.refresh_profile_status()
  if not trigger.triggered:self.flush(cfg);return
  # Keep microphone-only calibration and detection internally consistent with
  # their gate: live accelerometer data must not influence their vectors.
  feature_accel,feature_impulse=(None,0.) if trigger.source=="microphone" else (a,impulse)
  v=feature_vector(af,feature_accel,feature_impulse);cal=self.status.get("calibration")
  if cal and not cal.get("complete"):
   self.classifier.add(cal["zone"],v);cal["have"]+=1;save_classifier(self.classifier);self.refresh_profile_status()
   if cal["have"]>=cal["need"]:self.status["calibration"]={**cal,"complete":True}
   return
  if not cfg.get("enabled") or not self.status["profileReady"] or time.monotonic()-self.last_accept<int(cfg.get("cooldownMs",700))/1000:self.flush(cfg);return
  zone,confidence=self.classifier.classify(v,float(cfg.get("confidence",72))/100)
  if zone:self.last_accept=time.monotonic();self.pending.append((self.last_accept,zone));self.status["lastEvent"]={"zone":zone,"confidence":round(confidence,3),"impulse":round(impulse,4),"triggerSource":trigger.source,"degraded":trigger.degraded}
  self.flush(cfg)
 def run(self):
  threading.Thread(target=self.capture,daemon=True).start()
  while True:self.tick();self.publish();time.sleep(.1)
def run():Daemon().run()
