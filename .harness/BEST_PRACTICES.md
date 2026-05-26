# Harness Best Practices

This file defines how Codex should maintain specs, test plans, memory, and searches so the harness remains useful as the project grows.

## Spec Quality

A spec is created only after the user approves the contract in chat. It should be short, explicit, and testable.

Good spec fields:

- `goal`: desired outcome in one or two sentences
- `scope`: files, modules, workflows, or behaviors that may change
- `out_of_scope`: related things that must not be touched
- `acceptance_criteria`: observable conditions that must be true
- `test_strategy`: validation required by the contract
- `area`: stable feature identifier such as `auth`, `projects_budget`, `task_notifications`, `users_filters`
- `risk_level`: `low`, `medium`, or `high`

Avoid:

- vague acceptance such as "works correctly"
- hidden scope expansion during implementation
- creating a spec before the user approves the contract
- using broad area names when a concrete feature name is available

## Spec States

Valid states:

- `implementing`: approved spec is being worked on
- `ready_for_review`: planned tests passed and Codex verified requirements; wait for user approval
- `blocked`: Codex cannot continue without a user decision
- `closed`: user-approved work is finished

Do not use draft, locked, task, pending, in_progress, or done states.

Use `review_cycles` for in-scope changes requested by the user during final review.

## Implementation Discipline

Before implementation:

- Read the active spec.
- Fetch context with `python .harness/harness.py context "short task query"`.

During implementation:

- Change only what is needed for the spec.
- If the code reveals the spec is wrong, stop and ask to redefine it.

After implementation:

- Generate a post-implementation test plan with `python .harness/harness.py test plan`.
- Add or update relevant tests if the plan requires it.
- Run the planned targeted tests first.
- Run broader tests when risk is high or touched code is shared.
- If planned tests fail, keep working in `implementing` and fix the issue.
- After tests pass, Codex must verify in chat that the approved requirements are satisfied, then run `python .harness/harness.py spec ready --reason "..."`.
- If the user requests an in-scope change from `ready_for_review`, run `python .harness/harness.py spec revise --reason "..."`.

## Test Planning

`test_strategy` is the approved validation requirement. `test_plan` is the operative plan generated after implementation from the repository and current diff.

A good test plan includes:

- existing tests found by area, filename, class, group, or mark
- changed files from the current implementation diff
- testing memory related to the area
- tests to update
- tests to create
- required commands
- full/lint/build commands when useful
- a coverage map from acceptance criteria to tests or planned tests

Use `area` consistently so tests are discoverable. Prefer test names, groups, or marks that include the area.

Examples:

```php
/**
 * @group projects_budget
 */
```

```python
class TestProjectsBudget:
    ...
```

## Implementation Rules

Codex must apply these rules to every file it creates or edits.

Encoding:

- Save files as UTF-8 without BOM.
- Do not introduce mixed encodings.
- If a file already has a different encoding, stop and ask before converting it.

File modification:

- Edit existing files in place.
- Do not delete and recreate an existing file as a way to modify it.
- Preserve file identity, line endings, permissions, and history where possible.
- Delete/recreate is allowed only when the user explicitly approves it or when replacing generated artifacts where that behavior is already established by the project.

Comment language:

- Write implementation comments in Spanish.
- Keep comments concise and useful.
- Explain intent, responsibility, constraints, or non-obvious behavior.
- Do not add noisy comments that merely repeat syntax.

Functions:

- Every new or modified function should have a Spanish comment explaining what it does.
- For languages with docblocks/docstrings, prefer the local convention.
- For very small private callbacks or one-line lambdas, a nearby block-level comment is enough if a function-level comment would add noise.

CSS:

- Comment each new or modified selector or selector group.
- The comment should explain the role of the block, not list the properties.

HTML/templates:

- Comment new or modified components or meaningful structural blocks.
- Prefer comments at component boundaries.
- Do not comment every small tag.

Review checks:

- Confirm comments are in Spanish.
- Confirm function comments exist where applicable.
- Confirm CSS selectors or selector groups are documented when styles are changed.
- Confirm HTML/template components are documented when structure is changed.
- Confirm files are UTF-8 without BOM.
- Confirm existing files were edited in place, not deleted and recreated.

## Memory Quality

Memory should help future Codex sessions avoid rediscovery.

Store:

- `command`: exact command that works for an area
- `testing`: where relevant tests live and how to run them
- `decision`: a user-approved durable choice
- `pitfall`: a known trap, failing assumption, or risky area
- `convention`: local code style or workflow convention
- `architecture`: concise map of important components

Do not store:

- whole conversations
- full logs
- full diffs
- temporary debugging guesses
- facts that are obvious from file names
- stale information without marking it inactive or deprecated

## Memory Proposal Rules

At `ready_for_review`, Codex should propose memory instead of writing it directly.

A good proposal has:

- one reusable idea
- a stable `area`
- a precise `kind`
- searchable `tags`
- a summary under roughly 120 characters
- enough content to be useful without rereading the original task
- a source, usually the spec id
- an active default

Add accepted memory before `harness close` so the memory update can be committed with the same work.

## Area And Tag Hygiene

Use stable, concrete area names. Do not invent a new area for every task.

Prefer:

- `auth`
- `projects_budget`
- `task_notifications`
- `users_filters`
- `database`
- `harness`

Tags should include likely future search words:

```text
auth, login, validation, tests
projects_budget, ajax, calculations, tests
database, migration, seed, rollback
```

Avoid tag noise:

- no long phrases
- no dates unless date is semantically important
- no one-off ticket names unless needed

## Search Hygiene

Codex should query the harness with task-shaped phrases, not huge prompts.

Good:

```powershell
python .harness/harness.py context "auth login validation tests"
python .harness/harness.py memory search "projects_budget ajax tests"
```

If search results are poor:

- search by area plus behavior
- search by command name
- search by test class or file name
- include inactive memory only when debugging history

## Deprecation

When memory becomes wrong:

```powershell
python .harness/harness.py memory deprecate <memory_id>
```

When memory is valid but too noisy for default context:

```powershell
python .harness/harness.py memory set-active <memory_id> no
```

Inactive is not deleted. Deprecated means "do not rely on this."
