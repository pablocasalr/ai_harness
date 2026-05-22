# Codex Workflow Harness

This harness keeps Codex work structured without replacing the normal Codex UI.

It provides:

- locked specs before implementation
- mandatory test-based verification
- contract review with a bounded retry loop
- implementation rules for comments and UTF-8 without BOM
- SQLite memory with full-text search
- user-reviewed memory proposals

Run commands from the project root:

```powershell
python .harness/harness.py init
python .harness/harness.py spec new "add email uniqueness validation"
python .harness/harness.py spec lock
python .harness/harness.py context "email uniqueness validation"
python .harness/harness.py task attempt
python .harness/harness.py test plan
python .harness/harness.py test run --targeted
python .harness/harness.py review
python .harness/harness.py close
```

If `.harness/bin` is in your PATH, the same CLI can be used as:

```powershell
harness install --target .
harness spec new "add email uniqueness validation"
harness test run --targeted
```

## Install In Another Project

Install the harness into any project:

```powershell
python C:\path\to\Harness\.harness\harness.py install --target C:\path\to\Project
```

Or, when the launcher is available in PATH:

```powershell
harness install --target .
```

The installer copies only reusable harness files:

- `AGENTS.md`
- `README.md`
- `.harness/harness.py`
- `.harness/config.yaml`
- `.harness/BEST_PRACTICES.md`
- `.harness/bin/harness.ps1`
- `.harness/bin/harness.cmd`

It does not copy project state:

- `.harness/harness.db`
- `.harness/logs`
- `.harness/artifacts`

If `AGENTS.md` already exists in the target project, the harness section is appended instead of deleting or recreating the file. Use `--force` only when you explicitly want to overwrite harness files.

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
python .harness/harness.py memory candidates
python .harness/harness.py memory accept <candidate_id> --active yes
python .harness/harness.py memory reject <candidate_id>
```

## Search

The harness uses SQLite FTS, a local full-text index. It searches indexed words quickly and returns a small amount of relevant context, which reduces token usage.

Use tags and area names consistently so text search stays reliable:

```powershell
python .harness/harness.py memory search "auth login tests"
python .harness/harness.py context "billing webhook retry"
```
