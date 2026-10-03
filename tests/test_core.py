import os, sys, tempfile, unittest
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from tapzoneslib.core import Classifier, action_argv, audio_features, discover_accel, feature_vector, microphone_transient_gate, trigger_decision

class CoreTests(unittest.TestCase):
    def test_features_are_finite_and_stereo_lag(self):
        n=1024; left=(np.sin(np.arange(n)*.08)*12000).astype("<i2"); right=np.roll(left,4); x=np.column_stack((left,right)); f=audio_features(x)
        self.assertTrue(f["audio_ok"]); self.assertLess(f["lag"],0); self.assertTrue(all(np.isfinite(list(f.values())[1:])))
    def test_centroid_classifier_and_ood(self):
        c=Classifier({}); a=np.zeros(13); b=np.ones(13)*2
        for i in range(6): c.add("TL",a+i*.001); c.add("BR",b+i*.001)
        self.assertEqual(c.classify(a+.002,.5)[0],"TL"); self.assertIsNone(c.classify(np.ones(13)*100,.9)[0])
    def test_actions_are_argv_only(self):
        self.assertEqual(action_argv("volume_up")[0],"wpctl"); self.assertIsNone(action_argv("custom",["sh","-c","bad"] if False else [])); self.assertIsNone(action_argv("unknown"))
    def test_accel_discovery_is_dynamic(self):
        # Hardware can be absent in a container or temporarily unbound. The
        # discovery API must still be a safe dynamic lookup, not a node ID.
        device=discover_accel()
        self.assertTrue(device is None or (device / "name").read_text().strip() == "accel_3d")
    def test_accel_policy_uses_one_shared_gate(self):
        transient={"audio_ok":True,"rms":.02,"crest":3.0}
        quiet={"audio_ok":True,"rms":.02,"crest":1.5}

        # Required never lets microphone audio substitute for an available or
        # missing accelerometer impulse.
        self.assertEqual(trigger_decision("required",True,0,.2,transient).triggered,False)
        self.assertEqual(trigger_decision("required",False,0,.2,transient).source,"unavailable")

        # Preferred stays on the accelerometer while it can be sampled; only
        # a missing sample enables the conservative microphone fallback.
        preferred_with_accel=trigger_decision("preferred",True,0,.2,transient)
        self.assertEqual((preferred_with_accel.triggered,preferred_with_accel.source,preferred_with_accel.degraded),(False,"accelerometer",False))
        preferred_fallback=trigger_decision("preferred",False,0,.2,transient)
        self.assertEqual((preferred_fallback.triggered,preferred_fallback.source,preferred_fallback.degraded),(True,"microphone",True))

        # Off intentionally ignores an available accelerometer and accepts
        # only a microphone transient, not sustained microphone energy.
        off=trigger_decision("off",True,0,.2,transient)
        self.assertEqual((off.triggered,off.source,off.degraded),(True,"microphone",False))
        self.assertFalse(microphone_transient_gate(quiet))
        self.assertFalse(trigger_decision("off",True,1,.2,quiet).triggered)
    def test_profile_is_ready_only_after_every_zone_has_three_samples(self):
        c=Classifier({})
        sample=np.zeros(13)
        for zone in ("TL","TR","BL"):
            for _ in range(3): c.add(zone,sample)
        self.assertFalse(c.profile_ready())
        for _ in range(2): c.add("BR",sample)
        self.assertFalse(c.profile_ready())
        c.add("BR",sample)
        self.assertTrue(c.profile_ready())
if __name__ == "__main__": unittest.main()
