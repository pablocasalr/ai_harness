# Codex Workflow Harness

This project uses `.harness/` as the workflow contract for Codex.

## Core Rules

- Do not implement a non-trivial change until there is a locked spec.
- The user owns scope approval. If the spec is ambiguous, refine it before coding.
- Every behavior change must have a relevant test. If no relevant test exists, add one.
- If a relevant test cannot be added or run, stop and report the blocker.
- Do not use browser/UI verification in this harness. Verification is test based.
- Do not change a locked spec during implementation without explicit user approval.
- Keep implementation attempts scoped to the approved spec.
- Make at most two implementation attempts before asking the user how to proceed.
- All source files created or modified by Codex must be saved as UTF-8 without BOM.
- Code comments must be written in Spanish.
- Add a concise Spanish comment for each function explaining what it does.
- In CSS, comment selectors or selector groups in Spanish when adding or modifying style blocks.
- In HTML/templates, comment components or meaningful structural blocks in Spanish.
- Comments must clarify intent, not restate obvious syntax.
- Existing files must be edited in place. Do not delete and recreate a file to modify it unless the user explicitly approves that operation.
- If the implementation generates database migrations, run them before committing.

## Normal Workflow

1. Create or refine a spec:
   `python .harness/harness.py spec new "short title"`
2. Ask the user to review scope, acceptance criteria, and test strategy.
3. Lock the spec only after user approval:
   `python .harness/harness.py spec lock`
4. Get focused context before coding:
   `python .harness/harness.py context "task description"`
5. Start an implementation attempt:
   `python .harness/harness.py task attempt`
6. Implement the smallest scoped change that satisfies the spec.
7. Plan and run tests:
   `python .harness/harness.py test plan`
   `python .harness/harness.py test run --targeted`
8. Review the contract:
   `python .harness/harness.py review`
9. If review fails for code reasons, make one focused fix attempt and repeat tests/review.
10. If review still fails, stop and report what failed, what was tried, and options.
11. After user approval, close the work:
    `python .harness/harness.py close`
    This closes the spec/task in DB and prints a suggested commit message.
12. If the implementation includes database migrations, run them before committing.
13. Stage the files you modified plus the harness logs, then commit using the suggested message:
    `git add <file1> <file2> ... .harness/logs/`
    `git commit -m "feat(auth): add login"`
13. Register the commit hash:
    `python .harness/harness.py close --register-hash <hash>`
14. Propose any reusable memory entries via chat. After user confirms, add them:
    `python .harness/harness.py memory add --kind decision --area AUTH --summary "..." --content "..."`

## Command Reference

```
# Specs
python .harness/harness.py spec new "title" [--area AREA] [--risk low|medium|high]
python .harness/harness.py spec list [--status draft|locked|closed] [--area AREA]
python .harness/harness.py spec show [ID]          # defaults to active spec
python .harness/harness.py spec set [ID] --goal TEXT --scope TEXT --out-of-scope TEXT --acceptance TEXT --tests TEXT --area AREA --risk LEVEL
python .harness/harness.py spec lock [ID] [--force]
python .harness/harness.py spec activate ID     # set a backlog draft as active without creating a new one

# Tasks
python .harness/harness.py task attempt            # start new implementation attempt
python .harness/harness.py task active             # show current active task
python .harness/harness.py task list [--status pending|in_progress|done|blocked] [--spec SPEC_ID]

# Context
python .harness/harness.py context "query" [--include-inactive]

# Tests
python .harness/harness.py test plan [--pattern PATTERN]
python .harness/harness.py test run [--targeted|--full] [--pattern PATTERN] [--dry-run]

# Review & close
python .harness/harness.py review
python .harness/harness.py close

# Memory — search and list
python .harness/harness.py memory search "query" [--include-inactive] [--limit N]
python .harness/harness.py memory list [--all] [--kind KIND] [--area AREA] [--status active|deprecated]

# Memory — add directly (use propose instead during tasks)
python .harness/harness.py memory add --kind KIND --area AREA --summary TEXT --content TEXT [--tags TAGS] [--active yes|no]

# Memory — lifecycle
python .harness/harness.py memory set-active ID yes|no
python .harness/harness.py memory deprecate ID
```

Valid memory kinds: `command`, `decision`, `pitfall`, `convention`, `architecture`, `testing`

## Memory Rules

- Do not store full conversations, large logs, complete diffs, or obvious code facts.
- Store reusable project knowledge only: commands, decisions, conventions, pitfalls, and architecture notes.
- Prefer concise summaries with area, kind, tags, source, and active status.
- Propose memory entries via chat after close. Wait for user confirmation before running `memory add`.
- Active memory is returned by default in context searches. Inactive memory requires `--include-inactive`.
- Follow `.harness/BEST_PRACTICES.md` for area names, tags, memory quality, and search hygiene.

## Review Contract

After running `harness review`, read the **Spec Self-check** section printed at the end. Confirm in chat that every acceptance criterion is satisfied before reporting the work as ready. Do not mark ready if any criterion is unverified.

Before marking work ready for user review, confirm:

- Spec is locked.
- Acceptance criteria are satisfied.
- Behavior changes have tests.
- Relevant tests passed.
- Changes are within scope.
- Diff size is reasonable for the spec.
- No accidental debug code, TODOs, or unrelated refactors were introduced.
- Implementation comment rules were followed: functions, CSS selectors, and HTML/template components are documented in Spanish where applicable.
- Modified files are UTF-8 without BOM.
- Existing files were edited in place and were not deleted/recreated as a shortcut.
