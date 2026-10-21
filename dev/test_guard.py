"""Тесты хука guard.py. Запуск: python3 -m pytest dev/ -q"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/guard.py"


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".orchestra").mkdir()
    (tmp_path / ".orchestra/config.json").write_text(
        json.dumps({"tests_dir": "tests", "source_dirs": ["src"]})
    )
    return tmp_path


def run(project, agent_type, tool, tool_input, cwd=None):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(cwd or project),
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )
    return proc.returncode


ALLOW, BLOCK = 0, 2


@pytest.mark.parametrize("agent", [None, "Explore", "other-plugin:coder"])
def test_ignores_main_session_and_foreign_agents(project, agent):
    assert run(project, agent, "Write", {"file_path": "tests/x.py"}) == ALLOW
    assert run(project, agent, "Bash", {"command": "git commit -m x"}) == ALLOW


@pytest.mark.parametrize("cmd", [
    "git commit -m x", "git push", "git reset --hard HEAD", "git -C wt stash",
    "git checkout -- src/a.py", "git restore src/a.py",
])
def test_no_agent_can_commit_or_rollback(project, cmd):
    assert run(project, "orchestra:coder", "Bash", {"command": cmd}) == BLOCK


def test_git_read_commands_allowed(project):
    assert run(project, "orchestra:reviewer", "Bash", {"command": "git diff && git status"}) == ALLOW


def test_tester_writes_only_tests(project):
    assert run(project, "orchestra:tester", "Write", {"file_path": "tests/test_a.py"}) == ALLOW
    assert run(project, "orchestra:tester", "Write", {"file_path": str(project / "tests/b.py")}) == ALLOW
    assert run(project, "orchestra:tester", "Write", {"file_path": "src/a.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Edit", {"file_path": ".orchestra/contracts.md"}) == BLOCK


def test_tester_cannot_read_sources(project):
    assert run(project, "orchestra:tester", "Read", {"file_path": "src/service.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Read", {"file_path": ".orchestra/contracts.md"}) == ALLOW
    assert run(project, "orchestra:tester", "Read", {"file_path": "pyproject.toml"}) == ALLOW
    assert run(project, "orchestra:tester", "Grep", {"pattern": "def", "path": "src"}) == BLOCK
    assert run(project, "orchestra:tester", "Grep", {"pattern": "def"}) == BLOCK
    assert run(project, "orchestra:tester", "Grep", {"pattern": "def", "path": "tests"}) == ALLOW
    assert run(project, "orchestra:tester", "Glob", {"pattern": "**/*.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Glob", {"pattern": "src/**/*.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Glob", {"pattern": "tests/**/*.py"}) == ALLOW


def test_tester_shell(project):
    assert run(project, "orchestra:tester", "Bash", {"command": "cat src/service.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Bash", {"command": "grep -r def ./src/"}) == BLOCK
    assert run(project, "orchestra:tester", "Bash", {"command": "python -m pytest tests -q"}) == ALLOW
    assert run(project, "orchestra:tester", "Bash", {"command": "pytest --cov=src tests"}) == ALLOW


def test_coder_rules(project):
    assert run(project, "orchestra:coder", "Write", {"file_path": "src/a.py"}) == ALLOW
    assert run(project, "orchestra:coder", "Write", {"file_path": "Dockerfile"}) == ALLOW
    assert run(project, "orchestra:coder", "Edit", {"file_path": "tests/test_a.py"}) == BLOCK
    assert run(project, "orchestra:coder", "Write", {"file_path": ".orchestra/plan.md"}) == BLOCK
    assert run(project, "orchestra:coder", "Write", {"file_path": ".orchestra/reports/T1.md"}) == ALLOW
    assert run(project, "orchestra:coder", "Read", {"file_path": "tests/test_a.py"}) == ALLOW


def test_architect_rules(project):
    assert run(project, "orchestra:architect", "Write", {"file_path": ".orchestra/contracts.md"}) == ALLOW
    assert run(project, "orchestra:architect", "Write", {"file_path": "src/protocols.py"}) == ALLOW
    assert run(project, "orchestra:architect", "Write", {"file_path": "tests/test_x.py"}) == BLOCK


@pytest.mark.parametrize("role", ["reviewer", "debugger"])
def test_readonly_roles(project, role):
    assert run(project, f"orchestra:{role}", "Edit", {"file_path": "src/a.py"}) == BLOCK
    assert run(project, f"orchestra:{role}", "Write", {"file_path": ".orchestra/reports/r.md"}) == ALLOW
    assert run(project, f"orchestra:{role}", "Read", {"file_path": "src/a.py"}) == ALLOW


def test_no_writes_outside_project(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "x.py"
    assert run(project, "orchestra:coder", "Write", {"file_path": str(outside)}) == BLOCK
    assert run(project, "orchestra:coder", "Write", {"file_path": "../escape.py"}) == BLOCK


def test_default_config_when_missing(tmp_path):
    assert run(tmp_path, "orchestra:tester", "Read", {"file_path": "app/main.py"}) == BLOCK
    assert run(tmp_path, "orchestra:tester", "Write", {"file_path": "tests/t.py"}) == ALLOW


def test_garbage_input_does_not_block(project):
    proc = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True)
    assert proc.returncode == 0
