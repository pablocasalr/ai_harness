# Codex Workflow Harness

A lightweight workflow layer for AI-assisted development. It keeps Codex work structured around one approved spec, planned tests, Codex requirement checks, and reusable memory without replacing the normal Codex UI.

**Requirements:** Python 3.9+, no external dependencies.

## How It Works

The harness has two command sets:

### `harness-admin` - source repo only

Lives in this repo and is never copied as the project workflow command. It installs or upgrades harness instances in other projects.

### `harness` - installed in each project

Copied into `.harness/` inside each target project. It operates on that project's own SQLite database, config, logs, specs, test runs, and memory.

---

## Setup

Run once after cloning this repo:

```powershell
python harness-admin.py setup
```

Then open a new terminal.

---

## Admin Commands

```powershell
# install harness into a project
harness-admin install --target C:\path\to\Project

# upgrade harness files + run DB migrations in installed projects
harness-admin upgrade --scan C:\Users\you\Projects

# upgrade current directory only
harness-admin upgrade
```

`install` copies template files only:

- `.harness/harness.py`
- `.harness/config.yaml`
- `.harness/BEST_PRACTICES.md`
- `AGENTS.md`
- `README.md` or `README.harness.md`

`upgrade` preserves each target project's `config.yaml`.

---

## Project Workflow

Run from inside an installed project:

```powershell
# 1. Codex and the user define the contract in chat.

# 2. After user approval, create the spec directly in implementing state.
harness spec create --title "add email uniqueness validation" --area auth --goal "..." --scope "..." --out-of-scope "..." --acceptance "..." --tests "..." --risk medium

# 3. Gather context before implementing.
harness context "auth email uniqueness validation"

# 4. Implement the scoped change.

# 5. Generate the post-implementation test plan, update/add tests if needed, then run planned tests.
harness test plan
harness test run --targeted

# 6. Codex verifies requirements in chat after tests pass, then marks ready.
harness spec ready --reason "Codex verified the implementation against the approved requirements"

# 7. If the user requests an in-scope review change, reopen it.
harness spec revise --reason "adjust button label requested in review"

# 8. After user approval, add accepted memory entries, close, then commit outside the harness.
harness memory add --kind decision --area auth --summary "..." --content "..."
harness close
git add ...
git commit -m "feat(auth): add email uniqueness validation"
```

Valid spec states:

- `implementing`
- `ready_for_review`
- `blocked`
- `closed`

---

## Commands

```powershell
# Specs
harness spec create --title TEXT --area AREA --goal TEXT --scope TEXT --out-of-scope TEXT --acceptance TEXT --tests TEXT --risk low|medium|high
harness spec list [--status implementing|ready_for_review|blocked|closed] [--area AREA]
harness spec show [ID]
harness spec revise [--reason TEXT]
harness spec ready [--reason TEXT]
harness spec block --reason TEXT

# Context
harness context "query" [--include-inactive]

# Tests
harness test plan
harness test run [--targeted|--full] [--dry-run]

# Close
harness close [--outcome TEXT]

# Memory
harness memory add --kind KIND --area AREA --summary TEXT --content TEXT [--tags TAGS] [--active yes|no]
harness memory search "query" [--include-inactive] [--limit N]
harness memory list [--all] [--kind KIND] [--area AREA] [--status active|deprecated]
harness memory set-active ID yes|no
harness memory deprecate ID
```

Removed commands (`task`, `spec lock`, `spec set`, `spec activate`) fail with a clear message. The approved contract is created once with `spec create`.

Use `spec ready` only after Codex has verified in chat that the implementation satisfies the approved requirements. Use `spec revise` when the user asks for an in-scope change during final review. Use `spec block` when Codex needs a user decision.

---

## Test Planning

`test_strategy` is part of the approved spec. `test_plan` is generated after implementation and before tests run, so it can inspect the real diff and choose the right validation.

`harness test plan` inspects:

- `area`
- changed files from the current diff
- existing files under `tests/`
- test names, groups, marks, and class names
- testing memory related to the area

It stores:

- relevant existing tests
- tests to update
- tests to create
- required commands
- optional full/lint/build commands
- acceptance criteria coverage map

`harness test run` executes the commands declared in `test_plan`. If tests pass, Codex is responsible for reviewing whether the implementation satisfies the approved requirements before running `harness spec ready`.

The harness source repo uses:

```powershell
python -m unittest discover -s tests
```

---

## Memory

Memory is stored in `.harness/harness.db` and should contain reusable project knowledge only: commands, decisions, pitfalls, conventions, architecture notes, and testing notes.

Codex proposes memory entries in chat after `ready_for_review`. Add only user-approved entries before `harness close` so the memory update can be committed with the same work.
