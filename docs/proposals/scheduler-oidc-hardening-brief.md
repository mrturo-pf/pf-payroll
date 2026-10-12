# Brief: Scheduler OIDC hardening

Date: 2026-10-12
Status: investigating
Level: M
Scope: Replace static Scheduler API-key authentication with least-privilege OIDC authentication.
Related artifacts: [investigation](../investigations/scheduler-oidc-hardening.md) · [recommendation](scheduler-oidc-hardening-recommendation.md) · [plan](scheduler-oidc-hardening-plan.md) · [IMP-001 source](../investigations/gcp-scheduler-credential-exposure.md) · [register](../../../../docs/proposals/ecosystem-improvement-register.md)
Approved: pending explicit owner approval
Superseded-by: none

## Objective

Design and validate an authenticated Scheduler-to-Cloud Run invocation path using a
dedicated caller identity, least-privilege invoker access, and a precise OIDC
audience, without reintroducing static credentials or silently changing public API
contracts.

## Non-objectives

- Reopening the released `IMP-001` credential incident response;
- changing application routes or response contracts without separate approval;
- making the service publicly accessible as a workaround;
- creating IAM resources or changing Scheduler/Cloud Run configuration in this phase;
- recording tokens, headers, secrets, or real request data.

## Required outcomes

- Current Scheduler and Cloud Run invocation inventory;
- approved caller identity and resource-level permission model;
- audience, route, environment, and ingress contract;
- synthetic authentication and failure validation;
- rollback and monitoring procedure.

## Acceptance criteria

1. Scheduler can invoke only the intended endpoint and service.
2. No long-lived API key is required by the scheduled path.
3. OIDC claims/audience are explicit and tested.
4. Failed invocation, expired token, wrong audience, and unauthorized caller cases
   have stable evidence and ownership.
5. Rollback does not depend on restoring an exposed credential.
