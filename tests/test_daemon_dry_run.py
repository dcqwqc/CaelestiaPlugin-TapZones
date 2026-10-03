import json, os, subprocess, sys, tempfile, unittest
ROOT=os.path.join(os.path.dirname(__file__),"..")
class DaemonCliTests(unittest.TestCase):
 def test_action_dry_run_and_status(self):
  e=os.environ.copy();e["XDG_RUNTIME_DIR"]=tempfile.mkdtemp(); e["XDG_STATE_HOME"]=tempfile.mkdtemp(); e["XDG_CONFIG_HOME"]=tempfile.mkdtemp()
  p=subprocess.run([sys.executable,os.path.join(ROOT,"scripts/tapzones.py"),"action-test","volume_up"],env=e,text=True,capture_output=True,check=True)
  self.assertTrue(json.loads(p.stdout)["dry_run"])
  p=subprocess.run([sys.executable,os.path.join(ROOT,"scripts/tapzones.py"),"status"],env=e,text=True,capture_output=True,check=True)
  self.assertFalse(json.loads(p.stdout)["running"])
if __name__=="__main__":unittest.main()
