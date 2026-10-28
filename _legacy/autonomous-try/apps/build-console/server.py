from __future__ import annotations
import json,sys
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"services"/"orchestrator"))
from factory.store import FactoryStore  # noqa: E402
class ConsoleHandler(SimpleHTTPRequestHandler):
 def do_GET(self) -> None:  # noqa: N802
  if self.path=="/api/state":
   payload=json.dumps(FactoryStore(ROOT/"services"/"orchestrator"/"runtime"/"factory.sqlite3").snapshot()).encode();self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload);return
  if self.path in ("/","/index.html"): self.path="/index.html"
  return super().do_GET()
if __name__=="__main__": ThreadingHTTPServer(("127.0.0.1",8765),ConsoleHandler).serve_forever()
