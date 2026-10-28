from __future__ import annotations
import re, subprocess
from datetime import UTC,datetime
from pathlib import Path
from .codex_adapter import CodexCliAdapter,CodexRequest
from .store import FactoryStore
def timestamp() -> str: return datetime.now(UTC).isoformat().replace("+00:00","Z")
def parse_pytest_counts(output: str) -> dict[str,int]|None:
 match=re.search(r"(?P<parts>(?:\d+ (?:passed|failed|skipped|xfailed|xpassed|error)(?:, )?)+) in [\d.]+s",output)
 return {kind:int(count) for count,kind in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|error)",match.group("parts"))} if match else None
class PhaseOneController:
 project_id="samruddhi-fin"; task_id="phase-1-real-execution-loop"
 def __init__(self,repository_root: Path)->None: self.root=repository_root.resolve();self.runtime=self.root/"services"/"orchestrator"/"runtime";self.artifacts=self.root/"artifacts"/"phase-1";self.store=FactoryStore(self.runtime/"factory.sqlite3")
 def bootstrap(self)->None: self.store.initialize();self.store.ensure_project(self.project_id,"Samruddhi Fin");self.store.ensure_task(self.project_id,self.task_id)
 def run(self)->None:
  self.bootstrap(); task=next(x for x in self.store.snapshot()["tasks"] if x["id"]==self.task_id)
  if task["status"]=="verified": return
  if task["status"]=="implemented": self._verify(); return
  if task["status"]=="not_started": self.store.transition(self.task_id,"ready")
  self.store.transition(self.task_id,"in_progress"); output=self.artifacts/"codex-run.txt"; prompt="Reply exactly: PHASE_1_CODEX_EXECUTION_OK. Do not modify files or run commands."; request=CodexRequest(self.root,prompt,output); run_id=self.store.create_agent_run(self.task_id,{"command":"codex exec","sandbox":request.sandbox,"working_directory":str(self.root),"prompt":prompt});self.store.event(self.project_id,self.task_id,"orchestrator","agent_run.started",{"run_id":run_id});result=CodexCliAdapter().run(request);status="succeeded" if result.exit_code==0 else "failed";self.store.finish_agent_run(run_id,status,result.started_at,result.ended_at,result.exit_code,result.output_path,result.summary);self.store.add_artifact(self.task_id,"codex-output",result.output_path);self.store.event(self.project_id,self.task_id,"orchestrator","agent_run.completed",{"run_id":run_id,"status":status,"exit_code":result.exit_code,"evidence_ref":result.output_path})
  if result.exit_code != 0: self.store.transition(self.task_id,"failed",blocker="Codex CLI returned a non-zero exit code");return
  self.store.transition(self.task_id,"implemented");self._verify()
 def _verify(self)->None:
  command=["python","-m","pytest","services/orchestrator/tests","-q"];started=timestamp();done=subprocess.run(command,cwd=self.root,text=True,capture_output=True,check=False);ended=timestamp();output=done.stdout+done.stderr;artifact=self.artifacts/"pytest-run.txt";artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_text(output,encoding="utf-8");counts=parse_pytest_counts(output);run_id=self.store.add_verification(self.task_id,"python -m pytest services/orchestrator/tests -q",started,ended,done.returncode,str(artifact),counts);self.store.add_artifact(self.task_id,"pytest-output",str(artifact));self.store.event(self.project_id,self.task_id,"orchestrator","verification.completed",{"run_id":run_id,"result":"passed" if done.returncode==0 else "failed","exit_code":done.returncode,"evidence_ref":str(artifact),"counts":counts});self.store.transition(self.task_id,"verified" if done.returncode==0 else "failed")
