# secrets/ (pf-payroll, local-only)

Module-scoped, machine-local secrets **and PII staging area** for
`pf-payroll`. Everything placed in this folder — including new subfolders —
is gitignored by default (see `.gitignore` right here): only this
`README.md` and the `.gitignore` itself can ever be committed.

## Why this exists (PII, not just credentials)

`pf-payroll` deals with real Chilean *liquidaciones de sueldo* (national ID/RUT,
salary, pension, health data). Per this repo's non-negotiable PII rule, no real PDF and
no JSON/fixture derived from a real PDF may ever live in a git-tracked
folder — not even temporarily, not even in a test fixtures directory. This
folder is the sanctioned local drop zone for that kind of material during
manual testing/dev work on the PDF-import flow (e.g. `update-pdf.md`), same
pattern already used by `pf-db/secrets/`.

It's also, like the other services in this ecosystem, the place for any
throwaway credential a one-off script under `scripts/` might need locally
(a target URL, a one-off API key) that isn't worth documenting anywhere
more permanent.

## Rules

- Never commit a real *liquidación* PDF, or any JSON extracted from one,
  anywhere in this repo — this folder or an out-of-repo path only.
- Test fixtures that *do* get committed (e.g. under `tests/`) must use
  synthetic/anonymized data, never a real payslip.
- Never hardcode a path to a file here inside committed code — read it from
  an environment variable.
