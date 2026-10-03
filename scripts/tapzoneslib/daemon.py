import json, queue, subprocess, threading, time
import numpy as np
from .core import accel_sample,audio_features,discover_accel,effective_config,feature_vector,load_classifier,run_action,save_classifier,xdg,atomic_json
class Daemon:
 def __init__(self):
  self.accel_device=discover_accel(); self.classifier=load_classifier(); self.status={"running":True,"audio":"starting","accel":str(self.accel_device or "unavailable"),"calibration":None,"lastEvent":None,"lastAction":None,"profile":self.classifier.profile()}; self.q=queue.Queue(maxsize=20); self.prev=None; self.last_accept=0.; self.pending=[]
 def publish(self): atomic_json(xdg("runtime","status.json"),self.status)
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
   self.classifier.samples.pop(cmd["zone"],None);save_classifier(self.classifier);self.status["calibration"]={"zone":cmd["zone"],"need":max(3,min(30,int(cmd.get("count",12)))),"have":0}
  elif cmd.get("type")=="reset":self.classifier.samples={};save_classifier(self.classifier);self.status["profile"]={};self.status["calibration"]=None
  elif cmd.get("type")=="action-test":self.status["lastAction"]=run_action(cmd.get("action","none"),True,cmd.get("custom"))
 def flush(self,cfg):
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
   self.accel_device=discover_accel();self.status["accel"]=str(self.accel_device or "unavailable");a=accel_sample(self.accel_device)
  impulse=0. if a is None or self.prev is None else float(np.linalg.norm(a-self.prev));self.prev=a
  try:af=audio_features(self.q.get_nowait())
  except queue.Empty:af={"audio_ok":False}
  threshold=.12+(100-int(cfg.get("sensitivity",55)))*.004;gate=impulse>=threshold
  if cfg.get("accelPolicy")=="required" and not gate:self.flush(cfg);return
  if cfg.get("accelPolicy")=="preferred" and not gate and not af.get("rms",0)>.015:self.flush(cfg);return
  if not gate and not af.get("audio_ok"):self.flush(cfg);return
  v=feature_vector(af,a,impulse);cal=self.status.get("calibration")
  if cal and not cal.get("complete") and gate:
   self.classifier.add(cal["zone"],v);cal["have"]+=1;save_classifier(self.classifier);self.status["profile"]=self.classifier.profile()
   if cal["have"]>=cal["need"]:self.status["calibration"]={**cal,"complete":True}
   return
  if not cfg.get("enabled") or not gate or time.monotonic()-self.last_accept<int(cfg.get("cooldownMs",700))/1000:self.flush(cfg);return
  zone,confidence=self.classifier.classify(v,float(cfg.get("confidence",72))/100)
  if zone:self.last_accept=time.monotonic();self.pending.append((self.last_accept,zone));self.status["lastEvent"]={"zone":zone,"confidence":round(confidence,3),"impulse":round(impulse,4)}
  self.flush(cfg)
 def run(self):
  threading.Thread(target=self.capture,daemon=True).start()
  while True:self.tick();self.publish();time.sleep(.1)
def run():Daemon().run()
