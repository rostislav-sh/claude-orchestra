"""Тесты scripts/run_tests.py. Запуск: python3 -m pytest dev/ -q"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/run_tests.py"


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".orchestra").mkdir()
    (proj / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "tests", "source_dirs": ["src"]}))
    (proj / "src").mkdir()
    (proj / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "tests").mkdir()
    (proj / "tests/conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))\n"
    )
    (proj / "tests/test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "class TestGroup:\n    def test_zero(self):\n        assert add(0, 0) == 0\n"
    )
    return proj


def run(project, *args, cwd=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=str(cwd or project),
        capture_output=True, encoding="utf-8", env=env,
    )
    assert proc.stdout.strip(), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_green_run_is_short(project):
    code, res = run(project)
    assert code == 0
    assert res["status"] == "passed"
    assert (res["passed"], res["failed"], res["errors"]) == (2, 0, 0)
    assert res["failures"] == []
    assert "output_tail" not in res
    saved = json.loads((project / ".orchestra/reports/last-test-run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "passed"


def test_failure_has_test_id_message_and_location(project):
    (project / "src/calc.py").write_text("def add(a, b):\n    return a - b\n")
    code, res = run(project)
    assert code == 1
    assert res["status"] == "failed"
    assert res["failed"] == 1 and res["passed"] == 1
    fail = res["failures"][0]
    assert fail["test"] == "tests/test_calc.py::test_add"
    assert "assert" in fail["message"]
    assert fail["location"].startswith("tests/test_calc.py:")
    assert fail["details"]


def test_class_name_in_test_id(project):
    (project / "src/calc.py").write_text("def add(a, b):\n    return 1\n")
    _, res = run(project)
    ids = {f["test"] for f in res["failures"]}
    assert "tests/test_calc.py::TestGroup::test_zero" in ids


def test_collection_error_is_reported(project):
    (project / "tests/test_broken.py").write_text("import not_a_module_xyz\n")
    code, res = run(project)
    assert code != 0
    assert res["errors"] >= 1
    assert any("not_a_module_xyz" in (f["message"] + f["details"]) for f in res["failures"])


def test_details_are_trimmed(project):
    body = "\n".join(f"    x{i} = {i}" for i in range(200))
    (project / "tests/test_long.py").write_text(f"def test_long():\n{body}\n    assert x0 == 1\n")
    _, res = run(project, "--no-cache")
    fail = next(f for f in res["failures"] if "test_long" in f["test"])
    assert len(fail["details"].splitlines()) <= 26
    assert len(fail["details"]) <= 2501


def test_max_failures(project):
    cases = "\n".join(f"def test_f{i}():\n    assert False\n" for i in range(5))
    (project / "tests/test_many.py").write_text(cases)
    _, res = run(project, "--max-failures", "2")
    assert len(res["failures"]) == 2
    assert res["failures_not_shown"] == 3
    assert res["failed"] == 5


def test_pytest_args_pass_through(project):
    code, res = run(project, "tests/test_calc.py", "-k", "zero")
    assert code == 0
    assert res["passed"] == 1
    assert res["command"].endswith("tests/test_calc.py -k zero")


def test_no_tests(project):
    code, res = run(project, "-k", "nothing_matches_this")
    assert res["status"] == "no_tests"
    assert code == 5


def test_green_result_is_cached_until_inputs_change(project):
    _, first = run(project)
    assert "cached" not in first
    _, second = run(project)
    assert second["cached"] is True
    (project / "src/calc.py").write_text("def add(a, b):\n    return b + a\n")
    _, third = run(project)
    assert "cached" not in third
    (project / "tests/test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    _, fourth = run(project)
    assert "cached" not in fourth and fourth["passed"] == 1


def test_other_args_are_not_served_from_cache(project):
    run(project)
    _, res = run(project, "-k", "zero")
    assert "cached" not in res


def test_red_result_is_not_cached(project):
    (project / "src/calc.py").write_text("def add(a, b):\n    return 0\n")
    run(project)
    _, res = run(project)
    assert "cached" not in res


def test_no_cache_flag(project):
    run(project)
    _, res = run(project, "--no-cache")
    assert "cached" not in res


def test_runs_from_subdirectory(project):
    code, res = run(project, cwd=project / "src")
    assert code == 0 and res["passed"] == 2


def test_test_command_from_config(project):
    cfg = {"tests_dir": "tests", "source_dirs": ["src"], "test_command": f'"{sys.executable}" -m pytest'}
    (project / ".orchestra/config.json").write_text(json.dumps(cfg))
    code, res = run(project)
    assert code == 0 and res["passed"] == 2


def test_non_pytest_command_gets_output_tail(project):
    script = project / "fail.py"
    script.write_text("print('boom')\nraise SystemExit(3)\n")
    cfg = {"tests_dir": "tests", "source_dirs": ["src"], "test_command": f'"{sys.executable}" fail.py'}
    (project / ".orchestra/config.json").write_text(json.dumps(cfg))
    code, res = run(project)
    assert code == 3
    assert "boom" in res["output_tail"]


def test_works_without_orchestra_dir(tmp_path):
    proj = tmp_path / "plain"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests/test_x.py").write_text("def test_x():\n    assert True\n")
    code, res = run(proj)
    assert code == 0 and res["passed"] == 1
    assert not (proj / ".orchestra").exists()
