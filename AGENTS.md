# Codex Workflow Harness

This project uses `.harness/` as the workflow contract for Codex.

## Core Rules

- Do not implement a non-trivial change until the contract has been defined with the user and approved.
- After approval, create one complete spec with `harness spec create`; do not create empty draft specs.
- The only operative entity is the spec. Do not use task-based workflow.
- Valid spec states are `implementing`, `ready_for_review`, `blocked`, and `closed`.
- Every behavior change must have relevant tests. If no relevant test exists, add one.
- If a relevant test cannot be added or run, mark the work blocked and report the blocker.
- Do not use browser/UI verification in this harness. Verification is test based.
- Keep implementation work scoped to the approved spec.
- User-requested in-scope review changes use `spec revise`.
- All source files created or modified by Codex must be saved as UTF-8 without BOM.
- Code comments must be written in Spanish.
- Add a concise Spanish comment for each function explaining what it does.
- In CSS, comment selectors or selector groups in Spanish when adding or modifying style blocks.
- In HTML/templates, comment components or meaningful structural blocks in Spanish.
- Comments must clarify intent, not restate obvious syntax.
- Existing files must be edited in place. Do not delete and recreate a file to modify it unless the user explicitly approves that operation.
- If the implementation generates database migrations, run them before committing.

## Normal Workflow

1. Define the contract with the user in chat: goal, scope, out of scope, acceptance criteria, test strategy, area, and risk.
2. After user approval, create the spec:
   `python .harness/harness.py spec create --title "short title" --area AREA --goal "..." --scope "..." --out-of-scope "..." --acceptance "..." --tests "..." --risk medium`
3. Get focused context:
   `python .harness/harness.py context "task description"`
4. Implement the smallest scoped change that satisfies the spec.
5. Generate the post-implementation test plan:
   `python .harness/harness.py test plan`
6. Add or update tests if the test plan requires it.
7. Run planned tests:
   `python .harness/harness.py test run --targeted`
8. Codex must review in chat whether the implementation satisfies the approved requirements.
9. If Codex verifies the requirements are satisfied, mark it ready:
    `python .harness/harness.py spec ready --reason "..."`
10. If Codex needs a user decision, block the spec:
    `python .harness/harness.py spec block --reason "..."`
11. If the user requests an in-scope change while reviewing `ready_for_review`, reopen it:
    `python .harness/harness.py spec revise --reason "..."`
12. When the user approves, propose reusable memory entries via chat. After user confirmation, add them:
    `python .harness/harness.py memory add --kind decision --area AREA --summary "..." --content "..."`
13. Close the spec:
    `python .harness/harness.py close`
15. Stage files and commit outside the harness:
    `git add <files> .harness/logs/`
    `git commit -m "feat(area): short title"`

## Command Reference

```
# Specs
python .harness/harness.py spec create --title TEXT --area AREA --goal TEXT --scope TEXT --out-of-scope TEXT --acceptance TEXT --tests TEXT --risk low|medium|high
python .harness/harness.py spec list [--status implementing|ready_for_review|blocked|closed] [--area AREA]
python .harness/harness.py spec show [ID]
python .harness/harness.py spec revise [--reason TEXT]
python .harness/harness.py spec ready [--reason TEXT]
python .harness/harness.py spec block --reason TEXT

# Context
python .harness/harness.py context "query" [--include-inactive]

# Tests
python .harness/harness.py test plan
python .harness/harness.py test run [--targeted|--full] [--dry-run]
python -m unittest discover -s tests

# Close
python .harness/harness.py close [--outcome TEXT]

# Memory
python .harness/harness.py memory search "query" [--include-inactive] [--limit N]
python .harness/harness.py memory list [--all] [--kind KIND] [--area AREA] [--status active|deprecated]
python .harness/harness.py memory add --kind KIND --area AREA --summary TEXT --content TEXT [--tags TAGS] [--active yes|no]
python .harness/harness.py memory set-active ID yes|no
python .harness/harness.py memory deprecate ID
```

Valid memory kinds: `command`, `decision`, `pitfall`, `convention`, `architecture`, `testing`.

## Test Plan Rules

- `test_strategy` belongs to the approved spec.
- `test_plan` is generated after implementation and before tests run so it can inspect the actual diff.
- Codex must review existing tests by area, file/class names, groups/marks, and testing memory.
- Codex must use the changed files from the current diff to decide whether existing tests are enough.
- The plan must map acceptance criteria to an existing or planned test.
- `test run` executes commands declared in `test_plan`.
- After planned tests pass, Codex must verify requirement satisfaction in chat before `spec ready`.

## Memory Rules

- Do not store full conversations, large logs, complete diffs, or obvious code facts.
- Store reusable project knowledge only: commands, decisions, conventions, pitfalls, and architecture notes.
- Prefer concise summaries with area, kind, tags, source, and active status.
- Propose memory entries via chat after Codex marks the spec ready. Wait for user confirmation before running `memory add`.
- Active memory is returned by default in context searches. Inactive memory requires `--include-inactive`.
- Follow `.harness/BEST_PRACTICES.md` for area names, tags, memory quality, and search hygiene.

## Review Contract

Before marking work ready for user review, confirm:

- Spec is in `ready_for_review` only after Codex has verified the approved requirements.
- Acceptance criteria are satisfied.
- Behavior changes have tests.
- Planned tests passed.
- Changes are within scope.
- Diff size is reasonable for the spec.
- No accidental debug code, TODOs, or unrelated refactors were introduced.
- Implementation comment rules were followed: functions, CSS selectors, and HTML/template components are documented in Spanish where applicable.
- Modified files are UTF-8 without BOM.
- Existing files were edited in place and were not deleted/recreated as a shortcut.
