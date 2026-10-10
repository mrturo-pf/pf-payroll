# Brief: remediate exposed Scheduler credential

Date: 2026-10-10
Status: approved
Level: L
Scope: Security remediation for the credential exposed in GCP Scheduler inventory 01.
Related artifacts: [investigation](../investigations/gcp-scheduler-credential-exposure.md) · [register](../../../../docs/proposals/ECOSYSTEM-IMPROVEMENT-REGISTER.md)
Approved: 2026-10-10 by a0a11b7 (explicit operational authorization)
Superseded-by: none

## Objective

Contain and remediate the credential exposed in the first GCP inventory for
`gcp-scheduler-runner`. The remediation must prevent continued use of the
compromised value, determine whether it was misused, remove exposed copies, and
prove that the scheduled operation still works with replacement authentication.

## Non-objectives

- Renaming `pf-payroll` or creating `pf-svc-income` resources.
- Changing payroll schemas, APIs, or application code unrelated to this credential.
- Performing broad IAM cleanup unrelated to the exposed credential.
- Implementing OIDC authentication without a separate approved design and test.
- Recording any credential, token, raw log, request body, or production payload in Git.

## Affected boundaries

| Responsibility | Owner |
| --- | --- |
| Incident record and PF coordination | `pf-payroll` / `pf-base` |
| `gcp-scheduler-runner` service and job | Service owner to be confirmed |
| Scheduler configuration | GCP operations owner |
| Credential storage and rotation | Owner of the target service/configuration |
| Rename cutover gate | `pf-payroll` rename plan |

## Required observable outcomes

- The exposed credential is rotated or revoked through an authorized procedure.
- No active consumer depends on the compromised value.
- Logs are reviewed for unexpected use during the exposure window.
- Raw inventory copies and shell-history artifacts are contained, removed, or
  redacted according to the approved incident procedure.
- The scheduled operation succeeds with replacement authentication.
- No replacement secret value appears in Git, chat, logs, or shared artifacts.
- The rename plan has a sanitized closure result and no longer treats this incident
  as an unresolved blocker unless evidence requires it.

## Constraints

- Security containment takes priority over the parent rename schedule.
- Registration does not authorize rotation, revocation, or production mutation.
- All operational actions require explicit authorization from the responsible owner.
- Evidence must remain sanitized and access-controlled.

## Questions for recommendation

1. Is rotation sufficient, or must the credential be revoked and replaced entirely?
2. What is the authorized exposure and log-review window?
3. Who owns `gcp-scheduler-runner` and its secret configuration?
4. Should OIDC authentication be a separate follow-up after immediate containment?
5. What evidence is sufficient to close the incident without retaining sensitive data?
