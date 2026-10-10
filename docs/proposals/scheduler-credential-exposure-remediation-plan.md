# Plan: remediate exposed Scheduler credential

Date: 2026-10-10
Status: in progress
Level: L
Scope: Controlled remediation of the exposed GCP Scheduler credential.
Related artifacts: [brief](scheduler-credential-exposure-remediation-brief.md) · [recommendation](scheduler-credential-exposure-remediation-recommendation.md) · [investigation](../investigations/gcp-scheduler-credential-exposure.md)
Approved: 2026-10-10 by a0a11b7 (explicit operational authorization)
Superseded-by: none

> Containment was authorized and executed on 2026-10-10. Artifact cleanup, exposure-window
> log review, and the OIDC follow-up remain pending; no further mutation is authorized
> by this plan without the applicable owner approval.

## 1. Status and gates

- [x] Existing investigation created and sanitized.
- [x] Immediate remediation recommendation prepared.
- [x] Security/service owner identified.
- [x] Containment authorization recorded.
- [ ] Exposure and log-review windows approved; Cloud Logging is blocked by VPC Service Controls.
- [x] Credential rotated or revoked.
- [x] Scheduler configuration updated atomically.
- [x] Replacement execution validated at dispatch level; `/health` returned `ok`.
- [ ] Exposed artifacts contained, removed, or redacted.
- [x] Incident evidence recorded without sensitive values.
- [x] Rename register entry reconciled.
- [ ] OIDC follow-up accepted, deferred, or rejected separately.

## 2. Implementation slices

### Slice 1 — Authorize and identify

- Confirm the owner of `gcp-scheduler-runner` and its credential configuration.
- Confirm the enabled job, target, authentication mode, and approved exposure window.
- Record authorization without copying the credential value.

**Exit criteria:** owner, scope, and approval are recorded.

### Slice 2 — Contain the credential

- Rotate the credential, or revoke it and issue a replacement if required.
- Update the Scheduler configuration atomically.
- Do not print, commit, or paste the replacement value into artifacts.

**Exit criteria:** the compromised value is unusable and the replacement is configured.

### Slice 3 — Validate scheduled execution

- Run an authorized immediate validation or wait for the next scheduled execution.
- Verify target health and expected response behavior.
- Record only status, timestamp, and sanitized evidence.

**Exit criteria:** the job succeeds with replacement authentication.

### Slice 4 — Review exposure and clean artifacts

- Review authorized request metadata during the exposure window.
- Inventory raw files, archives, shell history, and shared artifacts.
- Remove or redact exposed copies according to the incident procedure.
- Escalate unexplained use rather than embedding raw evidence here.

**Exit criteria:** cleanup is complete or an explicit exception is recorded.

### Slice 5 — Close and separate follow-up

- Update the investigation with operator, timestamp, result, and sanitized evidence.
- Set this plan and the register entry to the correct lifecycle status.
- Update the `pf-svc-income` rename gate: resolved, accepted with rationale, or still
  blocked.
- Create a separate OIDC proposal if the hardening decision is authorized.

**Exit criteria:** incident closure and any OIDC follow-up are independently tracked.

## 3. Validation commands and evidence

The exact commands depend on the service owner and approved GCP access. Record:

- authorized Scheduler metadata query result;
- credential rotation/revocation result without the value;
- target health and scheduled execution result;
- artifact cleanup result;
- sanitized log-review conclusion;
- operator and timestamps.

Do not place raw headers, request/response bodies, API keys, connection strings,
production payloads, or personal data in Git.

## 4. Rollback and failure handling

If replacement authentication fails:

1. stop further mutation;
2. preserve the incident state and error evidence without secrets;
3. use only the owner-approved rollback configuration;
4. validate the job before closing the change;
5. escalate if the old credential cannot be safely restored because it is compromised.

Rollback must never restore a known-compromised credential merely to make the job
pass.

## 5. Change log

| Date | Status | Change | Evidence |
| --- | --- | --- | --- |
| 2026-10-10 | in progress | Owner authorization recorded; dedicated runtime identity created; Cloud Run updated to `gcp-scheduler-runner-00020-j8v`; API key rotated; Scheduler updated; `/health` returned `ok`; manual job execution accepted. | Sanitized command results; no credential value recorded. |

## 6. Release record

- Commit: pending
- Workflow run: pending
- Operator: a0a11b7
- Rotation/revocation timestamp: 2026-10-10
- Scheduled execution result: manual dispatch accepted; log confirmation pending VPC Service Controls resolution
- Incident closure: pending artifact cleanup and exposure-window review
- OIDC decision: pending separate decision
