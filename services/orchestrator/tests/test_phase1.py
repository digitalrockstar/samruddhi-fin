from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from factory.controller import parse_pytest_counts
from factory.store import FactoryStore
def test_valid_task_transitions_are_persisted(tmp_path: Path) -> None:
 store=FactoryStore(tmp_path/"factory.sqlite3");store.initialize();store.ensure_project("project","Project");store.ensure_task("project","task");store.transition("task","ready");store.transition("task","in_progress")
 assert FactoryStore(tmp_path/"factory.sqlite3").snapshot()["tasks"][0]["status"]=="in_progress"
 assert len(store.snapshot()["events"])==3
def test_invalid_transition_is_rejected(tmp_path: Path) -> None:
 store=FactoryStore(tmp_path/"factory.sqlite3");store.initialize();store.ensure_project("project","Project");store.ensure_task("project","task")
 with pytest.raises(ValueError): store.transition("task","verified")
def test_pytest_count_parser_only_returns_supported_counts() -> None:
 assert parse_pytest_counts("3 passed, 1 skipped in 0.12s")=={"passed":3,"skipped":1}
 assert parse_pytest_counts("no usable runner summary") is None
