from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class HarnessCliTest(unittest.TestCase):
    """Valida el flujo CLI del harness instalado en proyectos temporales."""

    def make_project(self) -> Path:
        """Crea un proyecto temporal con la plantilla actual del harness."""
        root = Path(tempfile.mkdtemp(prefix="harness-test-"))
        harness_dir = root / ".harness"
        harness_dir.mkdir()
        shutil.copyfile(SOURCE_ROOT / ".harness" / "harness.py", harness_dir / "harness.py")
        shutil.copyfile(SOURCE_ROOT / ".harness" / "config.yaml", harness_dir / "config.yaml")
        return root

    def run_harness(self, root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Ejecuta el harness dentro de un proyecto temporal."""
        proc = subprocess.run(
            [sys.executable, ".harness/harness.py", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            self.fail(f"Command failed: {args}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return proc

    def create_spec(self, root: Path, area: str = "harness") -> None:
        """Crea una spec completa para las pruebas de flujo."""
        self.run_harness(
            root,
            "spec",
            "create",
            "--title",
            "Refactor harness",
            "--area",
            area,
            "--goal",
            "Validar el nuevo flujo de spec unica.",
            "--scope",
            "Actualizar harness y tests.",
            "--out-of-scope",
            "No anadir dependencias externas.",
            "--acceptance",
            "1. La spec se crea en implementing. 2. Los tests planificados pasan.",
            "--tests",
            "Ejecutar unittest discover.",
            "--risk",
            "medium",
        )

    def db(self, root: Path) -> sqlite3.Connection:
        """Abre la base SQLite del proyecto temporal."""
        conn = sqlite3.connect(root / ".harness" / "harness.db")
        conn.row_factory = sqlite3.Row
        return conn

    def test_spec_create_requires_complete_contract(self) -> None:
        """Comprueba que una spec incompleta se rechaza y una completa arranca en implementing."""
        root = self.make_project()
        failed = self.run_harness(root, "spec", "create", "--title", "Incomplete", check=False)
        self.assertNotEqual(failed.returncode, 0)

        self.create_spec(root)
        spec = self.db(root).execute("SELECT * FROM specs").fetchone()
        self.assertEqual(spec["status"], "implementing")
        self.assertEqual(spec["review_cycles"], 0)

    def test_spec_attempt_is_removed(self) -> None:
        """Comprueba que el comando attempt ya no forma parte del flujo."""
        root = self.make_project()
        self.create_spec(root)
        removed = self.run_harness(root, "spec", "attempt", check=False)
        self.assertNotEqual(removed.returncode, 0)
        self.assertIn("removed", removed.stderr)

    def test_spec_revise_counts_review_cycles(self) -> None:
        """Comprueba que los cambios pedidos por usuario cuentan ciclos de revision."""
        root = self.make_project()
        self.create_spec(root)
        conn = self.db(root)
        conn.execute("UPDATE specs SET status = 'ready_for_review', ready_at = '2026-01-01'")
        conn.commit()

        self.run_harness(root, "spec", "revise", "--reason", "Ajuste pedido por usuario.")
        spec = self.db(root).execute(
            "SELECT status, review_cycles, ready_at, outcome FROM specs"
        ).fetchone()
        self.assertEqual(spec["status"], "implementing")
        self.assertEqual(spec["review_cycles"], 1)
        self.assertIsNone(spec["ready_at"])
        self.assertEqual(spec["outcome"], "Ajuste pedido por usuario.")

    def test_test_plan_and_run_use_spec_only(self) -> None:
        """Asegura que test_plan se guarda y los runs quedan ligados solo a spec_id."""
        root = self.make_project()
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_harness.py").write_text(
            "import unittest\n\n"
            "class TestHarnessArea(unittest.TestCase):\n"
            "    def test_harness(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.create_spec(root)
        self.run_harness(root, "test", "plan")
        self.run_harness(root, "test", "run", "--targeted")

        conn = self.db(root)
        spec = conn.execute("SELECT test_plan FROM specs").fetchone()
        plan = json.loads(spec["test_plan"])
        self.assertIn("tests/test_harness.py", plan["existing_tests"])
        self.assertIn("changed_files", plan)
        run = conn.execute("SELECT * FROM runs WHERE phase = 'test'").fetchone()
        self.assertIsNotNone(run["spec_id"])
        self.assertNotIn("task_id", [row[1] for row in conn.execute("PRAGMA table_info(runs)")])

    def test_ready_and_close_flow(self) -> None:
        """Valida que spec ready pasa a ready_for_review y close cierra sin hash."""
        root = self.make_project()
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_harness.py").write_text(
            "import unittest\n\n"
            "class TestHarness(unittest.TestCase):\n"
            "    def test_flow(self):\n"
            "        self.assertEqual(1, 1)\n",
            encoding="utf-8",
        )
        self.create_spec(root)
        self.run_harness(root, "test", "plan")
        self.run_harness(root, "test", "run", "--targeted")
        self.run_harness(root, "spec", "ready", "--reason", "Codex verified requirements.")

        conn = self.db(root)
        spec = conn.execute("SELECT status FROM specs").fetchone()
        self.assertEqual(spec["status"], "ready_for_review")

        self.run_harness(root, "close")
        spec = self.db(root).execute("SELECT status, active FROM specs").fetchone()
        self.assertEqual(spec["status"], "closed")
        self.assertEqual(spec["active"], 0)
        self.assertNotIn("commit_hash", [row[1] for row in self.db(root).execute("PRAGMA table_info(specs)")])

    def test_migrates_v1_database_to_spec_only_schema(self) -> None:
        """Comprueba que la migracion elimina tasks, candidates, task_id y commit_hash."""
        root = self.make_project()
        db_path = root / ".harness" / "harness.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE specs (
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
              locked_at TEXT,
              commit_hash TEXT
            );
            CREATE TABLE tasks (
              id TEXT PRIMARY KEY,
              spec_id TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'draft',
              active INTEGER NOT NULL DEFAULT 1,
              attempts INTEGER NOT NULL DEFAULT 0,
              outcome TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              commit_hash TEXT
            );
            CREATE TABLE runs (
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
              created_at TEXT NOT NULL
            );
            CREATE TABLE memory_candidates (
              id TEXT PRIMARY KEY,
              spec_id TEXT,
              area TEXT NOT NULL DEFAULT 'general',
              kind TEXT NOT NULL,
              summary TEXT NOT NULL,
              content TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute(
            "INSERT INTO specs (id, title, goal, scope, acceptance_criteria, test_strategy, area, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("spec_old", "Old", "goal", "scope", "acceptance", "tests", "harness", "locked", "2026-01-01", "2026-01-02"),
        )
        conn.execute(
            "INSERT INTO tasks (id, spec_id, status, attempts, outcome, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("task_old", "spec_old", "implementing", 2, "old outcome", "2026-01-01", "2026-01-02"),
        )
        conn.execute(
            "INSERT INTO runs (id, spec_id, task_id, phase, command, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run_old", "spec_old", "task_old", "test", "echo ok", "passed", "2026-01-02"),
        )
        conn.commit()
        conn.close()

        self.run_harness(root, "init")
        conn = self.db(root)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("tasks", tables)
        self.assertNotIn("memory_candidates", tables)
        self.assertNotIn("commit_hash", [row[1] for row in conn.execute("PRAGMA table_info(specs)")])
        self.assertNotIn("task_id", [row[1] for row in conn.execute("PRAGMA table_info(runs)")])
        self.assertNotIn("attempts", [row[1] for row in conn.execute("PRAGMA table_info(specs)")])
        spec = conn.execute("SELECT status, review_cycles, outcome FROM specs WHERE id = 'spec_old'").fetchone()
        self.assertEqual(spec["status"], "implementing")
        self.assertEqual(spec["review_cycles"], 0)
        self.assertEqual(spec["outcome"], "old outcome")


if __name__ == "__main__":
    unittest.main()
