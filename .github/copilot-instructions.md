# Copilot instructions for `pf-payroll`

This repository follows a modular monolith with **DDD** and **hexagonal architecture**:

- `src/payroll/domain`: business rules, entities, value objects, and pure calculations.
- `src/payroll/application`: use cases, DTOs, ports, and application services.
- `src/payroll/infrastructure`: adapters for database, importers, providers, reporting, and logging.
- `src/payroll/interfaces`: API, CLI, and dashboard entrypoints.

## Engineering standards

- Apply **DRY, SOLID, Clean Code, and DDD** consistently in every change.
- Keep identifiers, internal comments, and authored documentation in **English** by default, including file names, modules, classes, methods, functions, variables, and constants.
- Preserve **official domain terms, legal or regulatory wording, source-system literals, seed or reference data values, and user-facing localized content** in their original language when translation would change meaning, break parsing, or reduce domain fidelity.
- When these exceptions are necessary, keep the surrounding code, comments, and explanations in **English**.
- Follow **PEP 8** for Python style and formatting so code stays compatible with the repository lint rules.
- Follow **PEP 257** docstring conventions for every Python artifact. Module, package, class, function, method, and script docstrings must all be present; no Python artifact should be left undocumented.
- Follow **PEP 484** typing consistently in public APIs, DTOs, ports, and application services; type safety is validated with `mypy`.
- Use **PEP 544** protocols for application ports and structural contracts between the application layer and adapters.
- Prefer **PEP 585** built-in generics like `list[str]` and `dict[str, Decimal]` instead of legacy `typing.List` or `typing.Dict`.
- Prefer **PEP 604** unions like `X | None` instead of `Optional[X]` and `Union[...]`.
- Prefer **PEP 498** f-strings for string interpolation unless another API requires a different formatting style.
- Use **PEP 492** `async` / `await` for I/O-bound workflows and adapters so asynchronous boundaries stay explicit.
- Treat **PEP 654** as the guideline for concurrent failure aggregation: use `ExceptionGroup` and `except*` only when multiple async or concurrent failures must be surfaced together.
- Keep package metadata in **PEP 621** `pyproject.toml` fields instead of legacy setup metadata files.
- Keep use cases decoupled from infrastructure. Application code should depend on **ports**, not concrete adapters.
- Prefer small, focused classes and helpers. Avoid growing repository or service "god objects".
- Extract repeated business constants and mapping logic to shared helpers instead of duplicating literals.
- Use **semantic application errors** from `src/payroll/application/errors.py` instead of ad-hoc generic errors whenever the failure is part of the business flow.
- Do not use `assert` for production validation. Raise explicit errors instead.
- Fail loudly and explicitly; do not hide invalid states with silent fallbacks or broad exception handling.

## Financial and domain rules

- Preserve strict financial precision with `Decimal` in Python and `NUMERIC` in PostgreSQL.
- Never introduce float-based monetary calculations.
- Keep Chilean payroll rules explicit and historically traceable.

## Scalability and safety

- Follow **SemVer** for project versioning: breaking changes increment major, backward-compatible features increment minor, and fixes increment patch.
- Write git commit messages in **English** and follow the **Conventional Commits** specification.
- Follow **Twelve-Factor** principles where they apply to this service: keep config in environment variables, declare dependencies explicitly, keep processes disposable and stateless, and write logs to standard output/error.
- Keep HTTP, CLI, and dashboard layers thin; orchestration belongs in application use cases.
- Prefer reusable mappers, normalization helpers, and shared constants when multiple flows use the same rule.
- Keep validations explicit and close to the boundary or domain rule they protect.
- Preserve existing behavior unless the change intentionally updates a business rule.

## Repository validation

Before finishing changes, validate with:

```bash
PATH=.venv/bin:$PATH make check
```

- `make check` runs all quality gates in sequence: `lint`, `dead-code`, `typecheck`, `duplicate-code-src`, `duplicate-code-tests`, `test`, and `test-cov`.
The repository expects **100% coverage** for `src`.
