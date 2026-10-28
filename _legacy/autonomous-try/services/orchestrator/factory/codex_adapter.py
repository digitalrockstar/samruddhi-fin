from __future__ import annotations
import re, subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
def timestamp() -> str: return datetime.now(UTC).isoformat().replace("+00:00","Z")
def redact(value: str) -> str: return re.sub(r"(?i)(api[_-]?key|token|password|secret)\\s*[:=]\\s*\\S+",r"\\1=[REDACTED]",value)
@dataclass(frozen=True)
class CodexRequest: working_directory: Path; prompt: str; output_path: Path; sandbox: str="read-only"; timeout_seconds: int=180
@dataclass(frozen=True)
class CodexResult: started_at:str; ended_at:str; exit_code:int; output_path:str; summary:str; command:list[str]
class CodexCliAdapter:
 def run(self,request: CodexRequest) -> CodexResult:
  request.output_path.parent.mkdir(parents=True,exist_ok=True); command=["codex","exec","--sandbox",request.sandbox,"--color","never","--output-last-message",str(request.output_path),request.prompt]; started=timestamp()
  try:
   done=subprocess.run(command,cwd=request.working_directory,text=True,capture_output=True,timeout=request.timeout_seconds,check=False); code=done.returncode; captured=redact(done.stdout+done.stderr)
  except subprocess.TimeoutExpired as error: code=124; captured=redact((error.stdout or "")+(error.stderr or "")+"\nCodex invocation timed out.")
  if request.output_path.exists(): request.output_path.write_text(redact(request.output_path.read_text(encoding="utf-8",errors="replace")),encoding="utf-8")
  else: request.output_path.write_text(captured,encoding="utf-8")
  return CodexResult(started,timestamp(),code,str(request.output_path),captured[-1000:],command)
