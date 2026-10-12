# Recommendation: Scheduler OIDC hardening

Date: 2026-10-12
Status: designed
Level: M
Scope: Replace static Scheduler API-key authentication with least-privilege OIDC authentication.
Related artifacts: [brief](scheduler-oidc-hardening-brief.md) · [investigation](../investigations/scheduler-oidc-hardening.md) · [plan](scheduler-oidc-hardening-plan.md) · [IMP-001 source](../proposals/scheduler-credential-exposure-remediation-recommendation.md) · [register](../../../../docs/proposals/ecosystem-improvement-register.md)
Approved: pending explicit owner approval
Superseded-by: none

## 1. Recommendation

Use a dedicated service account as the Cloud Scheduler OIDC caller and grant only
resource-level invocation permission on the intended Cloud Run service. Configure a
strict audience for the target service URL or approved custom audience, and validate
the exact scheduled route separately.

Keep the application route and public API contract unchanged unless evidence shows
that the current endpoint cannot support authenticated Scheduler invocation. Do not
use a shared runtime/deployer identity, project-wide broad grants, or a static API
key fallback.

## 2. Security contract

The caller identity, target service, audience, environment, route, and invoker grant
must be explicit. Token validation is delegated to Cloud Run/IAM where possible;
application-level token parsing is not added without a demonstrated requirement.

If the service is made private, validate ingress and any approved intermediary before
cutover. If the service remains publicly reachable, authentication must still be
required and unauthorized requests must be rejected consistently.

## 3. Rollout and rollback

1. Inventory the live job and target without mutation.
2. Create or select the dedicated caller identity only after approval.
3. Grant resource-level invocation permission.
4. Configure OIDC audience and run synthetic authorized/unauthorized tests.
5. Observe at least one scheduled execution and failure case.
6. Remove the legacy API-key path only after successful evidence and approval.

Rollback disables the OIDC schedule/configuration and restores the prior approved
non-secret routing behavior only if explicitly authorized; it must not restore an
exposed or untracked credential.

## 4. Rejected alternatives

- Shared service account: rejected because it weakens attribution and least privilege.
- Project-wide invoker grant: rejected because the scheduler needs one target.
- Static API-key fallback: rejected because it recreates the original credential risk.
- Unauthenticated public endpoint: rejected because it bypasses the intended control.

## 5. Decision gate

Approve the caller identity, audience, ingress model, route, validation window, and
rollback procedure before any IAM, Scheduler, Cloud Run, or secret mutation.
