# Codex Workflow Harness

This harness keeps Codex work structured without replacing the normal Codex UI.

It provides:

- locked specs before implementation
- mandatory test-based verification
- contract review with a bounded retry loop
- implementation rules for comments and UTF-8 without BOM
- SQLite memory with full-text search
- user-reviewed memory proposals

## Setup (run once after cloning)

```powershell
python harness-admin.py setup
```

Adds `bin/` to your user PATH and initializes the local harness DB. After opening a new terminal, both `harness-admin` and `harness` are available globally.

- `harness-admin` — manages installations (install, upgrade)
- `harness` — project commands; auto-resolves to `.harness/harness.py` in the current directory

## Admin commands (`harness-admin`)

`harness-admin.py` lives in this repo and is never copied to projects.

```powershell
# install harness into another project
harness-admin install --target C:\path\to\Project

# update harness files + run DB migrations in all installed projects
harness-admin upgrade --scan C:\Users\you\Projects
harness-admin upgrade --scan C:\path\one C:\path\two
```

`install` copies only template files — never DB, logs, or artifacts:

- `.harness/harness.py`
- `.harness/config.yaml`
- `.harness/BEST_PRACTICES.md`
- `AGENTS.md` (appended if already exists)
- `README.md` (copied as `README.harness.md` if README already exists)

`upgrade` skips this source repo automatically and preserves each project's `config.yaml`.

## Project commands (`harness`)

Run from within any installed project directory. `harness` resolves to that project's `.harness/harness.py` automatically.

```powershell
harness spec new "add email uniqueness validation"
harness spec lock
harness context "email uniqueness validation"
harness task attempt
harness test plan
harness test run --targeted
harness review
harness close
```

## Specs

A spec is the contract for a task. It should define:

- goal
- scope
- out of scope
- acceptance criteria
- affected area
- test strategy
- risk level

Codex should not implement until the spec is locked by user approval.

## Tests

All behavior changes require tests. If a relevant test exists, update or extend it. If no relevant test exists, add one.

If tests cannot be added or run, the task must stop as blocked instead of being marked ready.

## Implementation Rules

Codex must save created or modified files as UTF-8 without BOM.

Existing files must be edited in place. Codex must not delete and recreate an existing file as a shortcut unless the user explicitly approves it.

Comments added by Codex must be in Spanish:

- functions: concise comment explaining what the function does
- CSS: comment selectors or selector groups
- HTML/templates: comment components or meaningful structural blocks

Comments should explain intent and avoid repeating obvious syntax.

## Memory

Memory is stored in `.harness/harness.db`.

Accepted active memory is returned by default from context searches. Inactive memory remains searchable only when explicitly requested.

Good memory:

- command: exact command that works for a project area
- decision: stable architecture or workflow decision
- pitfall: known failure mode or trap
- convention: local coding/testing convention
- architecture: concise structural note
- testing: where tests live and how to run them

Bad memory:

- full conversations
- large logs
- full diffs
- temporary observations
- obvious facts visible in file names
- anything stale without status/deprecation

Memory should normally be proposed at close, then accepted or rejected by the user:

```powershell
harness memory candidates
harness memory accept <candidate_id> --active yes
harness memory reject <candidate_id>
```

## Search

The harness uses SQLite FTS, a local full-text index. It searches indexed words quickly and returns a small amount of relevant context, which reduces token usage.

Use tags and area names consistently so text search stays reliable:

```powershell
harness memory search "auth login tests"
harness context "billing webhook retry"
```
