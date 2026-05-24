# Copilot instructions for `pf-payroll`

This repository follows a modular monolith with **DDD** and **hexagonal architecture**:

- `src/payroll/domain`: business rules, entities, value objects, and pure calculations.
- `src/payroll/application`: use cases, DTOs, ports, and application services.
- `src/payroll/infrastructure`: adapters for database, importers, providers, reporting, and logging.
- `src/payroll/interfaces`: API, CLI, and dashboard entrypoints.

## Engineering standards

- Apply **DRY, SOLID, Clean Code, and DDD** consistently in every change.
- Follow **PEP 8** for Python style and formatting so code stays compatible with the repository lint rules.
- Follow **PEP 257** docstring conventions for every Python artifact. Module, package, class, function, method, and script docstrings must all be present; no Python artifact should be left undocumented.
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

- Keep HTTP, CLI, and dashboard layers thin; orchestration belongs in application use cases.
- Prefer reusable mappers, normalization helpers, and shared constants when multiple flows use the same rule.
- Keep validations explicit and close to the boundary or domain rule they protect.
- Preserve existing behavior unless the change intentionally updates a business rule.

## Repository validation

Before finishing changes, validate with:

```bash
PATH=.venv/bin:$PATH make lint
PATH=.venv/bin:$PATH make typecheck
PATH=.venv/bin:$PATH make test-cov
```

The repository expects **100% coverage** for `src/payroll`.
