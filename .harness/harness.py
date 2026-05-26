#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.cwd()
HARNESS_DIR = ROOT / ".harness"
DB_PATH = HARNESS_DIR / "harness.db"
CONFIG_PATH = HARNESS_DIR / "config.yaml"
LOG_DIR = HARNESS_DIR / "logs"
LATEST_SCHEMA_VERSION = 4
SPEC_STATUSES = {"implementing", "ready_for_review", "blocked", "closed"}


DEFAULT_CONFIG: dict[str, Any] = {
    "project": {"name": ROOT.name, "language": "auto"},
    "workflow": {
        "require_tests_before_ready": True,
        "stop_on_scope_violation": True,
        "stop_on_environment_failure": True,
    },
    "commands": {
        "install": [],
        "test": {"targeted": [], "full": []},
        "lint": [],
        "build": [],
    },
    "review": {
        "max_diff_lines_warn": 800,
        "require_tests_for_code_changes": True,
    },
    "memory": {
        "default_active": True,
        "max_context_results": 8,
        "allowed_kinds": [
            "command",
            "decision",
            "pitfall",
            "convention",
            "architecture",
            "testing",
        ],
    },
}


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS specs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  area TEXT NOT NULL DEFAULT 'general',
  goal TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  out_of_scope TEXT NOT NULL DEFAULT '',
  acceptance_criteria TEXT NOT NULL DEFAULT '',
  test_strategy TEXT NOT NULL DEFAULT '',
  test_plan TEXT NOT NULL DEFAULT '',
  risk_level TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'implementing',
  active INTEGER NOT NULL DEFAULT 1,
  review_cycles INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ready_at TEXT,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  spec_id TEXT,
  phase TEXT NOT NULL,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  duration_sec REAL,
  log_path TEXT,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(spec_id) REFERENCES specs(id)
);

CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'project',
  area TEXT NOT NULL DEFAULT 'general',
  kind TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.8,
  active INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  summary,
  content,
  area,
  kind,
  tags,
  content='memory',
  content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
  INSERT INTO memory_fts(rowid, summary, content, area, kind, tags)
  VALUES (new.rowid, new.summary, new.content, new.area, new.kind, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, summary, content, area, kind, tags)
  VALUES ('delete', old.rowid, old.summary, old.content, old.area, old.kind, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, summary, content, area, kind, tags)
  VALUES ('delete', old.rowid, old.summary, old.content, old.area, old.kind, old.tags);
  INSERT INTO memory_fts(rowid, summary, content, area, kind, tags)
  VALUES (new.rowid, new.summary, new.content, new.area, new.kind, new.tags);
END;
"""


def now() -> str:
    """Devuelve una marca temporal UTC estable para registros del harness."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    """Crea un identificador corto con prefijo legible."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def slugify(value: str) -> str:
    """Normaliza texto libre para usarlo en ids y patrones de busqueda."""
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:64] or "item"


def ensure_dirs() -> None:
    """Crea las carpetas locales que necesita el harness."""
    HARNESS_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Indica si una tabla existe en la base SQLite actual."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Obtiene las columnas actuales de una tabla si existe."""
    if not table_exists(conn, table):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    """Lee una columna opcional de forma segura durante migraciones."""
    return row[key] if key in row.keys() else default


def create_final_specs_table(conn: sqlite3.Connection, table: str) -> None:
    """Crea una tabla de specs con el esquema operativo final."""
    conn.execute(
        f"""
        CREATE TABLE {table} (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          area TEXT NOT NULL DEFAULT 'general',
          goal TEXT NOT NULL DEFAULT '',
          scope TEXT NOT NULL DEFAULT '',
          out_of_scope TEXT NOT NULL DEFAULT '',
          acceptance_criteria TEXT NOT NULL DEFAULT '',
          test_strategy TEXT NOT NULL DEFAULT '',
          test_plan TEXT NOT NULL DEFAULT '',
          risk_level TEXT NOT NULL DEFAULT 'medium',
          status TEXT NOT NULL DEFAULT 'implementing',
          active INTEGER NOT NULL DEFAULT 1,
          review_cycles INTEGER NOT NULL DEFAULT 0,
          outcome TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          ready_at TEXT,
          closed_at TEXT
        )
        """
    )


def create_final_runs_table(conn: sqlite3.Connection, table: str) -> None:
    """Crea una tabla de ejecuciones ligada solo a specs."""
    conn.execute(
        f"""
        CREATE TABLE {table} (
          id TEXT PRIMARY KEY,
          spec_id TEXT,
          phase TEXT NOT NULL,
          command TEXT NOT NULL,
          status TEXT NOT NULL,
          exit_code INTEGER,
          duration_sec REAL,
          log_path TEXT,
          summary TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(spec_id) REFERENCES specs(id)
        )
        """
    )


def old_task_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Resume las antiguas tasks por spec para migrar resultado y estado."""
    if not table_exists(conn, "tasks"):
        return {}
    summaries: dict[str, dict[str, Any]] = {}
    rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at").fetchall()
    for row in rows:
        spec_id = row["spec_id"]
        current = summaries.setdefault(
            spec_id,
            {"outcome": "", "status": "", "updated_at": ""},
        )
        if row_value(row, "outcome", ""):
            current["outcome"] = row["outcome"]
        current["status"] = row_value(row, "status", "")
        current["updated_at"] = row_value(row, "updated_at", "")
    return summaries


def mapped_spec_status(old_status: str, task_status: str) -> str:
    """Traduce estados antiguos al conjunto final de estados de spec."""
    if old_status == "closed":
        return "closed"
    if old_status == "draft":
        return "blocked"
    if task_status == "implementing" or old_status in {"locked", "in_progress"}:
        return "implementing"
    if old_status in SPEC_STATUSES:
        return old_status
    return "blocked"


def migrate_specs_to_v2(conn: sqlite3.Connection) -> None:
    """Reconstruye specs para eliminar tasks, draft/locked y commit_hash."""
    specs_columns = column_names(conn, "specs")
    final_columns = {
        "test_plan",
        "review_cycles",
        "outcome",
        "ready_at",
        "closed_at",
    }
    if final_columns.issubset(set(specs_columns)) and "commit_hash" not in specs_columns:
        return

    tasks = old_task_summary(conn)
    specs = conn.execute("SELECT * FROM specs").fetchall() if table_exists(conn, "specs") else []
    create_final_specs_table(conn, "specs_v2")
    timestamp = now()
    for spec in specs:
        task = tasks.get(spec["id"], {})
        old_status = row_value(spec, "status", "draft")
        status = mapped_spec_status(old_status, str(task.get("status", "")))
        outcome = str(task.get("outcome") or row_value(spec, "outcome", "") or "")
        if old_status == "draft" and not outcome:
            outcome = "Requiere re-aprobacion antes de implementar."
        conn.execute(
            """
            INSERT INTO specs_v2 (
              id, title, area, goal, scope, out_of_scope, acceptance_criteria,
              test_strategy, test_plan, risk_level, status, active,
              review_cycles, outcome, created_at, updated_at, ready_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec["id"],
                row_value(spec, "title", ""),
                row_value(spec, "area", "general"),
                row_value(spec, "goal", ""),
                row_value(spec, "scope", ""),
                row_value(spec, "out_of_scope", ""),
                row_value(spec, "acceptance_criteria", ""),
                row_value(spec, "test_strategy", ""),
                row_value(spec, "test_plan", ""),
                row_value(spec, "risk_level", "medium"),
                status,
                0 if status == "closed" else int(row_value(spec, "active", 1) or 0),
                int(row_value(spec, "review_cycles", 0) or 0),
                outcome,
                row_value(spec, "created_at", timestamp),
                row_value(spec, "updated_at", timestamp),
                row_value(spec, "ready_at", None),
                row_value(spec, "closed_at", row_value(spec, "updated_at", None) if status == "closed" else None),
            ),
        )
    conn.execute("DROP TABLE IF EXISTS specs")
    conn.execute("ALTER TABLE specs_v2 RENAME TO specs")


def migrate_runs_to_v2(conn: sqlite3.Connection) -> None:
    """Reconstruye runs para quitar la referencia antigua a tasks."""
    run_columns = column_names(conn, "runs")
    if not run_columns or "task_id" not in run_columns:
        return
    runs = conn.execute("SELECT * FROM runs").fetchall()
    create_final_runs_table(conn, "runs_v2")
    for run in runs:
        conn.execute(
            """
            INSERT INTO runs_v2 (
              id, spec_id, phase, command, status, exit_code, duration_sec,
              log_path, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["id"],
                run["spec_id"],
                run["phase"],
                run["command"],
                run["status"],
                run["exit_code"],
                run["duration_sec"],
                run["log_path"],
                run["summary"],
                run["created_at"],
            ),
        )
    conn.execute("DROP TABLE IF EXISTS runs")
    conn.execute("ALTER TABLE runs_v2 RENAME TO runs")


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Aplica migraciones idempotentes hasta el esquema actual."""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    conn.execute("PRAGMA foreign_keys = OFF")
    migrate_specs_to_v2(conn)
    migrate_runs_to_v2(conn)
    conn.execute("DROP TABLE IF EXISTS tasks")
    conn.execute("DROP TABLE IF EXISTS memory_candidates")
    conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
    conn.commit()
    if current < LATEST_SCHEMA_VERSION:
        print(
            f"Applied schema migration: v{current} -> v{LATEST_SCHEMA_VERSION}",
            file=sys.stderr,
        )


def connect() -> sqlite3.Connection:
    """Abre la base local y garantiza que el esquema esta actualizado."""
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    apply_migrations(conn)
    return conn


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fusiona configuraciones preservando valores por defecto anidados."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    """Carga config.yaml aceptando JSON o YAML si esta disponible."""
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG
    text = CONFIG_PATH.read_text(encoding="utf-8").strip()
    if not text:
        return DEFAULT_CONFIG
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(text) or {}
        except Exception:
            print("Warning: config.yaml could not be parsed; using defaults.", file=sys.stderr)
            return DEFAULT_CONFIG
    return deep_merge(DEFAULT_CONFIG, loaded)


def write_default_config(force: bool = False) -> None:
    """Escribe una configuracion inicial solo si no existe o se fuerza."""
    ensure_dirs()
    if CONFIG_PATH.exists() and not force:
        return
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")


def print_rows(rows: list[sqlite3.Row], fields: list[str]) -> None:
    """Imprime filas SQLite en formato compacto de consola."""
    if not rows:
        print("No rows.")
        return
    for row in rows:
        print("- " + " | ".join(f"{field}: {row[field]}" for field in fields))


def active_spec(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Devuelve la spec activa mas reciente si existe."""
    return conn.execute(
        "SELECT * FROM specs WHERE active = 1 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()


def get_spec(conn: sqlite3.Connection, spec_id: str | None) -> sqlite3.Row | None:
    """Busca una spec concreta o la activa si no se indica id."""
    if spec_id:
        return conn.execute("SELECT * FROM specs WHERE id = ?", (spec_id,)).fetchone()
    return active_spec(conn)


def detect_project() -> dict[str, Any]:
    """Detecta lenguaje y comandos habituales de test del proyecto actual."""
    files = {p.name for p in ROOT.iterdir() if p.is_file()}
    languages: list[str] = []
    full_cmds: list[str] = []
    targeted_cmds: list[str] = []
    lint_cmds: list[str] = []
    build_cmds: list[str] = []

    if (ROOT / "artisan").exists():
        languages.append("php-laravel")
        full_cmds.append("php artisan test")
        targeted_cmds.append("php artisan test --filter={pattern}")
    elif "composer.json" in files:
        languages.append("php")
        full_cmds.append("vendor/bin/phpunit")
        targeted_cmds.append("vendor/bin/phpunit --filter {pattern}")

    if "package.json" in files:
        languages.append("node")
        try:
            package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        except Exception:
            scripts = {}
        if "test" in scripts:
            full_cmds.append("npm test")
            targeted_cmds.append("npm test -- {pattern}")
        if "lint" in scripts:
            lint_cmds.append("npm run lint")
        if "build" in scripts:
            build_cmds.append("npm run build")

    has_python_tests = (ROOT / "tests").exists() and any((ROOT / "tests").glob("*.py"))
    has_python_project = (
        "pyproject.toml" in files
        or "pytest.ini" in files
        or "requirements.txt" in files
        or any(p.suffix == ".py" for p in ROOT.iterdir() if p.is_file())
        or has_python_tests
    )
    if not languages and has_python_project:
        languages.append("python")
        full_cmds.append("python -m unittest discover -s tests")
        targeted_cmds.append("python -m unittest discover -s tests")
        lint_cmds.append("python -m compileall .")

    if "go.mod" in files:
        languages.append("go")
        full_cmds.append("go test ./...")
        targeted_cmds.append("go test ./... -run {pattern}")

    if "Cargo.toml" in files:
        languages.append("rust")
        full_cmds.append("cargo test")
        targeted_cmds.append("cargo test {pattern}")

    if any(p.suffix in (".sln", ".csproj") for p in ROOT.iterdir() if p.is_file()):
        languages.append("dotnet")
        full_cmds.append("dotnet test")
        targeted_cmds.append('dotnet test --filter "{pattern}"')

    if "pom.xml" in files:
        languages.append("java-maven")
        full_cmds.append("mvn test")
        targeted_cmds.append("mvn -Dtest={pattern} test")

    if "build.gradle" in files or "build.gradle.kts" in files:
        languages.append("java-gradle")
        full_cmds.append("gradle test")
        targeted_cmds.append("gradle test --tests {pattern}")

    return {
        "language": "+".join(languages) if languages else "unknown",
        "test": {"full": full_cmds, "targeted": targeted_cmds},
        "lint": lint_cmds,
        "build": build_cmds,
    }


def configured_test_commands(config: dict[str, Any], mode: str, pattern: str) -> list[str]:
    """Resuelve comandos de test configurados o detectados para un patron."""
    commands = config.get("commands", {}).get("test", {}).get(mode, []) or []
    if not commands:
        detected = detect_project()
        commands = detected.get("test", {}).get(mode, []) or []
        if mode == "targeted" and not commands:
            commands = detected.get("test", {}).get("full", []) or []
    return [cmd.replace("{pattern}", pattern) for cmd in commands]


def git_diff_summary() -> dict[str, Any]:
    """Resume el diff de git sin modificar el repositorio."""
    if not (ROOT / ".git").exists():
        return {"available": False, "files": [], "lines": None}
    try:
        changed_files = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
        status_lines = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
        files = list(changed_files)
        for line in status_lines:
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            if path and path not in files:
                files.append(path)
        stat = subprocess.run(
            ["git", "diff", "--numstat"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
        lines = 0
        for line in stat:
            parts = line.split("\t")
            if len(parts) >= 2:
                for part in parts[:2]:
                    if part.isdigit():
                        lines += int(part)
        return {"available": True, "files": files, "lines": lines}
    except Exception:
        return {"available": False, "files": [], "lines": None}


def fts_query(query: str) -> str:
    """Convierte texto libre en una consulta FTS tolerante."""
    terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
    if not terms:
        return ""
    return " OR ".join(f"{term}*" for term in terms[:8])


def search_memory(
    conn: sqlite3.Connection, query: str, active_only: bool = True, limit: int = 8
) -> list[sqlite3.Row]:
    """Busca memoria reutilizable con FTS y fallback por LIKE."""
    match = fts_query(query)
    active_clause = "AND memory.active = 1 AND memory.status = 'active'" if active_only else ""
    if match:
        try:
            return conn.execute(
                f"""
                SELECT memory.*
                FROM memory_fts
                JOIN memory ON memory_fts.rowid = memory.rowid
                WHERE memory_fts MATCH ? {active_clause}
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass
    like = f"%{query}%"
    return conn.execute(
        f"""
        SELECT * FROM memory
        WHERE (summary LIKE ? OR content LIKE ? OR tags LIKE ? OR area LIKE ?)
        {active_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (like, like, like, like, limit),
    ).fetchall()


def command_init(args: argparse.Namespace) -> None:
    """Inicializa carpetas, config y base de datos del proyecto."""
    ensure_dirs()
    write_default_config(force=args.force_config)
    with connect():
        pass
    print(f"Initialized harness at {HARNESS_DIR}")
    print(f"Database: {DB_PATH}")


def command_detect(_: argparse.Namespace) -> None:
    """Muestra la deteccion de lenguaje y comandos disponibles."""
    print(json.dumps(detect_project(), indent=2))


def require_contract(args: argparse.Namespace) -> None:
    """Valida que spec create reciba todos los campos del contrato."""
    missing = [
        name
        for name in [
            "title",
            "area",
            "goal",
            "scope",
            "out_of_scope",
            "acceptance",
            "tests",
            "risk",
        ]
        if not (getattr(args, name, "") or "").strip()
    ]
    if missing:
        sys.exit(f"Missing required spec fields: {', '.join(missing)}")


def command_spec_create(args: argparse.Namespace) -> None:
    """Crea una spec aprobada y la deja lista para implementar."""
    require_contract(args)
    conn = connect()
    current = active_spec(conn)
    if current and current["status"] != "closed":
        sys.exit(f"Active spec already exists: {current['id']}. Close or block it before creating another.")
    timestamp = now()
    spec_id = f"spec_{slugify(args.title)}_{uuid.uuid4().hex[:6]}"
    conn.execute("UPDATE specs SET active = 0 WHERE active = 1")
    conn.execute(
        """
        INSERT INTO specs (
          id, title, area, goal, scope, out_of_scope, acceptance_criteria,
          test_strategy, risk_level, status, active, review_cycles, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'implementing', 1, 0, ?, ?)
        """,
        (
            spec_id,
            args.title,
            args.area,
            args.goal,
            args.scope,
            args.out_of_scope,
            args.acceptance,
            args.tests,
            args.risk,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    print(f"Created implementing spec: {spec_id}")
    print("Implement the scoped change, then run `harness test plan` before running tests.")


def command_spec_list(args: argparse.Namespace) -> None:
    """Lista specs filtrando por estado o area."""
    conn = connect()
    conditions: list[str] = []
    params: list[object] = []
    if args.status:
        conditions.append("status = ?")
        params.append(args.status)
    if args.area:
        conditions.append("area = ?")
        params.append(args.area)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT id, title, area, status, active, review_cycles, updated_at FROM specs {where} ORDER BY updated_at DESC",
        params,
    ).fetchall()
    print_rows(rows, ["id", "title", "area", "status", "active", "review_cycles", "updated_at"])


def command_spec_show(args: argparse.Namespace) -> None:
    """Muestra todos los campos relevantes de una spec."""
    conn = connect()
    spec = get_spec(conn, args.id)
    if not spec:
        sys.exit("Spec not found.")
    for field in [
        "id",
        "title",
        "status",
        "active",
        "area",
        "risk_level",
        "review_cycles",
        "goal",
        "scope",
        "out_of_scope",
        "acceptance_criteria",
        "test_strategy",
        "test_plan",
        "outcome",
        "created_at",
        "updated_at",
        "ready_at",
        "closed_at",
    ]:
        print(f"{field}: {spec[field]}")


def command_spec_revise(args: argparse.Namespace) -> None:
    """Reabre una spec lista por cambios pedidos por el usuario en revision."""
    conn = connect()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    if spec["status"] != "ready_for_review":
        sys.exit("Spec revise requires ready_for_review status.")
    timestamp = now()
    outcome = args.reason or "User requested review changes within approved scope."
    conn.execute(
        """
        UPDATE specs
        SET review_cycles = review_cycles + 1,
            status = 'implementing',
            outcome = ?,
            ready_at = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (outcome, timestamp, spec["id"]),
    )
    conn.commit()
    print(f"Started review cycle {int(spec['review_cycles']) + 1}.")


def command_spec_ready(args: argparse.Namespace) -> None:
    """Marca la spec lista tras la auto-revision semantica de Codex."""
    conn = connect()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    if spec["status"] == "closed":
        sys.exit("Cannot mark a closed spec ready.")
    timestamp = now()
    outcome = args.reason or "Codex verified implementation against approved requirements."
    conn.execute(
        "UPDATE specs SET status = 'ready_for_review', outcome = ?, ready_at = ?, updated_at = ? WHERE id = ?",
        (outcome, timestamp, timestamp, spec["id"]),
    )
    conn.commit()
    print(f"Spec {spec['id']} is ready_for_review.")


def command_spec_block(args: argparse.Namespace) -> None:
    """Bloquea la spec activa cuando hace falta una decision del usuario."""
    conn = connect()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    if spec["status"] == "closed":
        sys.exit("Cannot block a closed spec.")
    timestamp = now()
    conn.execute(
        "UPDATE specs SET status = 'blocked', outcome = ?, updated_at = ? WHERE id = ?",
        (args.reason, timestamp, spec["id"]),
    )
    conn.commit()
    print(f"Blocked spec {spec['id']}.")


def command_removed(args: argparse.Namespace) -> None:
    """Informa de comandos retirados por el flujo de spec unica."""
    sys.exit(
        f"`{args.command_name}` was removed. Define the contract in chat, then use `harness spec create ...`."
    )


def command_context(args: argparse.Namespace) -> None:
    """Muestra spec activa, memoria relevante y orientacion de tests."""
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    if spec:
        print("# Active Spec")
        print(f"- id: {spec['id']}")
        print(f"- title: {spec['title']}")
        print(f"- status: {spec['status']}")
        print(f"- area: {spec['area']}")
        print(f"- review_cycles: {spec['review_cycles']}")
        if spec["goal"]:
            print(f"- goal: {spec['goal']}")
        if spec["acceptance_criteria"]:
            print(f"- acceptance: {spec['acceptance_criteria']}")
        if spec["test_strategy"]:
            print(f"- test_strategy: {spec['test_strategy']}")
        print()

    print("# Relevant Memory")
    limit = int(config.get("memory", {}).get("max_context_results", 8))
    rows = search_memory(conn, args.query, active_only=not args.include_inactive, limit=limit)
    if not rows:
        print("No relevant memory found.")
    else:
        for row in rows:
            print(f"- [{row['kind']}/{row['area']}] {row['summary']} ({row['id']})")
            if row["content"]:
                print(f"  {row['content']}")
            conn.execute("UPDATE memory SET last_used_at = ? WHERE id = ?", (now(), row["id"]))
        conn.commit()

    print()
    print("# Test Guidance")
    pattern = spec["area"] if spec else args.query
    commands = configured_test_commands(config, "targeted", pattern=slugify(pattern))
    if commands:
        for command in commands:
            print(f"- {command}")
    else:
        print("- No targeted test command configured or detected.")


def test_file_matches(path: Path, area: str, content: str) -> bool:
    """Determina si un test parece relacionado con el area de la spec."""
    normalized = slugify(area).replace("-", "_")
    candidates = {
        normalized,
        normalized.replace("_", "-"),
        area.lower(),
        area.lower().replace("_", "-"),
        area.lower().replace("_", ""),
    }
    searchable = f"{path.as_posix().lower()}\n{content.lower()}"
    group_patterns = [
        f"@group {normalized}",
        f"group('{normalized}')",
        f'group("{normalized}")',
        f"pytest.mark.{normalized}",
    ]
    return any(candidate and candidate in searchable for candidate in candidates) or any(
        pattern in searchable for pattern in group_patterns
    )


def discover_relevant_tests(area: str) -> list[str]:
    """Busca tests existentes por ruta, nombre de clase, grupo o marca."""
    tests_dir = ROOT / "tests"
    if not tests_dir.exists():
        return []
    matches: list[str] = []
    for path in tests_dir.rglob("*"):
        if path.name == "__init__.py":
            continue
        if not path.is_file() or path.suffix.lower() not in {".py", ".php", ".js", ".ts", ".tsx", ".jsx"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""
        if test_file_matches(path.relative_to(ROOT), area, content):
            matches.append(path.relative_to(ROOT).as_posix())
    return sorted(set(matches))


def acceptance_items(text: str) -> list[str]:
    """Divide criterios de aceptacion en elementos trazables."""
    lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    parts = re.split(r"\s+\d+\.\s+", f" {text.strip()}")
    return [part.strip() for part in parts if part.strip()]


def build_test_plan(conn: sqlite3.Connection, spec: sqlite3.Row, config: dict[str, Any]) -> dict[str, Any]:
    """Construye un plan operativo de tests a partir de la spec y el repo."""
    area = spec["area"]
    diff = git_diff_summary()
    existing_tests = discover_relevant_tests(area)
    testing_memory = [
        {
            "id": row["id"],
            "summary": row["summary"],
            "content": row["content"],
        }
        for row in search_memory(conn, f"{area} testing tests", limit=5)
        if row["kind"] == "testing"
    ]
    pattern = slugify(area)
    targeted = configured_test_commands(config, "targeted", pattern)
    full = configured_test_commands(config, "full", pattern)
    lint = config.get("commands", {}).get("lint", []) or detect_project().get("lint", [])
    build = config.get("commands", {}).get("build", []) or detect_project().get("build", [])
    requires_test_changes = not existing_tests
    suggested_test = f"tests/test_{pattern.replace('-', '_')}.py"
    coverage_target = existing_tests[0] if existing_tests else suggested_test
    coverage_map = [
        {"criterion": item, "covered_by": coverage_target}
        for item in acceptance_items(spec["acceptance_criteria"])
    ]
    required_commands = targeted or full
    return {
        "area": area,
        "strategy": spec["test_strategy"],
        "existing_tests": existing_tests,
        "testing_memory": testing_memory,
        "tests_to_update": [] if not existing_tests else existing_tests,
        "tests_to_create": [] if existing_tests else [suggested_test],
        "requires_test_changes": requires_test_changes,
        "required_commands": required_commands,
        "targeted_commands": targeted,
        "full_commands": full,
        "lint_commands": lint,
        "build_commands": build,
        "changed_files": diff.get("files", []),
        "coverage_map": coverage_map,
        "generated_at": now(),
    }


def command_test_plan(_: argparse.Namespace) -> None:
    """Genera, guarda e imprime el plan operativo de tests."""
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    plan = build_test_plan(conn, spec, config)
    timestamp = now()
    conn.execute(
        "UPDATE specs SET test_plan = ?, updated_at = ? WHERE id = ?",
        (json.dumps(plan, indent=2), timestamp, spec["id"]),
    )
    conn.execute(
        """
        INSERT INTO runs (
          id, spec_id, phase, command, status, exit_code, duration_sec,
          log_path, summary, created_at
        ) VALUES (?, ?, 'test_plan', ?, 'planned', 0, 0, NULL, ?, ?)
        """,
        (new_id("run"), spec["id"], "harness test plan", "test plan generated", timestamp),
    )
    conn.commit()
    print("# Test Plan")
    print(json.dumps(plan, indent=2))


def load_test_plan(spec: sqlite3.Row) -> dict[str, Any]:
    """Decodifica el plan de tests guardado en la spec activa."""
    if not spec["test_plan"]:
        sys.exit("No test_plan found. Run `harness test plan` before `harness test run`.")
    try:
        return json.loads(spec["test_plan"])
    except json.JSONDecodeError:
        sys.exit("Stored test_plan is invalid JSON. Regenerate it with `harness test plan`.")


def planned_commands(plan: dict[str, Any], full: bool) -> list[str]:
    """Selecciona comandos declarados en el plan segun el modo solicitado."""
    if full:
        return plan.get("full_commands") or plan.get("required_commands") or []
    return plan.get("required_commands") or plan.get("targeted_commands") or []


def command_test_run(args: argparse.Namespace) -> None:
    """Ejecuta comandos declarados por test_plan y registra sus resultados."""
    conn = connect()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    plan = load_test_plan(spec)
    commands = planned_commands(plan, full=args.full)
    if not commands:
        sys.exit("No planned test command found. Update config or regenerate the test plan.")
    if args.dry_run:
        for command in commands:
            print(command)
        return

    failures = 0
    for command in commands:
        run_id = new_id("run")
        log_path = LOG_DIR / f"{run_id}.log"
        started = time.time()
        print(f"Running: {command}")
        proc = subprocess.run(command, cwd=ROOT, shell=True, text=True, capture_output=True)
        duration = round(time.time() - started, 3)
        output = proc.stdout + ("\n" if proc.stdout and proc.stderr else "") + proc.stderr
        log_path.write_text(output, encoding="utf-8", errors="replace")
        status = "passed" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            failures += 1
        conn.execute(
            """
            INSERT INTO runs (
              id, spec_id, phase, command, status, exit_code,
              duration_sec, log_path, summary, created_at
            ) VALUES (?, ?, 'test', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                spec["id"],
                command,
                status,
                proc.returncode,
                duration,
                str(log_path.relative_to(ROOT)),
                f"planned test {status}",
                now(),
            ),
        )
        conn.commit()
        print(f"{status.upper()} exit={proc.returncode} duration={duration}s log={log_path.relative_to(ROOT)}")

    if failures:
        conn.execute(
            "UPDATE specs SET status = 'implementing', updated_at = ? WHERE id = ?",
            (now(), spec["id"]),
        )
        conn.commit()
        sys.exit(1)


def diff_touches_tests(diff: dict[str, Any]) -> bool:
    """Comprueba si el diff actual toca archivos de tests."""
    return any(str(file).replace("\\", "/").startswith("tests/") for file in diff.get("files", []))


def latest_test_runs(conn: sqlite3.Connection, spec_id: str) -> list[sqlite3.Row]:
    """Obtiene las ejecuciones de test recientes de una spec."""
    return conn.execute(
        "SELECT * FROM runs WHERE spec_id = ? AND phase = 'test' ORDER BY created_at DESC",
        (spec_id,),
    ).fetchall()


def command_memory_add(args: argparse.Namespace) -> None:
    """Guarda una memoria aprobada por el usuario."""
    conn = connect()
    timestamp = now()
    mem_id = new_id("mem")
    conn.execute(
        """
        INSERT INTO memory (
          id, scope, area, kind, tags, summary, content, source,
          confidence, active, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            mem_id,
            args.scope,
            args.area,
            args.kind,
            args.tags or "",
            args.summary,
            args.content,
            args.source or "",
            args.confidence,
            yes_no(args.active),
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    print(f"Added memory: {mem_id}")


def command_memory_search(args: argparse.Namespace) -> None:
    """Busca memorias por texto, area, tags o contenido."""
    conn = connect()
    rows = search_memory(conn, args.query, active_only=not args.include_inactive, limit=args.limit)
    if not rows:
        print("No memory found.")
        return
    for row in rows:
        print(f"- id: {row['id']}")
        print(f"  kind: {row['kind']}")
        print(f"  area: {row['area']}")
        print(f"  active: {row['active']}")
        print(f"  status: {row['status']}")
        print(f"  summary: {row['summary']}")
        print(f"  content: {row['content']}")


def command_memory_list(args: argparse.Namespace) -> None:
    """Lista memorias activas o historicas con filtros simples."""
    conn = connect()
    conditions: list[str] = []
    params: list[object] = []
    if not args.all:
        conditions.append("status = 'active' AND active = 1")
    if args.kind:
        conditions.append("kind = ?")
        params.append(args.kind)
    if args.area:
        conditions.append("area = ?")
        params.append(args.area)
    if args.status:
        conditions.append("status = ?")
        params.append(args.status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT id, kind, area, active, status, summary, updated_at FROM memory {where} ORDER BY updated_at DESC",
        params,
    ).fetchall()
    print_rows(rows, ["id", "kind", "area", "active", "status", "summary", "updated_at"])


def command_memory_set_active(args: argparse.Namespace) -> None:
    """Activa o silencia una memoria sin borrarla."""
    conn = connect()
    conn.execute(
        "UPDATE memory SET active = ?, updated_at = ? WHERE id = ?",
        (yes_no(args.active), now(), args.id),
    )
    conn.commit()
    print(f"Updated active={args.active} for memory {args.id}")


def command_memory_deprecate(args: argparse.Namespace) -> None:
    """Marca una memoria como obsoleta para evitar que se use."""
    conn = connect()
    conn.execute(
        "UPDATE memory SET active = 0, status = 'deprecated', updated_at = ? WHERE id = ?",
        (now(), args.id),
    )
    conn.commit()
    print(f"Deprecated memory {args.id}")


def command_close(args: argparse.Namespace) -> None:
    """Cierra la spec activa tras aprobacion del usuario."""
    conn = connect()
    spec = active_spec(conn)
    if not spec:
        sys.exit("No active spec.")
    if spec["status"] != "ready_for_review":
        sys.exit("Spec must be ready_for_review before close. Run tests, verify requirements, and use `harness spec ready` first.")
    timestamp = now()
    conn.execute(
        "UPDATE specs SET status = 'closed', active = 0, outcome = ?, closed_at = ?, updated_at = ? WHERE id = ?",
        (args.outcome or "closed after user approval", timestamp, timestamp, spec["id"]),
    )
    conn.commit()
    print(f"Closed spec {spec['id']}.")
    print("Codex should now stage files and create the git commit outside the harness.")


def yes_no(value: str | bool | None) -> int:
    """Convierte valores humanos yes/no en enteros SQLite."""
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None:
        return 0
    normalized = str(value).lower().strip()
    if normalized in {"yes", "y", "true", "1", "active"}:
        return 1
    if normalized in {"no", "n", "false", "0", "inactive"}:
        return 0
    raise argparse.ArgumentTypeError("Expected yes/no.")


def add_removed_spec_command(spec_sub: argparse._SubParsersAction, name: str) -> None:
    """Registra comandos antiguos con un error explicito."""
    removed = spec_sub.add_parser(name, help="removed command")
    removed.set_defaults(func=command_removed, command_name=f"spec {name}")


def build_parser() -> argparse.ArgumentParser:
    """Construye la interfaz CLI del harness de spec unica."""
    parser = argparse.ArgumentParser(description="Codex workflow harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize harness DB and config in current directory")
    init.add_argument("--force-config", action="store_true")
    init.set_defaults(func=command_init)

    detect = sub.add_parser("detect", help="auto-detect project language and test commands")
    detect.set_defaults(func=command_detect)

    spec = sub.add_parser("spec", help="manage specs")
    spec_sub = spec.add_subparsers(dest="spec_command", required=True)

    spec_create = spec_sub.add_parser("create", help="create an approved spec in implementing state")
    spec_create.add_argument("--title", required=True)
    spec_create.add_argument("--goal", required=True)
    spec_create.add_argument("--scope", required=True)
    spec_create.add_argument("--out-of-scope", dest="out_of_scope", required=True)
    spec_create.add_argument("--acceptance", required=True)
    spec_create.add_argument("--tests", required=True)
    spec_create.add_argument("--area", required=True)
    spec_create.add_argument("--risk", required=True, choices=["low", "medium", "high"])
    spec_create.set_defaults(func=command_spec_create)

    spec_list = spec_sub.add_parser("list", help="list specs")
    spec_list.add_argument("--status", choices=sorted(SPEC_STATUSES))
    spec_list.add_argument("--area")
    spec_list.set_defaults(func=command_spec_list)

    spec_show = spec_sub.add_parser("show", help="show full details of a spec")
    spec_show.add_argument("id", nargs="?")
    spec_show.set_defaults(func=command_spec_show)

    spec_revise = spec_sub.add_parser("revise", help="reopen ready spec for user-requested review changes")
    spec_revise.add_argument("--reason")
    spec_revise.set_defaults(func=command_spec_revise)

    spec_ready = spec_sub.add_parser("ready", help="mark spec ready after Codex verifies requirements")
    spec_ready.add_argument("--reason")
    spec_ready.set_defaults(func=command_spec_ready)

    spec_block = spec_sub.add_parser("block", help="block active spec pending user decision")
    spec_block.add_argument("--reason", required=True)
    spec_block.set_defaults(func=command_spec_block)

    for removed_name in ["new", "set", "lock", "activate", "attempt"]:
        add_removed_spec_command(spec_sub, removed_name)

    task = sub.add_parser("task", help="removed; use spec commands")
    task.add_argument("args", nargs=argparse.REMAINDER)
    task.set_defaults(func=command_removed, command_name="task")

    context = sub.add_parser("context", help="show active spec + relevant memory for a query")
    context.add_argument("query")
    context.add_argument("--include-inactive", action="store_true")
    context.set_defaults(func=command_context)

    test = sub.add_parser("test", help="plan or run tests")
    test_sub = test.add_subparsers(dest="test_command", required=True)

    test_plan = test_sub.add_parser("plan", help="generate and store a post-implementation test plan")
    test_plan.set_defaults(func=command_test_plan)

    test_run = test_sub.add_parser("run", help="execute planned tests and record results")
    mode = test_run.add_mutually_exclusive_group()
    mode.add_argument("--targeted", action="store_true", default=True)
    mode.add_argument("--full", action="store_true")
    test_run.add_argument("--dry-run", action="store_true")
    test_run.set_defaults(func=command_test_run)

    memory = sub.add_parser("memory", help="manage project memory")
    mem_sub = memory.add_subparsers(dest="memory_command", required=True)

    mem_add = mem_sub.add_parser("add", help="add a confirmed memory entry")
    mem_add.add_argument("--kind", required=True)
    mem_add.add_argument("--area", default="general")
    mem_add.add_argument("--summary", required=True)
    mem_add.add_argument("--content", required=True)
    mem_add.add_argument("--tags")
    mem_add.add_argument("--source")
    mem_add.add_argument("--scope", default="project")
    mem_add.add_argument("--confidence", type=float, default=0.8)
    mem_add.add_argument("--active", choices=["yes", "no"], default="yes")
    mem_add.set_defaults(func=command_memory_add)

    mem_search = mem_sub.add_parser("search", help="full-text search memory")
    mem_search.add_argument("query")
    mem_search.add_argument("--include-inactive", action="store_true")
    mem_search.add_argument("--limit", type=int, default=8)
    mem_search.set_defaults(func=command_memory_search)

    mem_list = mem_sub.add_parser("list", help="list memory entries")
    mem_list.add_argument("--all", action="store_true", help="include inactive/deprecated entries")
    mem_list.add_argument("--kind")
    mem_list.add_argument("--area")
    mem_list.add_argument("--status")
    mem_list.set_defaults(func=command_memory_list)

    mem_active = mem_sub.add_parser("set-active", help="enable or disable a memory entry")
    mem_active.add_argument("id")
    mem_active.add_argument("active", choices=["yes", "no"])
    mem_active.set_defaults(func=command_memory_set_active)

    mem_deprecate = mem_sub.add_parser("deprecate", help="mark a memory entry as deprecated")
    mem_deprecate.add_argument("id")
    mem_deprecate.set_defaults(func=command_memory_deprecate)

    close = sub.add_parser("close", help="close active spec after user approval")
    close.add_argument("--outcome")
    close.set_defaults(func=command_close)

    return parser


def main() -> None:
    """Ejecuta el comando solicitado por CLI."""
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
