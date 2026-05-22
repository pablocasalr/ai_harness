# Harness Best Practices

This file defines how Codex should maintain specs, tasks, memory, and searches so the harness remains useful as the project grows.

## Spec Quality

A spec should be short, explicit, and testable.

Good spec fields:

- `goal`: the desired outcome in one or two sentences
- `scope`: files, modules, workflows, or behaviors that may change
- `out_of_scope`: related things that must not be touched
- `acceptance_criteria`: observable conditions that must be true
- `test_strategy`: exact test class/file/filter/command when known
- `area`: stable area name such as `auth`, `billing`, `api`, `cli`, `database`, `frontend`, `infra`, `harness`
- `risk_level`: `low`, `medium`, or `high`

Avoid:

- vague acceptance such as "works correctly"
- hidden scope expansion during implementation
- spec changes after lock without explicit user approval
- mixing several unrelated tasks into one spec

## Task Discipline

Before implementation:

- Read the active spec.
- Fetch context with `python .harness/harness.py context "short task query"`.
- Start an attempt with `python .harness/harness.py task attempt`.

During implementation:

- Change only what is needed for the spec.
- If the code reveals the spec is wrong, stop and ask to revise the spec.
- If a behavior change has no test, add a relevant test before review.

After implementation:

- Run targeted tests first.
- Run broader tests when the risk level is high or touched code is shared.
- Run `python .harness/harness.py review`.
- If review fails for code reasons, perform only one focused fix attempt.

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

Good:

```css
/* Define el estado visual del boton principal del formulario. */
.form-primary-button {
  ...
}
```

Bad:

```css
/* Color azul y padding. */
.form-primary-button {
  ...
}
```

HTML/templates:

- Comment new or modified components or meaningful structural blocks.
- Prefer comments at component boundaries.
- Do not comment every small tag.

Good:

```html
<!-- Formulario principal de acceso del usuario. -->
<form>
  ...
</form>
```

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

At close, Codex should propose memory instead of writing it directly.

A good proposal has:

- one reusable idea
- a stable `area`
- a precise `kind`
- searchable `tags`
- a summary under roughly 120 characters
- enough content to be useful without rereading the original task
- a source, usually the spec id
- an active default

Use active memory for:

- current commands
- current conventions
- current decisions
- high-confidence pitfalls

Use inactive memory for:

- historical acceptance criteria
- low-confidence observations
- rare edge cases
- notes that may be useful but should not appear in default context

## Area And Tag Hygiene

Use stable, boring area names. Do not invent a new area for every task.

Prefer:

- `auth`
- `billing`
- `api`
- `database`
- `cli`
- `frontend`
- `infra`
- `testing`
- `harness`

Tags should include likely future search words:

```text
auth, login, validation, tests
billing, webhook, retry, idempotency
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
python .harness/harness.py memory search "billing webhook idempotency"
```

Bad:

```powershell
python .harness/harness.py context "Here is the full user conversation..."
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
