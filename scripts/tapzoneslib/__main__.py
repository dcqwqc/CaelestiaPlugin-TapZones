import argparse,json
from .core import ZONES,discover_accel,load_classifier,read_json,run_action,xdg,atomic_json
from .daemon import run


def inactive_status():
 c=load_classifier()
 return {"running":False,"audio":"unavailable","accel":str(discover_accel() or "unavailable"),"calibration":None,"lastEvent":None,"lastAction":None,"profile":c.profile(),"profileReady":c.profile_ready(),"triggerSource":"unavailable","degraded":True,"testActive":False,"testRemainingMs":0,"testEvent":None}


def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="cmd",required=True);s.add_parser("daemon");s.add_parser("status");c=s.add_parser("calibrate");c.add_argument("zone",choices=ZONES);c.add_argument("--count",type=int,default=12);s.add_parser("reset");a=s.add_parser("action-test");a.add_argument("action");a.add_argument("--custom-json",default="[]");t=s.add_parser("test-start");t.add_argument("--seconds",type=int,default=30);s.add_parser("test-stop");s.add_parser("accel-discover");args=p.parse_args()
 if args.cmd=="daemon":run();return 0
 if args.cmd=="status":
  status=inactive_status();status.update(read_json(xdg("runtime","status.json"),{}));print(json.dumps(status));return 0
 if args.cmd=="accel-discover":print(str(discover_accel() or ""));return 0 if discover_accel() else 1
 if args.cmd=="action-test":
  try:custom=json.loads(args.custom_json)
  except json.JSONDecodeError:custom=[]
  print(json.dumps(run_action(args.action,True,custom)));return 0
 cmd={"type":args.cmd,"zone":getattr(args,"zone",None),"count":getattr(args,"count",None),"seconds":getattr(args,"seconds",None)};atomic_json(xdg("runtime","command.json"),cmd);return 0
