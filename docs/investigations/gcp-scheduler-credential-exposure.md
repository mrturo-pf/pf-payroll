# Investigation: GCP Scheduler credential exposure

Date: 2026-10-09
Status: investigating
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
- No rotation, revocation, IAM change, Scheduler update, or production mutation has been performed as part of this investigation.

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

- [ ] Security owner approves immediate rotation/revocation.
- [ ] Owner of `gcp-scheduler-runner` is identified.
- [ ] Exposure window and log-review window are defined.
- [ ] Rotation is preferred over revocation-only, unless the credential is no longer needed.
- [ ] OIDC hardening is accepted as a separate follow-up or explicitly deferred.
- [ ] Raw inventory and Cloud Shell history cleanup is authorized and completed.
- [ ] Post-rotation Scheduler execution is validated.

## 7. Closure criteria

Close this investigation only when:

- the exposed credential is rotated or revoked;
- no active consumer depends on the compromised value;
- logs were reviewed for unexpected use during the exposure window;
- raw copies and shareable artifacts are removed or redacted;
- Cloud Shell history handling is complete or explicitly documented as unavailable;
- the Scheduler job executes successfully with the replacement authentication;
- OIDC follow-up is either tracked as a separate proposal or explicitly declined;
- the remediation record contains operator, timestamp, result, and sanitized evidence.

## 8. Current conclusion

Proceed with independent security remediation. Do not include credential rotation
inside the implementation mechanics of the `pf-svc-income` rename, but keep
incident closure as a prerequisite for creating target resources or deploying
the renamed service. No destructive or production command is authorized by this
investigation document alone.
