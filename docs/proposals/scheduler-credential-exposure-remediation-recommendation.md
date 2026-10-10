# Recommendation: remediate exposed Scheduler credential

Date: 2026-10-10
Status: approved
Level: L
Scope: Immediate containment and remediation of the credential exposed in GCP Scheduler inventory 01.
Related artifacts: [brief](scheduler-credential-exposure-remediation-brief.md) · [investigation](../investigations/gcp-scheduler-credential-exposure.md) · [register](../../../../docs/proposals/ECOSYSTEM-IMPROVEMENT-REGISTER.md)
Approved: 2026-10-10 by a0a11b7 (explicit operational authorization)
Superseded-by: none

## 1. Executive recommendation

Immediately rotate or revoke the exposed credential through the owning GCP/service
procedure, update the enabled Scheduler configuration atomically, and validate the
next scheduled execution. Treat the exposed value as compromised until that work is
complete.

After immediate containment, perform a bounded log review and artifact cleanup. Treat
OIDC migration as a separate hardening decision: it may replace static API-key
authentication later, but it must not delay rotation of the compromised credential.

## 2. Rejected alternatives

### Wait for the service rename

Rejected. The incident is independent of `pf-payroll` → `pf-svc-income` and waiting
would extend the exposure window.

### Delete the inventory file only

Rejected. Deleting a local copy does not revoke a credential or remove other copies,
including shell history, archives, or operational exports.

### Revoke without validating the scheduled operation

Rejected unless the credential is no longer needed. If the job remains required,
replacement authentication must be configured and tested in the same controlled
change.

### Implement OIDC before rotation

Rejected as an execution order. OIDC may be the long-term design, but immediate
containment must not depend on a larger authentication migration.

## 3. Remediation contract

### Immediate containment

1. Obtain explicit approval from the security and service owners.
2. Identify the owning service, secret/configuration location, enabled Scheduler job,
   target URI, and exposure window using metadata only.
3. Rotate the credential, or revoke it and issue a replacement if rotation is not
   supported.
4. Update the Scheduler target atomically without recording the replacement value.
5. Validate target health and the next scheduled execution.

### Evidence and cleanup

1. Review authorized request metadata for unexpected use during the exposure window.
2. Identify raw inventory, archives, shell history, and shared artifacts containing
   the exposed value.
3. Remove or redact those artifacts according to the approved incident procedure.
4. Record operator, timestamps, actions, result, and sanitized evidence only.

### Follow-up hardening

Evaluate Scheduler OIDC separately:

- Scheduler obtains an OIDC token for an approved service account;
- target service grants only the required invoker permission;
- audience and service-account ownership are documented without secrets;
- scheduled execution and rollback are tested;
- static API-key authentication is removed only after successful validation.

The OIDC work must receive its own register entry or proposal if authorized.

## 4. Ownership and sequencing

| Order | Owner | Work | Gate |
| ---: | --- | --- | --- |
| 1 | Security/service owner | Approve containment and identify credential owner | Explicit authorization |
| 2 | GCP operations owner | Rotate/revoke and update Scheduler atomically | Replacement configuration accepted |
| 3 | Security/service owner | Review use and contain exposed artifacts | Sanitized evidence recorded |
| 4 | Service owner | Validate scheduled execution | Successful health/job validation |
| 5 | `pf-payroll` | Update rename gate and register resolution | Incident status reconciled |
| 6 | Security owner | Decide whether to authorize OIDC hardening | Separate decision |

## 5. Validation matrix

| Area | Validation | Acceptance |
| --- | --- | --- |
| Credential | Authorized metadata confirms old value is no longer usable | Rotation/revocation succeeds |
| Scheduler | Job remains enabled with replacement configuration | Next execution succeeds |
| Target service | Health and authenticated request checks | Expected status and behavior |
| Exposure review | Authorized logs and artifact inventory | No unexplained use remains, or incident is escalated |
| Repository hygiene | Sanitized repository and proposal review | No credential, token, raw log, or payload committed |
| Rollback | Replacement configuration rollback is documented | Owner can restore the approved safe configuration |

## 6. Cost and operational impact

- No new permanent infrastructure is required for immediate rotation/revocation.
- OIDC may add IAM configuration and operational maintenance but avoids static-key
  exposure; evaluate it separately against the current API-key model.
- Log review and artifact cleanup create operational work but no material cloud-cost
  increase is expected.

## 7. Execution status

Immediate containment was executed on 2026-10-10: the runtime identity was replaced
with a dedicated service account, the API key was rotated, Scheduler was updated,
Cloud Run health returned `ok`, and a manual Scheduler dispatch was accepted.
Exposure-window log review remains blocked by VPC Service Controls; artifact cleanup
and the OIDC decision remain open.