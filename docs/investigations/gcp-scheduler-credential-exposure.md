# Investigation: GCP Scheduler credential exposure

Date: 2026-10-09
Status: validated
Level: L
Scope: Independent security incident involving a credential exposed in the first GCP inventory for `gcp-scheduler-runner`.
Owner: `pf-payroll` investigation record; operational remediation belongs to the owner of `gcp-scheduler-runner`.
Related artifacts: [pf-svc-income rename investigation](pf-svc-income-renaming.md) · [ecosystem workflow](../../../../docs/ecosystem-improvement-workflow.md)
Superseded-by: none

## 1. Investigation question

What is the impact of the literal Scheduler `X-API-Key` exposed in inventory 01,
and what authorized remediation is required before the PF ecosystem rename can
enter its mutation phase?

This incident is intentionally separate from the `pf-payroll` → `pf-svc-income`
rename. It must not wait for the rename implementation schedule, and the rename
must not be used as a reason to delay security remediation.

## 2. Scope

### In scope

- the exposed Scheduler credential and its exposure window;
- the enabled job `gcp-scheduler-runner-chile`;
- target service `gcp-scheduler-runner`;
- logs and consumers relevant to the credential;
- raw inventory archives, Cloud Shell history, and shareable artifacts;
- credential rotation/revocation and post-rotation validation;
- evaluation of Scheduler OIDC authentication as a follow-up design.

### Out of scope

- renaming `pf-payroll` or creating `pf-svc-income` resources;
- changing the payroll database schema;
- changing unrelated API keys or secrets;
- implementing OIDC before its owner approves and tests that design;
- broad IAM cleanup unrelated to the exposed credential.

## 3. Confirmed facts

- The first GCP inventory contained a literal `X-API-Key` in exported output.
- The credential must be treated as compromised until rotation or revocation is complete.
- The enabled Scheduler job is:

  ```text
  gcp-scheduler-runner-chile
  ```

- The job runs at `30 4 * * *` UTC and sends `POST` to:

  ```text
  https://gcp-scheduler-runner-yqd4p7fzaa-uc.a.run.app/execute
  ```

- The Scheduler job does not directly target `pf-payroll`.
- The inventory package and Cloud Shell history require separate handling; deleting a local archive does not revoke a credential.
- No credential value is stored in this investigation or in the repository.
- The owner explicitly authorized remediation on 2026-10-10.
- A dedicated runtime service account was created for `gcp-scheduler-runner` and the owner received scoped `roles/iam.serviceAccountUser` access.
- Cloud Run now serves revision `gcp-scheduler-runner-00020-j8v` with the dedicated runtime identity.
- The API key was rotated without recording its value; the enabled Scheduler job was updated atomically with replacement headers.
- `/health` returned `ok` and a manual Scheduler execution was accepted.
- Cloud Logging review is blocked by VPC Service Controls; an empty log result must not be interpreted as absence of use.

## 4. Required evidence

Obtain only what is necessary and avoid copying secret values:

- Scheduler job metadata, target URI, method, and state;
- `gcp-scheduler-runner` request logs during the exposure window;
- source IPs and user agents associated with use of the exposed credential, reviewed as sensitive data;
- whether the target service accepts API-key authentication, IAM authentication, or both;
- whether any other job, script, Postman environment, repository, or operator uses the same credential;
- confirmation that raw inventory 01 and any archive containing it are identified and contained.

Do not export request bodies, response bodies, headers, authorization values, or
other credentials into the repository.

## 5. Remediation options

### Option A — Rotate/revoke the existing key

Required minimum remediation:

1. Obtain explicit operational approval.
2. Rotate or revoke the exposed key in the owning service/configuration.
3. Update the enabled Scheduler job or target configuration atomically.
4. Validate the next scheduled execution and target health.
5. Redact/remove raw inventory artifacts and Cloud Shell history containing the literal value.
6. Record timestamps, operator, result, and evidence without recording the new key.

**Execution record 2026-10-10:** owner authorization was received; a dedicated
runtime identity was created; Cloud Run was updated to revision
`gcp-scheduler-runner-00020-j8v`; the API key was rotated without recording its
value; Scheduler was updated using `--update-headers`; `/health` returned `ok`;
and a manual Scheduler execution was accepted. Cloud Logging review remains
blocked by VPC Service Controls.

### Option B — Replace static API-key authentication with Scheduler OIDC

Evaluate as a follow-up hardening change:

- configure Scheduler to obtain an OIDC token for the target service account;
- grant only the required invoker permission to the target Cloud Run service;
- remove the static `X-API-Key` dependency after successful validation;
- test the scheduled execution and rollback path;
- document the target service account and audience without exposing credentials.

OIDC is recommended as a separate design/remediation decision, not an excuse to
delay rotation of the currently exposed key.

## 6. Decisions required

- [x] Security owner approves immediate rotation/revocation.
- [x] Owner of `gcp-scheduler-runner` is identified.
- [x] Exposure and log-review window reviewed from available metadata; 31 records had `200`, while 31 records lacked HTTP status and remain inconclusive.
- [x] Rotation is preferred over revocation-only and completed.
- [x] OIDC hardening deferred as a separate follow-up (`IMP-009`).
- [x] Raw inventory and Cloud Shell history cleanup is authorized and completed.
- [x] Post-rotation Scheduler execution is validated at the job-dispatch level and `/health` returned `ok`.

## 7.1 Execution evidence (2026-10-10)

Sanitized execution evidence was reviewed on 2026-10-10 and removed after local
validation. The repository retains only the summarized results below; no evidence
directory or raw metadata is retained.
The manifest confirms no secrets or raw payloads were included. The evidence shows:

- Cloud Run revision `gcp-scheduler-runner-00020-j8v` is `Ready=True`;
- the dedicated runtime service account is configured;
- Scheduler remains `ENABLED` with the expected schedule and target;
- OIDC and OAuth are not configured;
- `/health` returned `ok`;
- log metadata was available: 62 metadata records were collected, including 31
  records with HTTP status `200`; records without a status remain inconclusive;
- no request bodies, response bodies, or credential values were collected.

This evidence validates the replacement configuration and health, but it does not
close the exposure-window review or artifact cleanup.

## 8. Owner acceptance of residual log limitation

On 2026-10-10, a0a11b7 accepted the residual historical log limitation:
Cloud Logging metadata was reviewed, but 31 of the 62 records lacked an HTTP
status and further attribution was blocked by VPC Service Controls.

This limitation does not indicate an active credential. The compromised API key
was rotated, Scheduler was updated, Cloud Run was validated, artifacts were
cleaned, and Cloud Shell history was cleared. No further GCP command is required
for `IMP-001`; OIDC hardening remains separately deferred as `IMP-009`.

## 9. Closure criteria

Close this investigation only when:

- [x] the exposed credential is rotated or revoked;
- [x] no active consumer can use the compromised value after rotation;
- [x] logs were reviewed for unexpected use during the exposure window; available metadata had no non-200 status, while status-missing records remain inconclusive;
- [x] raw copies and shareable artifacts are removed or redacted;
- [x] Cloud Shell history handling is complete or explicitly documented as unavailable;
- [x] the Scheduler job executes with replacement configuration at the dispatch level;
- [x] OIDC follow-up is tracked as `IMP-009` and explicitly deferred from immediate containment;
- [x] the remediation record contains operator authorization, execution date, result, and sanitized evidence.

## 10. Current conclusion

`IMP-001` is closed after immediate containment, credential rotation, Scheduler
and Cloud Run validation, artifact cleanup, Cloud Shell history cleanup, and
explicit owner acceptance of the residual historical log limitation.

OIDC remains a separate deferred hardening item tracked as `IMP-009`.
