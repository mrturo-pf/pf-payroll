# Investigation: Scheduler OIDC hardening

Date: 2026-10-12
Status: investigating
Level: M
Scope: Replace static Scheduler API-key authentication with least-privilege OIDC authentication after separate design and validation.
Owner: `pf-payroll`
Related artifacts: [brief](../proposals/scheduler-oidc-hardening-brief.md) · [recommendation](../proposals/scheduler-oidc-hardening-recommendation.md) · [plan](../proposals/scheduler-oidc-hardening-plan.md) · [IMP-001 source](gcp-scheduler-credential-exposure.md) · [register](../../../../docs/proposals/ecosystem-improvement-register.md)
Superseded-by: none

## 1. Question

Can the Scheduler-to-service invocation path use a dedicated OIDC identity and
least-privilege audience/permission model, eliminating the static API key without
breaking scheduled operations or creating an implicit public endpoint?

## 2. Known boundary

`IMP-001` remediation is released: the exposed API key was contained, rotated or
removed as applicable, and the residual historical logging limitation was accepted.
OIDC is a separate hardening decision and must not be represented as unfinished
incident response.

The investigation must verify the actual Scheduler job, target URL, service
invocation policy, current caller identity, API route, environment, and rollback
path before designing a mutation. No credential value may be read or recorded.

## 3. Evidence required

- Scheduler job metadata and target authentication configuration;
- Cloud Run service IAM/invocation policy and environment;
- dedicated caller service account ownership and existing grants;
- exact OIDC audience and token subject requirements;
- target route behavior and API error contract;
- rollback path that does not reintroduce a long-lived shared key.

Use sanitized resource names, roles, URLs where safe, status codes, and hashes or
metadata only. Do not record tokens, headers, secret values, or real payloads.

## 4. Open questions

1. Is the target endpoint suitable for OIDC without changing public API semantics?
2. Should the Scheduler caller invoke a private Cloud Run service or an authenticated
   public endpoint with invoker permission?
3. What audience and service-account ownership are approved?
4. How is a failed scheduled execution detected and rolled back safely?

## 5. Current conclusion

Prepare a separate OIDC design and validation matrix. Do not mutate Scheduler, IAM,
Cloud Run ingress, or API-key configuration until the design is explicitly approved.
