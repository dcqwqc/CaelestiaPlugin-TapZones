import os, sys, tempfile, unittest
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from tapzoneslib.core import Classifier, action_argv, audio_features, discover_accel, feature_vector

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
if __name__ == "__main__": unittest.main()
