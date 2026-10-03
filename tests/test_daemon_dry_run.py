import json, os, queue, subprocess, sys, tempfile, unittest
from unittest.mock import Mock, patch
import numpy as np
ROOT=os.path.join(os.path.dirname(__file__),"..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from tapzoneslib.daemon import Daemon
class DaemonCliTests(unittest.TestCase):
 def test_action_dry_run_and_status(self):
  e=os.environ.copy();e["XDG_RUNTIME_DIR"]=tempfile.mkdtemp(); e["XDG_STATE_HOME"]=tempfile.mkdtemp(); e["XDG_CONFIG_HOME"]=tempfile.mkdtemp()
  p=subprocess.run([sys.executable,os.path.join(ROOT,"scripts/tapzones.py"),"action-test","volume_up"],env=e,text=True,capture_output=True,check=True)
  self.assertTrue(json.loads(p.stdout)["dry_run"])
  p=subprocess.run([sys.executable,os.path.join(ROOT,"scripts/tapzones.py"),"status"],env=e,text=True,capture_output=True,check=True)
  status=json.loads(p.stdout)
  self.assertFalse(status["running"])
  self.assertEqual(status["triggerSource"],"unavailable")
  self.assertTrue(status["degraded"])
  self.assertFalse(status["profileReady"])
 def test_detection_does_not_classify_before_profile_ready(self):
  classifier=Mock();classifier.profile.return_value={};classifier.profile_ready.return_value=False
  daemon=Daemon.__new__(Daemon)
  daemon.accel_device=object();daemon.classifier=classifier
  daemon.status={"calibration":None,"profile":{},"profileReady":False}
  daemon.q=queue.Queue();daemon.prev=np.zeros(3);daemon.last_accept=0.;daemon.pending=[];daemon.command=lambda:None
  cfg={"enabled":True,"sensitivity":55,"confidence":72,"accelPolicy":"required","cooldownMs":700,"multiTapWindowMs":420}
  with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=np.ones(3)):
   daemon.tick()
  classifier.classify.assert_not_called()
  self.assertEqual(daemon.pending,[])
  self.assertFalse(daemon.status["profileReady"])
 def test_off_policy_calibrates_and_detects_without_an_accel_impulse(self):
  transient={"audio_ok":True,"rms":.02,"crest":3.0}
  cfg={"enabled":True,"sensitivity":55,"confidence":72,"accelPolicy":"off","cooldownMs":700,"multiTapWindowMs":420}

  calibrating=Mock();calibrating.profile.return_value={};calibrating.profile_ready.return_value=False
  calibration_daemon=Daemon.__new__(Daemon)
  calibration_daemon.accel_device=object();calibration_daemon.classifier=calibrating
  calibration_daemon.status={"calibration":{"zone":"TL","need":3,"have":0},"profile":{},"profileReady":False}
  calibration_daemon.q=queue.Queue();calibration_daemon.q.put(object());calibration_daemon.prev=np.zeros(3);calibration_daemon.last_accept=0.;calibration_daemon.pending=[];calibration_daemon.command=lambda:None
  with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=np.zeros(3)), patch("tapzoneslib.daemon.audio_features",return_value=transient), patch("tapzoneslib.daemon.save_classifier"):
   calibration_daemon.tick()
  calibrating.add.assert_called_once()

  detecting=Mock();detecting.profile.return_value={};detecting.profile_ready.return_value=True;detecting.classify.return_value=("TL",.99)
  detection_daemon=Daemon.__new__(Daemon)
  detection_daemon.accel_device=object();detection_daemon.classifier=detecting
  detection_daemon.status={"calibration":None,"profile":{},"profileReady":True}
  detection_daemon.q=queue.Queue();detection_daemon.q.put(object());detection_daemon.prev=np.zeros(3);detection_daemon.last_accept=0.;detection_daemon.pending=[];detection_daemon.command=lambda:None
  with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=np.zeros(3)), patch("tapzoneslib.daemon.audio_features",return_value=transient):
   detection_daemon.tick()
  detecting.classify.assert_called_once()
  self.assertEqual(detection_daemon.status["lastEvent"]["triggerSource"],"microphone")
 def test_microphone_trigger_has_no_accelerometer_features(self):
  transient={"audio_ok":True,"rms":.02,"crest":3.0}
  for policy,accel in (("off",np.array([3.,4.,0.])),("preferred",None)):
   with self.subTest(policy=policy):
    classifier=Mock();classifier.profile.return_value={};classifier.profile_ready.return_value=True;classifier.classify.return_value=("TL",.99)
    daemon=Daemon.__new__(Daemon)
    daemon.accel_device=object();daemon.classifier=classifier
    daemon.status={"calibration":None,"profile":{},"profileReady":True}
    daemon.q=queue.Queue();daemon.q.put(object());daemon.prev=np.zeros(3);daemon.last_accept=0.;daemon.pending=[];daemon.command=lambda:None
    cfg={"enabled":True,"sensitivity":55,"confidence":72,"accelPolicy":policy,"cooldownMs":700,"multiTapWindowMs":420}
    with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=accel), patch("tapzoneslib.daemon.audio_features",return_value=transient), patch("tapzoneslib.daemon.feature_vector",return_value=np.zeros(13)) as feature:
     daemon.tick()
    feature.assert_called_once()
    self.assertIsNone(feature.call_args.args[1])
    self.assertEqual(feature.call_args.args[2],0.)
    self.assertEqual(daemon.status["triggerSource"],"microphone")
 def test_live_test_classifies_without_running_actions(self):
  transient={"audio_ok":True,"rms":.02,"crest":3.0}
  classifier=Mock();classifier.profile.return_value={"TL":{"count":3},"TR":{"count":3},"BL":{"count":3},"BR":{"count":3}};classifier.profile_ready.return_value=True;classifier.classify.return_value=("BR",.81)
  daemon=Daemon.__new__(Daemon)
  daemon.accel_device=object();daemon.classifier=classifier
  daemon.status={"calibration":None,"profile":classifier.profile(),"profileReady":True,"lastAction":None,"testActive":True,"testEvent":None}
  daemon.q=queue.Queue();daemon.q.put(object());daemon.prev=np.zeros(3);daemon.last_accept=0.;daemon.pending=[];daemon.command=lambda:None
  daemon.test_serial=0
  cfg={"enabled":True,"sensitivity":55,"confidence":72,"accelPolicy":"off","cooldownMs":700,"multiTapWindowMs":420}
  with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=np.zeros(3)), patch("tapzoneslib.daemon.audio_features",return_value=transient), patch("tapzoneslib.daemon.run_action") as action:
   daemon.tick()
  classifier.classify.assert_called_once()
  action.assert_not_called()
  self.assertEqual(daemon.status["testEvent"]["zone"],"BR")
  self.assertTrue(daemon.status["testEvent"]["accepted"])
  self.assertFalse(daemon.status["testActive"])
  self.assertEqual(daemon.pending,[])
 def test_live_test_with_zero_trained_zones_still_runs_safely(self):
  transient={"audio_ok":True,"rms":.02,"crest":3.0}
  classifier=Mock();classifier.profile.return_value={};classifier.profile_ready.return_value=False;classifier.classify.return_value=(None,0.0)
  daemon=Daemon.__new__(Daemon)
  daemon.accel_device=object();daemon.classifier=classifier
  daemon.status={"calibration":None,"profile":{},"profileReady":False,"lastAction":None,"testActive":True,"testEvent":None}
  daemon.q=queue.Queue();daemon.q.put(object());daemon.prev=np.zeros(3);daemon.last_accept=0.;daemon.pending=[];daemon.command=lambda:None
  daemon.test_serial=0
  cfg={"enabled":True,"sensitivity":55,"confidence":72,"accelPolicy":"off","cooldownMs":700,"multiTapWindowMs":420}
  with patch("tapzoneslib.daemon.effective_config",return_value=cfg), patch("tapzoneslib.daemon.accel_sample",return_value=np.zeros(3)), patch("tapzoneslib.daemon.audio_features",return_value=transient), patch("tapzoneslib.daemon.run_action") as action:
   daemon.tick()
  classifier.classify.assert_called_once()
  action.assert_not_called()
  self.assertFalse(daemon.status["testActive"])
  self.assertEqual(daemon.status["testEvent"]["reason"],"no-trained-zones")
  self.assertEqual(daemon.status["testEvent"]["trainedZones"],[])
  self.assertEqual(daemon.pending,[])
if __name__=="__main__":unittest.main()
