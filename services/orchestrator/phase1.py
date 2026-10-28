from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from factory.controller import PhaseOneController  # noqa: E402
def main() -> int:
 parser=argparse.ArgumentParser(description="Run or inspect the Phase 1 factory proof.");parser.add_argument("command",choices=("run","status"));args=parser.parse_args();controller=PhaseOneController(ROOT);controller.bootstrap()
 if args.command=="run": controller.run()
 print(json.dumps(controller.store.snapshot(),indent=2));return 0
if __name__=="__main__": raise SystemExit(main())
