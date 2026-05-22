#!/usr/bin/env python3
"""Admin tool for the Harness source repo.

Run this from the Harness source repo to install or upgrade harness
instances in other projects. Never ships to target projects.

Commands:
  setup                        add bin/ to user PATH and init local harness (run once after clone)
  install --target <dir>       install harness into a project
  upgrade --scan <dir> [...]   update harness files + run migrations in installed projects
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ADMIN_SCRIPT = Path(__file__).resolve()
SOURCE_ROOT = ADMIN_SCRIPT.parent
HARNESS_TEMPLATE_DIR = SOURCE_ROOT / ".harness"

TEMPLATE_FILES = ["harness.py", "config.yaml", "BEST_PRACTICES.md"]


def copy_harness_files(target_harness: Path, force: bool, skip: set[str] | None = None) -> list[str]:
    copied: list[str] = []
    for name in TEMPLATE_FILES:
        if skip and name in skip:
            continue
        source = HARNESS_TEMPLATE_DIR / name
        if not source.exists():
            print(f"Warning: template file missing: {source}", file=sys.stderr)
            continue
        destination = target_harness / name
        if destination.exists() and not force:
            continue
        shutil.copyfile(source, destination)
        copied.append(str(destination.relative_to(target_harness.parent)))
    return copied


def install_readme(target: Path, force: bool) -> str:
    source = SOURCE_ROOT / "README.md"
    destination = target / "README.md"
    if not source.exists():
        return "source README.md missing"
    if not destination.exists():
        shutil.copyfile(source, destination)
        return "README.md created"
    harness_destination = target / "README.harness.md"
    if harness_destination.exists() and not force:
        return "README.harness.md already exists"
    shutil.copyfile(source, harness_destination)
    return "README.harness.md created"


def install_agents_file(target: Path, force: bool) -> str:
    source = SOURCE_ROOT / "AGENTS.md"
    destination = target / "AGENTS.md"
    if not source.exists():
        return "source AGENTS.md missing"
    source_text = source.read_text(encoding="utf-8")
    if not destination.exists():
        destination.write_text(source_text, encoding="utf-8")
        return "created"
    existing = destination.read_text(encoding="utf-8-sig")
    if "Codex Workflow Harness" in existing and not force:
        return "already contains harness section"
    if force and "Codex Workflow Harness" in existing:
        destination.write_text(source_text, encoding="utf-8")
        return "replaced by --force"
    destination.write_text(existing.rstrip() + "\n\n---\n\n" + source_text, encoding="utf-8")
    return "appended harness section"


def upgrade_agents_file(target: Path) -> str:
    source = SOURCE_ROOT / "AGENTS.md"
    destination = target / "AGENTS.md"
    if not source.exists():
        return "source AGENTS.md missing"
    source_text = source.read_text(encoding="utf-8")
    if not destination.exists():
        destination.write_text(source_text, encoding="utf-8")
        return "created"
    existing = destination.read_text(encoding="utf-8-sig")
    if "Codex Workflow Harness" not in existing:
        # no harness section yet — append
        destination.write_text(existing.rstrip() + "\n\n---\n\n" + source_text, encoding="utf-8")
        return "harness section appended"
    # replace only the harness section, preserve project-specific content before it
    separator = "\n\n---\n\n"
    if separator in existing:
        parts = existing.split(separator)
        # find which part contains the harness section
        project_parts = [p for p in parts if "Codex Workflow Harness" not in p]
        if project_parts:
            destination.write_text(
                separator.join(project_parts).rstrip() + separator + source_text,
                encoding="utf-8",
            )
            return "harness section updated (project content preserved)"
    # entire file is the harness section — replace it
    destination.write_text(source_text, encoding="utf-8")
    return "harness section updated"



def initialize_target(target: Path) -> None:
    script = target / ".harness" / "harness.py"
    subprocess.run(
        [sys.executable, str(script), "init"],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )


def scan_projects(scan_dirs: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in scan_dirs:
        root = Path(raw).expanduser().resolve()
        if not root.exists():
            print(f"Warning: scan dir does not exist: {root}", file=sys.stderr)
            continue
        for dirpath, dirnames, _ in os.walk(root):
            current = Path(dirpath)
            if (current / ".harness" / "harness.py").exists():
                found.append(current)
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    return found


def command_install(args: argparse.Namespace) -> None:
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        if args.create:
            target.mkdir(parents=True)
        else:
            sys.exit(f"Target does not exist: {target}")
    if not target.is_dir():
        sys.exit(f"Target is not a directory: {target}")

    target_harness = target / ".harness"
    target_harness.mkdir(exist_ok=True)
    (target_harness / "artifacts").mkdir(exist_ok=True)
    (target_harness / "logs").mkdir(exist_ok=True)

    copied = copy_harness_files(target_harness, force=args.force)
    readme_result = install_readme(target, force=args.force)
    agents_result = install_agents_file(target, force=args.force)
    initialize_target(target)

    print(f"Installed harness in {target}")
    for item in copied:
        print(f"  copied: {item}")
    print(f"  README:    {readme_result}")
    print(f"  AGENTS.md: {agents_result}")
    print("Run from the target project:")
    print("  python .harness/harness.py detect")


def command_upgrade(args: argparse.Namespace) -> None:
    if not args.scan:
        cwd = Path.cwd().resolve()
        if not (cwd / ".harness" / "harness.py").exists():
            sys.exit("No .harness/harness.py found in current directory. Use --scan to specify dirs.")
        targets = [cwd]
        print(f"Upgrading current directory: {cwd}")
        print()
    else:
        projects = scan_projects(args.scan)
        if not projects:
            print("No projects with .harness found.")
            return
        targets = [p for p in projects if p.resolve() != SOURCE_ROOT.resolve()]
        skipped = len(projects) - len(targets)
        print(f"Found {len(projects)} project(s){f', skipping source repo' if skipped else ''}:")
        for p in projects:
            marker = "  (source repo — skipped)" if p.resolve() == SOURCE_ROOT.resolve() else ""
            print(f"  {p}{marker}")
        print()

    errors: list[str] = []
    upgraded = 0
    for project in targets:
        target_harness = project / ".harness"
        copied = copy_harness_files(target_harness, force=True, skip={"config.yaml"})
        agents_result = upgrade_agents_file(project)
        result = subprocess.run(
            [sys.executable, str(target_harness / "harness.py"), "init"],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        migrations_out = result.stderr.strip()
        status = "ok" if result.returncode == 0 else "ERROR"
        print(f"{project.name}  ({project})")
        for f in copied:
            print(f"  copied: {f}")
        print(f"  AGENTS.md: {agents_result}")
        if migrations_out:
            print(f"  migrations: {migrations_out}")
        print(f"  status: {status}")
        if result.returncode != 0:
            errors.append(f"{project}: {result.stderr.strip() or result.stdout.strip()}")
        else:
            upgraded += 1

    print()
    if errors:
        print(f"Errors in {len(errors)} project(s):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print(f"Upgraded {upgraded} project(s) successfully.")


def add_to_path_windows(bin_dir: Path) -> str:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    )
    try:
        current, _ = winreg.QueryValueEx(key, "PATH")
    except FileNotFoundError:
        current = ""
    entries = [e for e in current.split(";") if e]
    bin_str = str(bin_dir)
    if bin_str in entries:
        return "already in PATH"
    entries.append(bin_str)
    winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, ";".join(entries))
    winreg.CloseKey(key)
    # notify running shells of the change
    import ctypes
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, None)
    return f"added to user PATH: {bin_str}"


def add_to_path_unix(bin_dir: Path) -> str:
    bin_str = str(bin_dir)
    export_line = f'\nexport PATH="$PATH:{bin_str}"  # harness-admin\n'
    added_to: list[str] = []
    for rc in [Path.home() / ".bashrc", Path.home() / ".zshrc"]:
        if rc.exists() and bin_str not in rc.read_text():
            with rc.open("a") as f:
                f.write(export_line)
            added_to.append(rc.name)
    if not added_to:
        return "already in shell rc files (or no .bashrc/.zshrc found)"
    return f"appended to: {', '.join(added_to)} — restart shell or run: source ~/{added_to[0]}"


def command_setup(_: argparse.Namespace) -> None:
    bin_dir = SOURCE_ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)

    # ensure launchers exist (idempotent)
    create_launchers_admin(bin_dir)

    # add to PATH
    if sys.platform == "win32":
        result = add_to_path_windows(bin_dir)
    else:
        result = add_to_path_unix(bin_dir)
    print(f"PATH: {result}")

    # init local harness DB
    local_script = SOURCE_ROOT / ".harness" / "harness.py"
    if local_script.exists():
        proc = subprocess.run(
            [sys.executable, str(local_script), "init"],
            cwd=SOURCE_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            print("Local harness: initialized")
        else:
            print(f"Local harness: ERROR — {proc.stderr.strip()}", file=sys.stderr)

    print()
    print("Setup complete. Open a new terminal and run: harness-admin --help")


def create_launchers_admin(bin_dir: Path) -> None:
    (bin_dir / "harness-admin.ps1").write_text(
        '$script = Join-Path $PSScriptRoot "..\\harness-admin.py"\n'
        "python $script @args\n",
        encoding="utf-8",
    )
    (bin_dir / "harness-admin.cmd").write_text(
        "@echo off\r\n"
        'python "%~dp0..\\harness-admin.py" %*\r\n',
        encoding="utf-8",
    )
    (bin_dir / "harness.ps1").write_text(
        '$script = Join-Path $PWD ".harness\\harness.py"\n'
        'if (-not (Test-Path $script)) {\n'
        '    Write-Error "No .harness/harness.py found in current directory. Run from a project with harness installed."\n'
        '    exit 1\n'
        '}\n'
        "python $script @args\n",
        encoding="utf-8",
    )
    (bin_dir / "harness.cmd").write_text(
        "@echo off\r\n"
        'if not exist "%CD%\\.harness\\harness.py" (\r\n'
        '    echo Error: No .harness/harness.py found in current directory. Run from a project with harness installed.\r\n'
        '    exit /b 1\r\n'
        ')\r\n'
        'python "%CD%\\.harness\\harness.py" %*\r\n',
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Harness admin — install and upgrade harness instances in other projects"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="add bin/ to user PATH and init local harness (run once after clone)")
    setup.set_defaults(func=command_setup)

    install = sub.add_parser("install", help="install harness into a target project directory")
    install.add_argument("--target", required=True, metavar="DIR")
    install.add_argument("--force", action="store_true", help="overwrite existing files")
    install.add_argument("--create", action="store_true", help="create target dir if it does not exist")
    install.set_defaults(func=command_install)

    upgrade = sub.add_parser(
        "upgrade",
        help="update harness files and run migrations; without --scan upgrades current directory",
    )
    upgrade.add_argument("--scan", nargs="+", default=None, metavar="DIR",
                         help="one or more root dirs to scan for projects with .harness")
    upgrade.set_defaults(func=command_upgrade)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
