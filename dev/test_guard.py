"""Тесты хука guard.py. Запуск: python3 -m pytest dev/ -q"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/guard.py"
ALLOW, BLOCK = 0, 2


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / ".orchestra").mkdir()
    (proj / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "tests", "source_dirs": ["src"]}))
    (proj / "src").mkdir()
    (proj / "src/a.py").write_text("def f():\n    return 1\n")
    (proj / "tests").mkdir()
    (proj / "tests/test_a.py").write_text("def test_f():\n    assert True\n")
    monkeypatch.setenv("ORCHESTRA_GUARD_STATE", str(tmp_path / "state"))
    return proj


def call(project, agent_type, tool, tool_input, cwd=None, event="PreToolUse", tool_use_id=None,
         agent_id=None, project_dir=None, extra_env=None):
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(cwd or project),
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
        payload["agent_id"] = agent_id or f"id-{agent_type}"
    elif agent_id:
        payload["agent_id"] = agent_id
    if tool_use_id:
        payload["tool_use_id"] = tool_use_id
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir or project), **(extra_env or {}))
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, encoding="utf-8", env=env,
    )


def run(project, agent_type, tool, tool_input, **kw):
    return call(project, agent_type, tool, tool_input, **kw).returncode


# ------------------------------------------------------------ общие правила


@pytest.mark.parametrize("agent", [None, "Explore", "other-plugin:coder"])
def test_ignores_main_session_and_foreign_agents(project, agent):
    assert run(project, agent, "Write", {"file_path": "tests/x.py"}) == ALLOW
    assert run(project, agent, "Bash", {"command": "git commit -m x"}) == ALLOW


def test_garbage_input_does_not_block(project):
    proc = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True)
    assert proc.returncode == 0


def test_utf8_io_regardless_of_locale(project):
    # На Windows потоки Python по умолчанию в cp1251, а Claude Code говорит с хуком в UTF-8.
    proc = call(project, "orchestra:tester", "Write", {"file_path": "src/модуль.py"},
                extra_env={"PYTHONIOENCODING": "cp1251"})
    assert proc.returncode == BLOCK
    assert "Тестер" in proc.stderr
    assert "src/модуль.py" in proc.stderr


def test_no_orchestra_dir_main_session_is_noop(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("ORCHESTRA_GUARD_STATE", str(state))
    proj = tmp_path / "plain"
    proj.mkdir()
    assert run(proj, None, "Bash", {"command": "ls"}, tool_use_id="t1") == ALLOW
    assert not state.exists()


# ------------------------------------------------------------ git


@pytest.mark.parametrize("cmd", [
    "git commit -m x", "git push", "git reset --hard HEAD", "git -C wt stash",
    "git checkout -- src/a.py", "git restore src/a.py",
    # обходы, найденные на ревью
    "git clean -fd", "git clean -df", "git clean --force",
    "git -c user.name=x commit -m x", "git --no-pager commit -m x", "git -cuser.name=x commit",
    "git --git-dir=.git commit -m x", "git checkout src/a.py", "git checkout HEAD -- src/a.py",
    "git reset HEAD~1", "git switch -f main", "git stash", "git stash pop",
    "git branch -D main", "git branch new-branch", "git tag v1", "git tag -d v1",
    "git config user.name x", "git worktree add ../x", "git apply patch.diff",
    "git rebase main", "git merge x", "git cherry-pick abc", "git revert HEAD", "git am x.patch",
    "git update-ref -d HEAD", "git rm src/a.py", "git mv src/a.py src/b.py", "git add .",
    # обёртки и вложенность
    "ls && git commit -m x", "echo ok; git push", "true || git reset --hard",
    "sudo git commit -m x", "env GIT_AUTHOR_NAME=x git commit -m x", "GIT_DIR=.git git commit",
    "timeout 10 git push", "sh -c 'git commit -m x'", "bash -lc \"git push\"", "eval git commit -m x",
    "echo $(git commit -m x)", "/usr/bin/git commit -m x", "x=1\ngit commit -m x",
    "git --bogus-option commit",
])
def test_git_write_commands_blocked(project, cmd):
    assert run(project, "orchestra:coder", "Bash", {"command": cmd}) == BLOCK, cmd


@pytest.mark.parametrize("cmd", [
    "git diff && git status", "git log --oneline -5", "git show HEAD", "git blame src/a.py",
    "git -C wt diff", "git --no-pager log -p", "git -c color.ui=never diff", "git branch",
    "git branch -a", "git branch --list 'feat/*'", "git branch --contains abc", "git tag",
    "git tag -l 'v*'", "git stash list", "git worktree list", "git remote -v",
    "git config --get user.name", "git rev-parse --show-toplevel", "git ls-files",
    "git apply --check x.patch", "git --version", "git reflog",
    "grep -n git README.md", "echo git commit", "pytest -k git",
])
def test_git_read_commands_allowed(project, cmd):
    assert run(project, "orchestra:reviewer", "Bash", {"command": cmd}) == ALLOW, cmd


def test_unparseable_command_with_git_still_checked(project):
    assert run(project, "orchestra:coder", "Bash", {"command": "git commit -m 'unterminated"}) == BLOCK


# ------------------------------------------------------------ запись через инструменты


def test_tester_writes_only_tests(project):
    assert run(project, "orchestra:tester", "Write", {"file_path": "tests/test_a.py"}) == ALLOW
    assert run(project, "orchestra:tester", "Write", {"file_path": str(project / "tests/b.py")}) == ALLOW
    assert run(project, "orchestra:tester", "Write", {"file_path": "src/a.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Edit", {"file_path": ".orchestra/contracts.md"}) == BLOCK


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


def test_default_config_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_GUARD_STATE", str(tmp_path / "state"))
    proj = tmp_path / "p"
    proj.mkdir()
    assert run(proj, "orchestra:tester", "Read", {"file_path": "app/main.py"}) == BLOCK
    assert run(proj, "orchestra:tester", "Write", {"file_path": "tests/t.py"}) == ALLOW


# ------------------------------------------------------------ тестер: чтение


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


def test_tester_tests_inside_sources(project):
    (project / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "src/tests", "source_dirs": ["src"]}))
    assert run(project, "orchestra:tester", "Read", {"file_path": "src/tests/test_a.py"}) == ALLOW
    assert run(project, "orchestra:tester", "Write", {"file_path": "src/tests/test_b.py"}) == ALLOW
    assert run(project, "orchestra:tester", "Read", {"file_path": "src/a.py"}) == BLOCK
    assert run(project, "orchestra:tester", "Bash", {"command": "cat src/tests/conftest.py"}) == ALLOW


@pytest.mark.parametrize("cmd", [
    "cat src/service.py", "grep -r def ./src/", "cd src && cat a.py", "cd src; cat *.py",
    "python3 - <<X\nprint(open('src/a.py').read())\nX",
    "python3 -c \"print(open('src/a.py').read())\"",
    "vim -es -c '%p' -c q src/a.py", "less src/a.py", "head -c 999 src/a.py",
    "grep -rn def .", "rg def", "find . -name '*.py' -exec cat {} +", "tree",
    "echo \"$(cat src/a.py)\"", "sh -c 'cat src/a.py'", "cp src/a.py tests/copy.py",
    "git diff", "git show HEAD", "git diff -- src/a.py", "git log -p", "git grep def",
])
def test_tester_shell_cannot_read_sources(project, cmd):
    assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == BLOCK, cmd


def test_tester_shell_absolute_path(project):
    assert run(project, "orchestra:tester", "Bash", {"command": f"cat {project.as_posix()}/src/a.py"}) == BLOCK


def windows_forms(project):
    """Как агент в Git Bash на Windows может написать путь к src/a.py."""
    posix = project.as_posix()
    drive, rest = posix[0], posix[2:]
    return [
        f"cat '{project}\\src\\a.py'",
        f"cat \"{posix}/src/a.py\"",
        f"cat {posix.lower()}/src/a.py",
        f"cat /{drive.lower()}{rest}/src/a.py",
        f"cat /cygdrive/{drive.lower()}{rest}/src/a.py",
        f"cd /{drive.lower()}{rest}/src && cat a.py",
        f"python -c \"print(open(r'{project}\\src\\a.py').read())\"",
    ]


@pytest.mark.skipif(os.name != "nt", reason="пути Windows и Git Bash")
def test_tester_shell_windows_paths(project):
    for cmd in windows_forms(project):
        assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == BLOCK, cmd


@pytest.mark.skipif(os.name != "nt", reason="пути Windows и Git Bash")
def test_tester_shell_windows_paths_to_tests_allowed(project):
    drive, rest = project.as_posix()[0], project.as_posix()[2:]
    for cmd in [f"cat /{drive.lower()}{rest}/tests/test_a.py", f"cat '{project}\\tests\\test_a.py'"]:
        assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == ALLOW, cmd


@pytest.mark.parametrize("cmd", [
    "python -m pytest tests -q", "pytest --cov=src tests", "pytest", "uv run pytest -x",
    "cat tests/test_a.py", "ls", "ls tests", "grep -rn def tests", "git status",
    "git diff -- tests/", "git log --oneline", "cat .orchestra/contracts.md",
    "cd tests && cat test_a.py",
    "python3 /opt/plugins/orchestra/scripts/run_tests.py --cov=src",
    "python3 /opt/plugins/orchestra/scripts/run_tests.py src/../tests -k export",
])
def test_tester_shell_allowed(project, cmd):
    assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == ALLOW, cmd


@pytest.mark.parametrize("cmd", [
    "docker build -f src/Dockerfile --target test .",
    "docker build --progress=plain -f src/Dockerfile --target=tests .",
    "docker buildx build --target test -f src/Dockerfile .",
    "docker compose run --rm -e X=1 app pytest -q",
    "docker compose -f src/compose.yml exec app uv run pytest tests",
    "docker-compose run app python -m pytest",
])
def test_tester_shell_docker_test_runs_allowed(project, cmd):
    assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == ALLOW, cmd


@pytest.mark.parametrize("cmd", [
    "docker build -f src/Dockerfile .",
    "docker build -f src/Dockerfile --target prod .",
    "docker compose run app cat src/a.py",
    "docker compose exec app sh -c 'cat src/a.py'",
    "docker run img cat src/a.py",
])
def test_tester_shell_docker_other_blocked(project, cmd):
    assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == BLOCK, cmd


def test_tester_shell_cd_to_project_then_pytest(project):
    assert run(project, "orchestra:tester", "Bash", {"command": f"cd {project.as_posix()} && python -m pytest"}) == ALLOW


def test_tester_shell_ignores_parent_dir_named_src(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_GUARD_STATE", str(tmp_path / "state"))
    proj = tmp_path / "src" / "myproj"
    (proj / ".orchestra").mkdir(parents=True)
    (proj / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "tests", "source_dirs": ["src"]}))
    (proj / "tests").mkdir()
    (proj / "tests/conftest.py").write_text("")
    assert run(proj, "orchestra:tester", "Bash", {"command": f"cat {proj.as_posix()}/tests/conftest.py"}) == ALLOW


# ------------------------------------------------------------ Bash: снимок и откат


def bash_cycle(project, role, command, mutate, *, agent_id=None, tid="toolu_1", cwd=None, project_dir=None):
    """Pre → изменение файлов (как будто это сделала команда) → Post."""
    pre = call(project, f"orchestra:{role}", "Bash", {"command": command}, tool_use_id=tid,
               agent_id=agent_id, cwd=cwd, project_dir=project_dir)
    assert pre.returncode == ALLOW, pre.stderr
    mutate()
    return call(project, f"orchestra:{role}", "Bash", {"command": command}, event="PostToolUse",
                tool_use_id=tid, agent_id=agent_id, cwd=cwd, project_dir=project_dir)


def test_coder_bash_edit_of_tests_is_reverted(project):
    test_file = project / "tests/test_a.py"
    original = test_file.read_text()
    post = bash_cycle(project, "coder", "sed -i 's/True/False/' tests/test_a.py",
                      lambda: test_file.write_text("def test_f():\n    assert False\n"))
    assert post.returncode == BLOCK
    assert "откачены" in post.stderr
    assert test_file.read_text() == original
    assert "tests/test_a.py" in (project / ".orchestra/reports/guard.log").read_text()


def test_coder_bash_new_and_deleted_tests_are_reverted(project):
    def mutate():
        (project / "tests/test_a.py").unlink()
        (project / "tests/test_new.py").write_text("x = 1\n")
    post = bash_cycle(project, "coder", "rm tests/test_a.py; echo x > tests/test_new.py", mutate)
    assert post.returncode == BLOCK
    assert (project / "tests/test_a.py").exists()
    assert not (project / "tests/test_new.py").exists()


def test_coder_bash_source_changes_are_fine(project):
    post = bash_cycle(project, "coder", "ruff format src",
                      lambda: (project / "src/a.py").write_text("def f():\n    return 2\n"))
    assert post.returncode == ALLOW
    assert "return 2" in (project / "src/a.py").read_text()


def test_caches_are_ignored(project):
    def mutate():
        (project / "tests/__pycache__").mkdir()
        (project / "tests/__pycache__/test_a.cpython-313.pyc").write_bytes(b"x")
        (project / "tests/.pytest_cache").mkdir()
    post = bash_cycle(project, "coder", "pytest", mutate)
    assert post.returncode == ALLOW


def test_touch_without_content_change_is_fine(project):
    test_file = project / "tests/test_a.py"
    post = bash_cycle(project, "coder", "touch tests/test_a.py",
                      lambda: os.utime(test_file, (1, 1)))
    assert post.returncode == ALLOW


@pytest.mark.parametrize("role", ["reviewer", "debugger"])
def test_readonly_roles_bash_edit_reverted(project, role):
    src = project / "src/a.py"
    original = src.read_text()
    post = bash_cycle(project, role, "sed -i 's/1/2/' src/a.py", lambda: src.write_text("broken"))
    assert post.returncode == BLOCK
    assert src.read_text() == original


def test_reviewer_report_via_bash_is_fine(project):
    def mutate():
        (project / ".orchestra/reports").mkdir(exist_ok=True)
        (project / ".orchestra/reports/review.md").write_text("ok")
    assert bash_cycle(project, "reviewer", "echo ok > .orchestra/reports/review.md", mutate).returncode == ALLOW


def test_tester_bash_cannot_change_sources(project):
    src = project / "src/a.py"
    original = src.read_text()
    post = bash_cycle(project, "tester", "python tests/helper.py", lambda: src.write_text("hacked"))
    assert post.returncode == BLOCK
    assert src.read_text() == original


def test_post_failure_event_also_checked(project):
    test_file = project / "tests/test_a.py"
    original = test_file.read_text()
    tid = "toolu_fail"
    assert run(project, "orchestra:coder", "Bash", {"command": "x"}, tool_use_id=tid) == ALLOW
    test_file.write_text("changed")
    post = call(project, "orchestra:coder", "Bash", {"command": "x"}, event="PostToolUseFailure", tool_use_id=tid)
    assert post.returncode == BLOCK
    assert test_file.read_text() == original


def test_concurrent_tester_write_is_not_reverted(project):
    """Тестер пишет тест через Write, пока идёт Bash кодера: это не нарушение кодера."""
    new_test = project / "tests/test_b.py"

    def mutate():
        assert run(project, "orchestra:tester", "Write", {"file_path": str(new_test)}) == ALLOW
        new_test.write_text("def test_b(): pass\n")

    post = bash_cycle(project, "coder", "pytest", mutate)
    assert post.returncode == ALLOW
    assert new_test.exists()


def test_concurrent_main_session_write_is_not_reverted(project):
    src = project / "src/a.py"

    def mutate():
        assert run(project, None, "Edit", {"file_path": "src/a.py"}) == ALLOW
        src.write_text("fixed by teamlead")

    post = bash_cycle(project, "reviewer", "git diff", mutate)
    assert post.returncode == ALLOW
    assert src.read_text() == "fixed by teamlead"


def test_concurrent_bash_of_other_agent_warns_without_revert(project):
    """Если параллельно шёл чужой Bash, автор изменения неясен: предупреждаем, не откатываем."""
    test_file = project / "tests/test_a.py"

    def mutate():
        assert run(project, "orchestra:tester", "Bash", {"command": "python gen.py"}, tool_use_id="toolu_tester") == ALLOW
        test_file.write_text("generated")

    post = bash_cycle(project, "coder", "pytest", mutate)
    assert post.returncode == BLOCK
    assert "не откачены" in post.stderr
    assert test_file.read_text() == "generated"


def test_snapshot_removed_after_post(project, tmp_path):
    bash_cycle(project, "coder", "ls", lambda: None)
    state = tmp_path / "state"
    assert not list(state.rglob("snap-*"))


# ------------------------------------------------------------ worktree


def make_worktree(path):
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: /somewhere/.git/worktrees/w\n")
    (path / "src").mkdir()
    (path / "src/a.py").write_text("x = 1\n")
    (path / "tests").mkdir()
    (path / "tests/test_a.py").write_text("def test(): pass\n")
    return path


def test_worktree_inside_project(project):
    wt = make_worktree(project / ".claude/worktrees/w")
    assert run(project, "orchestra:coder", "Write", {"file_path": str(wt / "src/a.py")}, cwd=wt) == ALLOW
    assert run(project, "orchestra:coder", "Write", {"file_path": str(wt / "tests/test_a.py")}, cwd=wt) == BLOCK
    assert run(project, "orchestra:coder", "Write", {"file_path": "tests/test_b.py"}, cwd=wt) == BLOCK
    # из worktree нельзя писать в основную рабочую копию
    assert run(project, "orchestra:coder", "Write", {"file_path": str(project / "src/a.py")}, cwd=wt) == BLOCK


def test_worktree_outside_project(project, tmp_path):
    wt = make_worktree(tmp_path / "wt-outside")
    assert run(project, "orchestra:coder", "Write", {"file_path": str(wt / "src/a.py")}, cwd=wt) == ALLOW
    assert run(project, "orchestra:coder", "Write", {"file_path": str(wt / "tests/test_a.py")}, cwd=wt) == BLOCK


def test_worktree_bash_revert(project):
    wt = make_worktree(project / ".claude/worktrees/w")
    test_file = wt / "tests/test_a.py"
    original = test_file.read_text()
    post = bash_cycle(project, "coder", "sed -i x tests/test_a.py", lambda: test_file.write_text("bad"), cwd=wt)
    assert post.returncode == BLOCK
    assert test_file.read_text() == original


@pytest.mark.parametrize("cmd", [
    "python3 -m run_tests src",           # не скрипт плагина, а модуль
    "python3 other.py src/a.py",
    "python3 -c 'print(open(\"src/a.py\").read())'",
])
def test_tester_shell_only_run_tests_script_counts_as_test_run(project, cmd):
    assert run(project, "orchestra:tester", "Bash", {"command": cmd}) == BLOCK, cmd


def test_block_is_written_to_guard_log(project):
    assert run(project, "orchestra:tester", "Read", {"file_path": str(project / "src/a.py")}) == BLOCK
    log = (project / ".orchestra/reports/guard.log").read_text(encoding="utf-8")
    assert "tester: заблокировано Read" in log and "src/a.py" in log
