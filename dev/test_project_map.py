"""Тесты scripts/project_map.py. Запуск: python3 -m pytest dev/ -q"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/project_map.py"


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / ".orchestra").mkdir(parents=True)
    (p / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "tests", "source_dirs": ["src"]}))
    (p / "src/app/api").mkdir(parents=True)
    (p / "src/app/__init__.py").write_text("")
    (p / "src/app/api/__init__.py").write_text("")
    (p / "src/app/main.py").write_text('"""Точка входа FastAPI."""\nfrom fastapi import FastAPI\napp = FastAPI()\n')
    (p / "src/app/api/orders.py").write_text(
        '"""Эндпоинты заказов."""\n'
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef list_orders():\n    return []\n\n'
        '@router.post("/orders")\nasync def create_order():\n    return {}\n\n'
        "def _private():\n    pass\n"
    )
    (p / "src/app/service.py").write_text(
        "class OrderService:\n    def create(self):\n        pass\n    def _hidden(self):\n        pass\n\n"
        "TRANSITIONS = {}\n__all__ = []\nT = 1\n\ndef helper():\n    pass\n"
    )
    (p / "src/app/broken.py").write_text("def f(:\n")
    (p / "Dockerfile").write_text("FROM python:3.12\n")
    (p / "README.md").write_text("readme")
    (p / "tests").mkdir()
    (p / "tests/conftest.py").write_text(
        "import pytest\n\n@pytest.fixture(scope='session')\ndef client():\n    \"\"\"HTTP-клиент приложения.\"\"\"\n    return 1\n"
    )
    (p / "tests/test_orders.py").write_text(
        "from app.api.orders import list_orders\n\n"
        "def test_list(client):\n    assert list_orders() == []\n\n"
        "class TestCreate:\n    def test_ok(self, tmp_path):\n        pass\n"
    )
    return p


def run(project, *args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(project)],
                          input=stdin, capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def read_code_md(project):
    return (project / ".orchestra/map/code.md").read_text(encoding="utf-8")


def read_tests_md(project):
    return (project / ".orchestra/map/tests.md").read_text(encoding="utf-8")


def test_build_code_map(project):
    res = run(project, "build")
    assert res["code_files"] == 7 and res["test_files"] == 2
    md = read_code_md(project)
    assert "## src/app/" in md and "## src/app/api/" in md
    assert "`orders.py` — Эндпоинты заказов." in md
    assert "`list_orders()`" in md and "_private" not in md
    assert "`OrderService`(create)" in md and "_hidden" not in md
    assert "`TRANSITIONS`" in md and "`__all__`" not in md and "`T`" not in md
    assert "`__init__.py`" not in md   # пустые пакетные файлы не засоряют карту
    assert "GET /orders → `list_orders` · `src/app/api/orders.py`" in md
    assert "POST /orders → `create_order`" in md
    assert "**Точки входа:** `src/app/main.py`" in md
    assert "`Dockerfile`" in md and "README" not in md
    assert "синтаксическая ошибка" in md


def test_need_description_only_without_docstring(project):
    res = run(project, "build")
    assert sorted(res["need_description"]) == ["src/app/broken.py", "src/app/service.py"]
    assert "src/app/__init__.py" not in res["need_description"]   # пустой __init__
    assert "Dockerfile" not in res["need_description"]


def test_describe_and_staleness(project):
    run(project, "build")
    res = run(project, "describe", stdin=json.dumps({"./src/app/service.py": "Бизнес-логика заказов", "nope.py": "x"}))
    assert res["unknown"] == ["nope.py"]
    assert "src/app/service.py" not in res["need_description"]
    assert "`service.py` — Бизнес-логика заказов" in read_code_md(project)
    # файл изменился — описание устарело и снова нужно
    (project / "src/app/service.py").write_text("def helper():\n    return 2\n")
    res = run(project, "build")
    assert res["changed"] == ["src/app/service.py"]
    assert "src/app/service.py" in res["need_description"]
    assert "(устарело) Бизнес-логика заказов" in read_code_md(project)


def test_stale_description_not_required_when_docstring(project):
    run(project, "build")
    run(project, "describe", stdin=json.dumps({"src/app/api/orders.py": "Заказы: HTTP"}))
    (project / "src/app/api/orders.py").write_text('"""Эндпоинты заказов v2."""\n')
    res = run(project, "build")
    assert "src/app/api/orders.py" not in res["need_description"]


def test_added_and_removed(project):
    run(project, "build")
    (project / "src/app/new.py").write_text('"""Новый модуль."""\n')
    (project / "src/app/broken.py").unlink()
    res = run(project, "build")
    assert res["added"] == ["src/app/new.py"] and res["removed"] == ["src/app/broken.py"]


def test_first_build_does_not_report_everything_as_added(project):
    assert run(project, "build")["added"] == []


def test_tests_map(project):
    run(project, "build")
    md = read_tests_md(project)
    assert "тестов: 2" in md
    assert "`client` (tests/conftest.py, session) — HTTP-клиент приложения." in md
    assert "`test_orders.py` — 2 тест(ов) · модули: `app.api.orders` · фикстуры: client, tmp_path" in md
    assert "test_list, TestCreate.test_ok" in md
    assert "## Модули без тестов" in md and "`app.service`" in md and "`app.main`" in md
    assert "`app.api.orders`" not in md.split("## Модули без тестов")[1]
    assert "python -m pytest tests" in md


def test_tests_map_has_no_code_symbols(project):
    """tests.md открыт тестеру — в нём не должно быть устройства реализации."""
    run(project, "build")
    md = read_tests_md(project)
    assert "OrderService" not in md and "GET /orders" not in md and "Эндпоинты заказов" not in md


def test_last_run_in_tests_map(project):
    (project / ".orchestra/reports").mkdir()
    (project / ".orchestra/reports/last-test-run.json").write_text(json.dumps({
        "status": "failed", "passed": 1, "failed": 1, "errors": 0, "skipped": 0, "finished_at": "2026-11-08T10:00:00",
        "failures": [{"test": "tests/test_orders.py::test_list", "message": "assert 1 == 2"}]}))
    run(project, "build")
    md = read_tests_md(project)
    assert "failed — 1 прошло, 1 упало" in md and "test_list` — assert 1 == 2" in md


def test_whole_project_when_no_source_dirs(tmp_path):
    p = tmp_path / "flat"
    (p / ".orchestra").mkdir(parents=True)
    (p / "mybot").mkdir()
    (p / "mybot/handlers.py").write_text('"""Хендлеры бота."""\n')
    (p / ".venv/lib").mkdir(parents=True)
    (p / ".venv/lib/junk.py").write_text("")
    run(p, "build")
    md = read_code_md(p)
    assert "Хендлеры бота" in md and "junk" not in md


def test_need_command(project):
    assert sorted(run(project, "need")["need_description"]) == ["src/app/broken.py", "src/app/service.py"]


def test_requires_orchestra_dir(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "build", "--root", str(tmp_path)],
                          capture_output=True, encoding="utf-8")
    assert proc.returncode == 1 and "error" in json.loads(proc.stdout)


def test_nested_routes_fields_and_enums(project):
    (project / "src/app/models.py").write_text(
        '"""Модели."""\nfrom dataclasses import dataclass\nfrom enum import Enum\n\n'
        "class Status(str, Enum):\n    OPEN = 'open'\n    CLOSED = 'closed'\n\n"
        "@dataclass\nclass Ticket:\n    id: int\n    title: str\n    _secret: str = ''\n    def close(self):\n        pass\n"
    )
    (project / "src/app/api/users.py").write_text(
        '"""Эндпоинты пользователей."""\n\ndef register(router):\n'
        '    @router.post("/users")\n    def create_user(req):\n        return 1\n'
    )
    run(project, "build")
    md = read_code_md(project)
    assert "`Status`{OPEN, CLOSED}" in md
    assert "`Ticket`{id, title}(close)" in md and "_secret" not in md
    assert "POST /users → `create_user` · `src/app/api/users.py`" in md
