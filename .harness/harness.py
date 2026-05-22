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
ARTIFACTS_DIR = HARNESS_DIR / "artifacts"


DEFAULT_CONFIG: dict[str, Any] = {
    "project": {"name": ROOT.name, "language": "auto"},
    "workflow": {
        "max_attempts": 2,
        "require_locked_spec": True,
        "require_tests_before_ready": True,
        "allow_spec_change_during_implementation": False,
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


# Each entry: (target_version: int, sql: str)
# Only ADD new entries at the end. Never modify existing ones.
# Use ALTER TABLE ADD COLUMN for additive changes.
# For destructive changes: new table → copy → drop → rename.
MIGRATIONS: list[tuple[int, str]] = [
    # (1, "ALTER TABLE specs ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"),
]

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS specs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  out_of_scope TEXT NOT NULL DEFAULT '',
  acceptance_criteria TEXT NOT NULL DEFAULT '',
  test_strategy TEXT NOT NULL DEFAULT '',
  area TEXT NOT NULL DEFAULT 'general',
  risk_level TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'draft',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  locked_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  spec_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  active INTEGER NOT NULL DEFAULT 1,
  attempts INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(spec_id) REFERENCES specs(id)
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  spec_id TEXT,
  task_id TEXT,
  phase TEXT NOT NULL,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  duration_sec REAL,
  log_path TEXT,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(spec_id) REFERENCES specs(id),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
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

CREATE TABLE IF NOT EXISTS memory_candidates (
  id TEXT PRIMARY KEY,
  spec_id TEXT,
  area TEXT NOT NULL DEFAULT 'general',
  kind TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.8,
  active_default INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL,
  decided_at TEXT,
  FOREIGN KEY(spec_id) REFERENCES specs(id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:64] or "item"


def ensure_dirs() -> None:
    HARNESS_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    ARTIFACTS_DIR.mkdir(exist_ok=True)


def apply_migrations(conn: sqlite3.Connection) -> None:
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
    if not pending:
        return
    for version, sql in pending:
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    final = pending[-1][0]
    print(f"Applied {len(pending)} migration(s): schema v{current} → v{final}", file=sys.stderr)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    apply_migrations(conn)
    return conn


def load_config() -> dict[str, Any]:
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


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def write_default_config(force: bool = False) -> None:
    ensure_dirs()
    if CONFIG_PATH.exists() and not force:
        return
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")


def print_rows(rows: list[sqlite3.Row], fields: list[str]) -> None:
    if not rows:
        print("No rows.")
        return
    for row in rows:
        print("- " + " | ".join(f"{field}: {row[field]}" for field in fields))


def active_spec(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM specs WHERE active = 1 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()


def active_task(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tasks WHERE active = 1 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()


def detect_project() -> dict[str, Any]:
    files = {p.name for p in ROOT.iterdir() if p.is_file()}
    result: dict[str, Any] = {"language": "unknown", "test": {"targeted": [], "full": []}, "lint": [], "build": []}

    if "package.json" in files:
        result["language"] = "node"
        try:
            package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        except Exception:
            scripts = {}
        if "test" in scripts:
            result["test"]["full"] = ["npm test"]
            result["test"]["targeted"] = ["npm test -- {pattern}"]
        if "lint" in scripts:
            result["lint"] = ["npm run lint"]
        if "build" in scripts:
            result["build"] = ["npm run build"]
    elif "pyproject.toml" in files or "pytest.ini" in files or "requirements.txt" in files:
        result["language"] = "python"
        result["test"]["full"] = ["pytest"]
        result["test"]["targeted"] = ['pytest -k "{pattern}"']
        result["lint"] = ["ruff check ."]
    elif "composer.json" in files:
        result["language"] = "php"
        if (ROOT / "artisan").exists():
            result["test"]["full"] = ["php artisan test"]
            result["test"]["targeted"] = ["php artisan test --filter={pattern}"]
        else:
            result["test"]["full"] = ["vendor/bin/phpunit"]
            result["test"]["targeted"] = ["vendor/bin/phpunit --filter {pattern}"]
    elif "go.mod" in files:
        result["language"] = "go"
        result["test"]["full"] = ["go test ./..."]
        result["test"]["targeted"] = ["go test ./... -run {pattern}"]
    elif "Cargo.toml" in files:
        result["language"] = "rust"
        result["test"]["full"] = ["cargo test"]
        result["test"]["targeted"] = ["cargo test {pattern}"]
    elif any(p.suffix == ".sln" or p.suffix == ".csproj" for p in ROOT.iterdir() if p.is_file()):
        result["language"] = "dotnet"
        result["test"]["full"] = ["dotnet test"]
        result["test"]["targeted"] = ['dotnet test --filter "{pattern}"']
    elif "pom.xml" in files:
        result["language"] = "java-maven"
        result["test"]["full"] = ["mvn test"]
        result["test"]["targeted"] = ["mvn -Dtest={pattern} test"]
    elif "build.gradle" in files or "build.gradle.kts" in files:
        result["language"] = "java-gradle"
        result["test"]["full"] = ["gradle test"]
        result["test"]["targeted"] = ["gradle test --tests {pattern}"]

    return result


def configured_test_commands(config: dict[str, Any], mode: str, pattern: str) -> list[str]:
    commands = config.get("commands", {}).get("test", {}).get(mode, []) or []
    if not commands:
        detected = detect_project()
        commands = detected.get("test", {}).get(mode, []) or []
        if mode == "targeted" and not commands:
            commands = detected.get("test", {}).get("full", []) or []
    return [cmd.replace("{pattern}", pattern) for cmd in commands]


def git_diff_summary() -> dict[str, Any]:
    if not (ROOT / ".git").exists():
        return {"available": False, "files": [], "lines": None}
    try:
        files = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
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
    terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
    if not terms:
        return ""
    return " OR ".join(f"{term}*" for term in terms[:8])


def command_init(args: argparse.Namespace) -> None:
    ensure_dirs()
    write_default_config(force=args.force_config)
    with connect():
        pass
    print(f"Initialized harness at {HARNESS_DIR}")
    print(f"Database: {DB_PATH}")


def command_detect(_: argparse.Namespace) -> None:
    print(json.dumps(detect_project(), indent=2))



def command_spec_new(args: argparse.Namespace) -> None:
    conn = connect()
    timestamp = now()
    spec_id = f"spec_{slugify(args.title)}_{uuid.uuid4().hex[:6]}"
    task_id = f"task_{slugify(args.title)}_{uuid.uuid4().hex[:6]}"
    conn.execute("UPDATE specs SET active = 0 WHERE active = 1")
    conn.execute("UPDATE tasks SET active = 0 WHERE active = 1")
    conn.execute(
        """
        INSERT INTO specs (
          id, title, goal, scope, out_of_scope, acceptance_criteria,
          test_strategy, area, risk_level, status, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
        """,
        (
            spec_id,
            args.title,
            args.goal or "",
            args.scope or "",
            args.out_of_scope or "",
            args.acceptance or "",
            args.tests or "",
            args.area,
            args.risk,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        "INSERT INTO tasks (id, spec_id, status, active, created_at, updated_at) VALUES (?, ?, 'draft', 1, ?, ?)",
        (task_id, spec_id, timestamp, timestamp),
    )
    conn.commit()
    print(f"Created draft spec: {spec_id}")
    print(f"Created active task: {task_id}")
    print("Refine goal/scope/acceptance/test strategy, then lock only after user approval.")


def command_spec_list(_: argparse.Namespace) -> None:
    conn = connect()
    rows = conn.execute(
        "SELECT id, title, area, status, active, updated_at FROM specs ORDER BY updated_at DESC"
    ).fetchall()
    print_rows(rows, ["id", "title", "area", "status", "active", "updated_at"])


def command_spec_show(args: argparse.Namespace) -> None:
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
        "goal",
        "scope",
        "out_of_scope",
        "acceptance_criteria",
        "test_strategy",
        "created_at",
        "updated_at",
        "locked_at",
    ]:
        print(f"{field}: {spec[field]}")


def command_spec_set(args: argparse.Namespace) -> None:
    conn = connect()
    spec = get_spec(conn, args.id)
    if not spec:
        sys.exit("Spec not found.")
    if spec["status"] == "locked" and not args.force:
        sys.exit("Spec is locked. Use --force only with explicit user approval.")
    allowed = {
        "title",
        "goal",
        "scope",
        "out_of_scope",
        "acceptance_criteria",
        "test_strategy",
        "area",
        "risk_level",
    }
    updates = {key: value for key, value in vars(args).items() if key in allowed and value is not None}
    if not updates:
        sys.exit("No updates provided.")
    updates["updated_at"] = now()
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(f"UPDATE specs SET {set_clause} WHERE id = ?", [*updates.values(), spec["id"]])
    conn.commit()
    print(f"Updated spec: {spec['id']}")


def command_spec_lock(args: argparse.Namespace) -> None:
    conn = connect()
    spec = get_spec(conn, args.id)
    if not spec:
        sys.exit("Spec not found.")
    missing = [
        name
        for name in ["goal", "scope", "acceptance_criteria", "test_strategy"]
        if not (spec[name] or "").strip()
    ]
    if missing and not args.force:
        sys.exit(f"Spec is missing required fields: {', '.join(missing)}. Use --force only with user approval.")
    timestamp = now()
    conn.execute(
        "UPDATE specs SET status = 'locked', locked_at = ?, updated_at = ? WHERE id = ?",
        (timestamp, timestamp, spec["id"]),
    )
    conn.execute(
        "UPDATE tasks SET status = 'locked', updated_at = ? WHERE spec_id = ?",
        (timestamp, spec["id"]),
    )
    conn.commit()
    print(f"Locked spec: {spec['id']}")


def get_spec(conn: sqlite3.Connection, spec_id: str | None) -> sqlite3.Row | None:
    if spec_id:
        return conn.execute("SELECT * FROM specs WHERE id = ?", (spec_id,)).fetchone()
    return active_spec(conn)


def command_task_active(_: argparse.Namespace) -> None:
    conn = connect()
    task = active_task(conn)
    if not task:
        print("No active task.")
        return
    spec = conn.execute("SELECT title, status FROM specs WHERE id = ?", (task["spec_id"],)).fetchone()
    print(f"id: {task['id']}")
    print(f"spec_id: {task['spec_id']}")
    print(f"spec_title: {spec['title'] if spec else ''}")
    print(f"spec_status: {spec['status'] if spec else ''}")
    print(f"status: {task['status']}")
    print(f"attempts: {task['attempts']}")


def command_task_attempt(_: argparse.Namespace) -> None:
    conn = connect()
    task = active_task(conn)
    if not task:
        sys.exit("No active task.")
    config = load_config()
    max_attempts = int(config.get("workflow", {}).get("max_attempts", 2))
    if task["attempts"] >= max_attempts:
        sys.exit(f"Max attempts reached ({max_attempts}). Ask the user how to proceed.")
    timestamp = now()
    conn.execute(
        "UPDATE tasks SET attempts = attempts + 1, status = 'implementing', updated_at = ? WHERE id = ?",
        (timestamp, task["id"]),
    )
    conn.commit()
    print(f"Started implementation attempt {task['attempts'] + 1}/{max_attempts}.")


def command_task_list(_: argparse.Namespace) -> None:
    conn = connect()
    rows = conn.execute(
        "SELECT id, spec_id, status, active, attempts, updated_at FROM tasks ORDER BY updated_at DESC"
    ).fetchall()
    print_rows(rows, ["id", "spec_id", "status", "active", "attempts", "updated_at"])


def command_context(args: argparse.Namespace) -> None:
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    if spec:
        print("# Active Spec")
        print(f"- id: {spec['id']}")
        print(f"- title: {spec['title']}")
        print(f"- status: {spec['status']}")
        print(f"- area: {spec['area']}")
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
    pattern = spec["area"] if spec and spec["area"] != "general" else args.query
    commands = configured_test_commands(config, "targeted", pattern=slugify(pattern))
    if commands:
        for command in commands:
            print(f"- {command}")
    else:
        print("- No targeted test command configured or detected.")


def command_test_plan(args: argparse.Namespace) -> None:
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    pattern = args.pattern or (spec["area"] if spec else "relevant")
    targeted = configured_test_commands(config, "targeted", pattern)
    full = configured_test_commands(config, "full", pattern)
    lint = config.get("commands", {}).get("lint", []) or detect_project().get("lint", [])
    build = config.get("commands", {}).get("build", []) or detect_project().get("build", [])

    print("# Test Plan")
    if spec:
        print(f"- spec: {spec['id']} ({spec['title']})")
        print(f"- required strategy: {spec['test_strategy'] or 'not specified'}")
    print("- rule: behavior changes require relevant tests; add tests if missing.")
    print()
    print("## Targeted")
    print_commands(targeted)
    print("## Full")
    print_commands(full)
    print("## Lint")
    print_commands(lint)
    print("## Build")
    print_commands(build)


def print_commands(commands: list[str]) -> None:
    if not commands:
        print("- not configured/detected")
        return
    for command in commands:
        print(f"- {command}")


def command_test_run(args: argparse.Namespace) -> None:
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    task = active_task(conn)
    pattern = args.pattern or (spec["area"] if spec else "relevant")
    mode = "full" if args.full else "targeted"
    commands = configured_test_commands(config, mode, pattern)
    if not commands:
        sys.exit(f"No {mode} test command configured or detected.")
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
              id, spec_id, task_id, phase, command, status, exit_code,
              duration_sec, log_path, summary, created_at
            ) VALUES (?, ?, ?, 'test', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                spec["id"] if spec else None,
                task["id"] if task else None,
                command,
                status,
                proc.returncode,
                duration,
                str(log_path.relative_to(ROOT)),
                f"{mode} test {status}",
                now(),
            ),
        )
        conn.commit()
        print(f"{status.upper()} exit={proc.returncode} duration={duration}s log={log_path.relative_to(ROOT)}")

    if failures:
        sys.exit(1)


def command_review(_: argparse.Namespace) -> None:
    conn = connect()
    config = load_config()
    spec = active_spec(conn)
    task = active_task(conn)
    if not spec or not task:
        sys.exit("No active spec/task.")

    max_attempts = int(config.get("workflow", {}).get("max_attempts", 2))
    runs = conn.execute(
        "SELECT * FROM runs WHERE spec_id = ? ORDER BY created_at DESC LIMIT 10",
        (spec["id"],),
    ).fetchall()
    test_runs = [run for run in runs if run["phase"] == "test"]
    latest_test = test_runs[0] if test_runs else None
    diff = git_diff_summary()

    findings: list[str] = []
    status = "ready_for_user_review"

    if spec["status"] != "locked":
        findings.append("Spec is not locked.")
        status = "spec_needs_revision"
    if not test_runs:
        findings.append("No test run recorded. Behavior changes require relevant tests.")
        status = "needs_fix"
    elif latest_test and latest_test["status"] != "passed":
        findings.append(f"Latest test run failed: {latest_test['command']}")
        status = "needs_fix"
    if task["attempts"] >= max_attempts and status != "ready_for_user_review":
        status = "blocked_needs_user"
        findings.append(f"Max implementation attempts reached ({task['attempts']}/{max_attempts}).")

    max_diff = int(config.get("review", {}).get("max_diff_lines_warn", 800))
    if diff["available"] and diff["lines"] is not None and diff["lines"] > max_diff:
        findings.append(f"Large diff warning: {diff['lines']} changed lines exceeds {max_diff}.")

    print("# Harness Review")
    print(f"status: {status}")
    print(f"spec: {spec['id']} ({spec['title']})")
    print(f"spec_status: {spec['status']}")
    print(f"task: {task['id']}")
    print(f"attempts: {task['attempts']}/{max_attempts}")
    print()
    print("## Tests")
    if not test_runs:
        print("- no test runs recorded")
    else:
        for run in test_runs[:5]:
            print(f"- {run['status']}: {run['command']} ({run['log_path']})")
    print()
    print("## Diff")
    if diff["available"]:
        print(f"- changed_files: {len(diff['files'])}")
        print(f"- changed_lines: {diff['lines']}")
        for file in diff["files"][:20]:
            print(f"  - {file}")
    else:
        print("- git diff unavailable; project is not a git repository or git failed")
    print()
    print("## Findings")
    if findings:
        for finding in findings:
            print(f"- {finding}")
    else:
        print("- Contract checks passed. Ready for user review.")


def command_memory_add(args: argparse.Namespace) -> None:
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


def search_memory(
    conn: sqlite3.Connection, query: str, active_only: bool = True, limit: int = 8
) -> list[sqlite3.Row]:
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


def command_memory_list(args: argparse.Namespace) -> None:
    conn = connect()
    clause = "" if args.all else "WHERE status = 'active' AND active = 1"
    rows = conn.execute(
        f"SELECT id, kind, area, active, status, summary, updated_at FROM memory {clause} ORDER BY updated_at DESC"
    ).fetchall()
    print_rows(rows, ["id", "kind", "area", "active", "status", "summary", "updated_at"])


def command_memory_set_active(args: argparse.Namespace) -> None:
    conn = connect()
    conn.execute(
        "UPDATE memory SET active = ?, updated_at = ? WHERE id = ?",
        (yes_no(args.active), now(), args.id),
    )
    conn.commit()
    print(f"Updated active={args.active} for memory {args.id}")


def command_memory_deprecate(args: argparse.Namespace) -> None:
    conn = connect()
    conn.execute(
        "UPDATE memory SET active = 0, status = 'deprecated', updated_at = ? WHERE id = ?",
        (now(), args.id),
    )
    conn.commit()
    print(f"Deprecated memory {args.id}")


def command_memory_propose(args: argparse.Namespace) -> None:
    conn = connect()
    spec = active_spec(conn)
    candidate_id = new_id("cand")
    conn.execute(
        """
        INSERT INTO memory_candidates (
          id, spec_id, area, kind, tags, summary, content, source,
          confidence, active_default, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
        """,
        (
            candidate_id,
            spec["id"] if spec else None,
            args.area,
            args.kind,
            args.tags or "",
            args.summary,
            args.content,
            args.source or (spec["id"] if spec else ""),
            args.confidence,
            yes_no(args.active),
            now(),
        ),
    )
    conn.commit()
    print(f"Proposed memory candidate: {candidate_id}")


def command_memory_candidates(args: argparse.Namespace) -> None:
    conn = connect()
    clause = "" if args.all else "WHERE status = 'proposed'"
    rows = conn.execute(
        f"SELECT * FROM memory_candidates {clause} ORDER BY created_at DESC"
    ).fetchall()
    if not rows:
        print("No memory candidates.")
        return
    for row in rows:
        print(f"- id: {row['id']}")
        print(f"  status: {row['status']}")
        print(f"  kind: {row['kind']}")
        print(f"  area: {row['area']}")
        print(f"  active_default: {row['active_default']}")
        print(f"  summary: {row['summary']}")
        print(f"  content: {row['content']}")


def command_memory_accept(args: argparse.Namespace) -> None:
    conn = connect()
    cand = conn.execute("SELECT * FROM memory_candidates WHERE id = ?", (args.id,)).fetchone()
    if not cand:
        sys.exit("Candidate not found.")
    if cand["status"] != "proposed":
        sys.exit(f"Candidate is already {cand['status']}.")
    timestamp = now()
    mem_id = new_id("mem")
    active = cand["active_default"] if args.active is None else yes_no(args.active)
    conn.execute(
        """
        INSERT INTO memory (
          id, scope, area, kind, tags, summary, content, source,
          confidence, active, status, created_at, updated_at
        ) VALUES (?, 'project', ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            mem_id,
            cand["area"],
            cand["kind"],
            cand["tags"],
            cand["summary"],
            cand["content"],
            cand["source"],
            cand["confidence"],
            active,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        "UPDATE memory_candidates SET status = 'accepted', decided_at = ? WHERE id = ?",
        (timestamp, cand["id"]),
    )
    conn.commit()
    print(f"Accepted candidate {cand['id']} as memory {mem_id} active={active}")


def command_memory_reject(args: argparse.Namespace) -> None:
    conn = connect()
    conn.execute(
        "UPDATE memory_candidates SET status = 'rejected', decided_at = ? WHERE id = ?",
        (now(), args.id),
    )
    conn.commit()
    print(f"Rejected candidate {args.id}")


def command_close(args: argparse.Namespace) -> None:
    conn = connect()
    spec = active_spec(conn)
    task = active_task(conn)
    if not spec or not task:
        sys.exit("No active spec/task.")
    if not args.force and spec["status"] != "locked":
        sys.exit("Spec is not locked. Use --force only if the user explicitly approved closing.")
    timestamp = now()
    conn.execute(
        "UPDATE specs SET status = 'closed', active = 0, updated_at = ? WHERE id = ?",
        (timestamp, spec["id"]),
    )
    conn.execute(
        "UPDATE tasks SET status = 'closed', active = 0, outcome = ?, updated_at = ? WHERE id = ?",
        (args.outcome or "closed after user review", timestamp, task["id"]),
    )
    propose_close_memory(conn, spec)
    conn.commit()
    print(f"Closed task {task['id']} and spec {spec['id']}.")
    print("Memory candidates were proposed. Review them with:")
    print("python .harness/harness.py memory candidates")


def propose_close_memory(conn: sqlite3.Connection, spec: sqlite3.Row) -> None:
    existing = conn.execute(
        "SELECT COUNT(*) AS count FROM memory_candidates WHERE spec_id = ?",
        (spec["id"],),
    ).fetchone()["count"]
    if existing:
        return
    timestamp = now()
    candidates = []
    if spec["test_strategy"].strip():
        candidates.append(
            (
                new_id("cand"),
                spec["id"],
                spec["area"],
                "testing",
                f"{spec['area']}, tests",
                f"Test strategy for {spec['title']}",
                spec["test_strategy"],
                spec["id"],
                0.7,
                1,
                "proposed",
                timestamp,
            )
        )
    if spec["acceptance_criteria"].strip():
        candidates.append(
            (
                new_id("cand"),
                spec["id"],
                spec["area"],
                "decision",
                f"{spec['area']}, acceptance",
                f"Accepted contract for {spec['title']}",
                spec["acceptance_criteria"],
                spec["id"],
                0.6,
                0,
                "proposed",
                timestamp,
            )
        )
    conn.executemany(
        """
        INSERT INTO memory_candidates (
          id, spec_id, area, kind, tags, summary, content, source,
          confidence, active_default, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        candidates,
    )


def yes_no(value: str | bool | None) -> int:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex workflow harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize harness DB and config in current directory")
    init.add_argument("--force-config", action="store_true")
    init.set_defaults(func=command_init)

    detect = sub.add_parser("detect", help="auto-detect project language and test commands")
    detect.set_defaults(func=command_detect)

    spec = sub.add_parser("spec", help="manage specs (requirements)")
    spec_sub = spec.add_subparsers(dest="spec_command", required=True)

    spec_new = spec_sub.add_parser("new", help="create a new draft spec")
    spec_new.add_argument("title")
    spec_new.add_argument("--goal")
    spec_new.add_argument("--scope")
    spec_new.add_argument("--out-of-scope")
    spec_new.add_argument("--acceptance")
    spec_new.add_argument("--tests")
    spec_new.add_argument("--area", default="general")
    spec_new.add_argument("--risk", default="medium")
    spec_new.set_defaults(func=command_spec_new)

    spec_list = spec_sub.add_parser("list", help="list all specs")
    spec_list.set_defaults(func=command_spec_list)

    spec_show = spec_sub.add_parser("show", help="show full details of a spec (defaults to active)")
    spec_show.add_argument("id", nargs="?")
    spec_show.set_defaults(func=command_spec_show)

    spec_set = spec_sub.add_parser("set", help="update fields on a spec")
    spec_set.add_argument("id", nargs="?")
    spec_set.add_argument("--title")
    spec_set.add_argument("--goal")
    spec_set.add_argument("--scope")
    spec_set.add_argument("--out-of-scope", dest="out_of_scope")
    spec_set.add_argument("--acceptance", dest="acceptance_criteria")
    spec_set.add_argument("--tests", dest="test_strategy")
    spec_set.add_argument("--area")
    spec_set.add_argument("--risk", dest="risk_level")
    spec_set.add_argument("--force", action="store_true")
    spec_set.set_defaults(func=command_spec_set)

    spec_lock = spec_sub.add_parser("lock", help="lock a spec (freezes requirements)")
    spec_lock.add_argument("id", nargs="?")
    spec_lock.add_argument("--force", action="store_true")
    spec_lock.set_defaults(func=command_spec_lock)

    task = sub.add_parser("task", help="manage implementation tasks")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    task_active = task_sub.add_parser("active", help="show the current active task")
    task_active.set_defaults(func=command_task_active)

    task_attempt = task_sub.add_parser("attempt", help="start a new implementation attempt")
    task_attempt.set_defaults(func=command_task_attempt)

    task_list = task_sub.add_parser("list", help="list all tasks")
    task_list.set_defaults(func=command_task_list)

    context = sub.add_parser("context", help="show active spec + relevant memory for a query")
    context.add_argument("query")
    context.add_argument("--include-inactive", action="store_true")
    context.set_defaults(func=command_context)

    test = sub.add_parser("test", help="plan or run tests")
    test_sub = test.add_subparsers(dest="test_command", required=True)

    test_plan = test_sub.add_parser("plan", help="show test commands for current spec")
    test_plan.add_argument("--pattern")
    test_plan.set_defaults(func=command_test_plan)

    test_run = test_sub.add_parser("run", help="execute tests and record results")
    mode = test_run.add_mutually_exclusive_group()
    mode.add_argument("--targeted", action="store_true", default=True)
    mode.add_argument("--full", action="store_true")
    test_run.add_argument("--pattern")
    test_run.add_argument("--dry-run", action="store_true")
    test_run.set_defaults(func=command_test_run)

    review = sub.add_parser("review", help="run pre-merge checks (spec locked, tests passed, diff size)")
    review.set_defaults(func=command_review)

    memory = sub.add_parser("memory", help="manage project memory (decisions, conventions, pitfalls)")
    mem_sub = memory.add_subparsers(dest="memory_command", required=True)

    mem_add = mem_sub.add_parser("add", help="add a memory entry directly")
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

    mem_list = mem_sub.add_parser("list", help="list all memory entries")
    mem_list.add_argument("--all", action="store_true")
    mem_list.set_defaults(func=command_memory_list)

    mem_active = mem_sub.add_parser("set-active", help="enable or disable a memory entry")
    mem_active.add_argument("id")
    mem_active.add_argument("active", choices=["yes", "no"])
    mem_active.set_defaults(func=command_memory_set_active)

    mem_deprecate = mem_sub.add_parser("deprecate", help="mark a memory entry as deprecated")
    mem_deprecate.add_argument("id")
    mem_deprecate.set_defaults(func=command_memory_deprecate)

    mem_propose = mem_sub.add_parser("propose", help="propose a memory candidate for later review")
    mem_propose.add_argument("--kind", required=True)
    mem_propose.add_argument("--area", default="general")
    mem_propose.add_argument("--summary", required=True)
    mem_propose.add_argument("--content", required=True)
    mem_propose.add_argument("--tags")
    mem_propose.add_argument("--source")
    mem_propose.add_argument("--confidence", type=float, default=0.8)
    mem_propose.add_argument("--active", choices=["yes", "no"], default="yes")
    mem_propose.set_defaults(func=command_memory_propose)

    mem_candidates = mem_sub.add_parser("candidates", help="list pending memory candidates")
    mem_candidates.add_argument("--all", action="store_true")
    mem_candidates.set_defaults(func=command_memory_candidates)

    mem_accept = mem_sub.add_parser("accept", help="accept a memory candidate into active memory")
    mem_accept.add_argument("id")
    mem_accept.add_argument("--active", choices=["yes", "no"])
    mem_accept.set_defaults(func=command_memory_accept)

    mem_reject = mem_sub.add_parser("reject", help="reject a memory candidate")
    mem_reject.add_argument("id")
    mem_reject.set_defaults(func=command_memory_reject)

    close = sub.add_parser("close", help="close the active spec/task after user review")
    close.add_argument("--outcome")
    close.add_argument("--force", action="store_true")
    close.set_defaults(func=command_close)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
