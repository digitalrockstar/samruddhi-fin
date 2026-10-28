from __future__ import annotations
import json, sqlite3, uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

TASK_TRANSITIONS = {"not_started":{"ready","cancelled","deferred"},"ready":{"in_progress","blocked","cancelled","deferred"},"in_progress":{"implemented","failed","blocked","cancelled"},"blocked":{"ready","cancelled","deferred"},"implemented":{"verified","failed","blocked"},"failed":{"ready","cancelled","deferred"},"verified":set(),"deferred":{"ready","cancelled"},"cancelled":set()}
def now() -> str: return datetime.now(UTC).isoformat().replace("+00:00", "Z")

class FactoryStore:
 def __init__(self, database_path: Path) -> None: self.database_path = database_path
 @contextmanager
 def connection(self) -> Iterator[sqlite3.Connection]:
  self.database_path.parent.mkdir(parents=True, exist_ok=True); db=sqlite3.connect(self.database_path); db.row_factory=sqlite3.Row
  try: yield db; db.commit()
  finally: db.close()
 def initialize(self) -> None:
  with self.connection() as db: db.executescript("""PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,module_id TEXT,title TEXT NOT NULL,description TEXT NOT NULL,status TEXT NOT NULL,priority TEXT NOT NULL,risk TEXT NOT NULL,acceptance_criteria TEXT NOT NULL,dependencies TEXT NOT NULL,retry_budget INTEGER NOT NULL,retry_attempts INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,blocker TEXT,specification_version TEXT NOT NULL,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,adapter TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT,ended_at TEXT,exit_code INTEGER,invocation_json TEXT NOT NULL,output_artifact TEXT,summary TEXT,FOREIGN KEY(task_id) REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS verification_runs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,command TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT NOT NULL,exit_code INTEGER NOT NULL,output_artifact TEXT NOT NULL,counts_json TEXT,FOREIGN KEY(task_id) REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS task_events(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT,actor TEXT NOT NULL,type TEXT NOT NULL,timestamp TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,created_at TEXT NOT NULL);""")
 def event(self, project_id: str, task_id: str|None, actor: str, event_type: str, payload: dict[str,Any]) -> None:
  with self.connection() as db: db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),project_id,task_id,actor,event_type,now(),json.dumps(payload,sort_keys=True)))
 def ensure_project(self, project_id: str, name: str) -> None:
  with self.connection() as db: db.execute("INSERT OR IGNORE INTO projects VALUES(?,?,?)",(project_id,name,now()))
 def ensure_task(self, project_id: str, task_id: str) -> None:
  with self.connection() as db:
   if db.execute("SELECT id FROM tasks WHERE id=?",(task_id,)).fetchone(): return
   stamp=now(); db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(task_id,project_id,"phase-1","Prove one real Codex execution loop","Persist a task, invoke local Codex, run real tests, and retain evidence across restart.","not_started","high","medium",json.dumps(["A supported Codex CLI run is persisted","A real pytest run is persisted","The console reads persisted state"]),"[]",0,0,stamp,stamp,None,"phase-1")); db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),project_id,task_id,"orchestrator","task.created",stamp,json.dumps({"status":"not_started"},sort_keys=True)))
 def transition(self, task_id: str, new_status: str, *, blocker: str|None=None) -> None:
  with self.connection() as db:
   row=db.execute("SELECT project_id,status FROM tasks WHERE id=?",(task_id,)).fetchone()
   if row is None: raise KeyError(task_id)
   if new_status not in TASK_TRANSITIONS[row["status"]]: raise ValueError(f"invalid task transition {row['status']} -> {new_status}")
   stamp=now(); db.execute("UPDATE tasks SET status=?,blocker=?,updated_at=? WHERE id=?",(new_status,blocker,stamp,task_id)); db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),row["project_id"],task_id,"orchestrator","task.transitioned",stamp,json.dumps({"from":row["status"],"to":new_status,"blocker":blocker},sort_keys=True)))
 def create_agent_run(self, task_id: str, invocation: dict[str,Any]) -> str:
  run_id=str(uuid.uuid4())
  with self.connection() as db: db.execute("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?)",(run_id,task_id,"codex-cli","starting",None,None,None,json.dumps(invocation),None,None))
  return run_id
 def finish_agent_run(self, run_id: str,status: str,started_at: str,ended_at: str,exit_code: int,artifact: str,summary: str) -> None:
  with self.connection() as db: db.execute("UPDATE agent_runs SET status=?,started_at=?,ended_at=?,exit_code=?,output_artifact=?,summary=? WHERE id=?",(status,started_at,ended_at,exit_code,artifact,summary,run_id))
 def add_verification(self,task_id: str,command: str,started_at: str,ended_at: str,exit_code: int,artifact: str,counts: dict[str,int]|None) -> str:
  run_id=str(uuid.uuid4()); result="passed" if exit_code==0 else "failed"
  with self.connection() as db: db.execute("INSERT INTO verification_runs VALUES(?,?,?,?,?,?,?,?,?,?)",(run_id,task_id,command,"completed",result,started_at,ended_at,exit_code,artifact,json.dumps(counts) if counts is not None else None))
  return run_id
 def add_artifact(self,task_id: str,kind: str,path: str) -> None:
  with self.connection() as db: db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",(str(uuid.uuid4()),task_id,kind,path,now()))
 def snapshot(self) -> dict[str,Any]:
  with self.connection() as db:
   def rows(q: str) -> list[dict[str,Any]]: return [dict(r) for r in db.execute(q).fetchall()]
   return {"projects":rows("SELECT * FROM projects"),"tasks":rows("SELECT * FROM tasks"),"agent_runs":rows("SELECT * FROM agent_runs ORDER BY started_at"),"verification_runs":rows("SELECT * FROM verification_runs ORDER BY started_at"),"events":rows("SELECT * FROM task_events ORDER BY timestamp"),"artifacts":rows("SELECT * FROM artifacts")}
