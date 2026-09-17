import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATHER_SCRIPTS = sorted(PROJECT_ROOT.glob("skills/*/scripts/gather.py"))


@pytest.mark.parametrize("script", GATHER_SCRIPTS, ids=lambda path: path.parents[1].name)
def test_skill_gather_cli_reaches_existing_tool(script, monkeypatch):
    called = []

    def fake_run(command, **_kwargs):
        called.append(command)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(script), "A"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(script), run_name="__main__")

    assert exited.value.code == 0
    assert Path(called[0][1]).is_file()
    assert Path(called[0][1]).parent == PROJECT_ROOT / "tools"


@pytest.mark.parametrize(
    ("skill_name", "schema_name"),
    [("market-review", "market_review_schema.json"), ("stock-analysis", "report_schema.json")],
)
def test_skill_links_canonical_schema(skill_name, schema_name):
    skill = PROJECT_ROOT / "skills" / skill_name / "SKILL.md"
    rel = f"../../schemas/{schema_name}"
    schema = (skill.parent / rel).resolve()
    assert schema == PROJECT_ROOT / "schemas" / schema_name
    assert schema.is_file()
    assert rel in skill.read_text(encoding="utf-8")
