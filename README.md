# Codex Workflow Harness

A lightweight workflow layer for AI-assisted development. Keeps Codex work structured — spec before code, tests before merge, memory that persists across sessions — without replacing the normal Codex UI.

**Requirements:** Python 3.9+, no external dependencies.

## How it works

The harness is split into two separate command sets with different responsibilities:

### `harness-admin` — source repo only

Lives in this repo and is never copied to projects. Manages installations: installs the harness into other projects and upgrades them when the source changes.

### `harness` — installed in each project

Copied into `.harness/` inside each target project. Runs from within that project and always operates on that project's own database and config. Handles the full development workflow: specs, tasks, tests, review, memory.

The two commands are independent by design. `harness-admin` knows about this source repo and the projects it manages. `harness` knows nothing about where it came from — it only knows about the project it lives in.

---

## Setup (run once after cloning this repo)

```powershell
python harness-admin.py setup
```

Adds `bin/` to your user PATH and initializes the local harness DB. Open a new terminal — both commands are then available globally.

---

## Admin commands (`harness-admin`)

```powershell
# install harness into a project
harness-admin install --target C:\path\to\Project

# upgrade harness files + run DB migrations in all installed projects
harness-admin upgrade --scan C:\Users\you\Projects
harness-admin upgrade --scan C:\path\one C:\path\two

# upgrade current directory only
harness-admin upgrade
```

`install` copies only template files — never DB, logs, or project-specific config:

- `.harness/harness.py`
- `.harness/config.yaml`
- `.harness/BEST_PRACTICES.md`
- `AGENTS.md` (appended if already exists)
- `README.md` (copied as `README.harness.md` if a README already exists)

`upgrade` skips this source repo automatically and preserves each project's `config.yaml`.

---

## Project commands (`harness`)

Run from within any installed project. `harness` resolves to that project's `.harness/harness.py` automatically.

### Workflow

```powershell
# 1. Start a task — create new or activate an existing draft
harness spec new "add email uniqueness validation"
harness spec activate <spec_id>

# 2. Refine spec fields, then lock after user approval
harness spec set --goal "..." --acceptance "..." --tests "..."
harness spec lock

# 3. Get context before implementing
harness context "email uniqueness validation"

# 4. Implement
harness task attempt

# 5. Test and review
harness test plan
harness test run --targeted
harness review

# 6. Close — commits automatically with conventional commits
harness close
harness close --type fix        # override inferred commit type
harness close --no-commit       # skip automatic commit
```

### Specs

A spec is the contract for a task. Codex must not implement until the spec is locked by the user.

Fields: goal, scope, out of scope, acceptance criteria, test strategy, area, risk level.

```powershell
harness spec list [--status draft|locked|closed] [--area AREA]
harness spec show
harness spec set --goal "..." --scope "..." --acceptance "..." --tests "..."
harness spec lock
harness spec activate <id>      # resume a backlog draft without creating a new spec
```

### Tests

All behavior changes require tests. If no relevant test exists, add one. If tests cannot be run, the task stops as blocked.

```powershell
harness test plan               # show which commands will run
harness test run --targeted     # run and record result
harness test run --full
```

### Memory

Stored in `.harness/harness.db` (SQLite, not committed). Searched with FTS before each implementation to surface relevant decisions, pitfalls, and conventions.

After closing a task, propose memory entries in chat. Add confirmed ones directly:

```powershell
harness memory add --kind decision --area auth --summary "..." --content "..."
harness memory search "auth login tests"
harness memory list [--kind pitfall] [--area auth]
harness context "billing webhook retry"   # active spec + relevant memory + test guidance
```

Good memory kinds: `command`, `decision`, `pitfall`, `convention`, `architecture`, `testing`.

---

## Implementation rules

These apply to all projects with harness installed:

- Do not implement until the spec is locked.
- All files must be saved as UTF-8 without BOM.
- Existing files must be edited in place, never deleted and recreated.
- Code comments must be written in Spanish: functions, CSS selectors, HTML/template blocks.
- Comments explain intent — never restate obvious syntax.
