# Copilot instructions for `pf-payroll`

Modular monolith (DDD + Hexagonal):
- `src/payroll/domain`: Business rules, entities, VOs, pure calculations.
- `src/payroll/application`: Use cases, DTOs, ports, application services.
- `src/payroll/infrastructure`: DB, importers, providers, reporting, logging adapters.
- `src/payroll/interfaces`: API, CLI, dashboard entrypoints.
- `src/payroll/shared`: Cross-cutting utilities.

## Engineering standards

- Apply DRY, SOLID, Clean Code, DDD.
- Strict English: All thoughts, reasoning, code, and agent responses must be in English, regardless of user input language.
- Output Optimization: Do not include natural language explanations, markdown formatting (except standard code blocks), or introductory/concluding prose. Output raw source code or raw data structures only.
- Structured Schema: If code explanation is explicitly requested, ignore previous rules and output only a single minified JSON block using the exact schema (Do not add text outside the block):

```json
{
  "problem_summary": "1 sentence, max 20 words.",
  "suspected_root_cause": "1 sentence, max 20 words.",
  "evidence_needed": ["Max 3 items", "max 20 words each"],
  "proposed_fix": ["Max 3 items", "max 20 words each"],
  "risks_side_effects": ["Max 2 items tracking blast radius", "max 20 words each"],
  "done_when": ["Verifiable criteria", "max 20 words each"]
}
```

- Naming: Identifiers, comments, docs, and files in English.
- Domain Exceptions: Preserve official domain/regulatory terms (e.g., Chilean laws), source literals, seed data, and localized UI content in original language only if translation alters meaning. Surrounding code/docs stay English.
- Python Standards: Follow PEP 8 (style), PEP 257 (docstrings), PEP 484 (types verified with `mypy`), PEP 544 (protocols for ports), PEP 585 (built-in generics), PEP 604 (unions `X | None`), PEP 498 (f-strings), PEP 492 (`async`/`await`), PEP 654 (`ExceptionGroup`), and PEP 621 (`pyproject.toml`). Docstrings are required only for public modules, classes, and functions; internal implementations should use minimal inline code comments.
- Decouple use cases from infra; depend on ports, not concrete adapters.
- Avoid god objects; prefer small, focused classes.
- Extract repeated constants, mapping, and literals to shared helpers. Zero tolerance for duplication in source or tests.
- Use `src/payroll/application/errors.py` for business flow errors.
- Never use `assert` for production validation. Raise explicit errors. Fail loudly; no silent fallbacks.

## Financial and domain rules

- Strict precision: `Decimal` in Python, `NUMERIC` in PostgreSQL. Float-based monetary calculation is forbidden.
- Chilean payroll rules must be explicit and historically traceable.

## Scalability and safety

- Strict Operational Control: Prohibited from executing git commits, pushing branches, creating tickets/issues, or opening PRs autonomously. Requires explicit user command per turn.
- SemVer for versioning. English Conventional Commits for git messages.
- Follow 12-Factor (env vars for config, explicit deps, stateless/disposable processes, stdout/stderr logs).
- Thin HTTP/CLI/dashboard layers; orchestration stays in use cases.
- Validations must be explicit and close to boundaries or domain rules.

## Repository validation

Validate before finishing:

```bash
PATH=.venv/bin:$PATH make check
```

- `make check` runs: `lint` -> `dead-code` -> `typecheck` -> `duplicate-code-src` -> `duplicate-code-tests` -> `test` -> `test-cov`.
- Duplication thresholds: `src` <= 1%, `tests` <= 10%.
- Coverage target: 100% for `src`.
- Data reset for local validation: `make db-reset-data-real`.
