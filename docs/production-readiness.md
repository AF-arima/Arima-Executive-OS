# Production readiness and smoke tests

This runbook is the controlled release gate for Arima Executive OS. It does
not authorize a deployment, provider activation, Telegram activation, or
customer-facing market data.

## Pre-deployment gate

- Confirm `main` is the approved commit and the working tree is clean.
- Confirm `python3 -m alembic heads` returns exactly one head.
- Run Ruff, focused mypy, the full backend suite, the OpenAPI regression, and
  `git diff --check`.
- Verify the frontend repository's lint, typecheck, production build, metadata,
  robots, sitemap, canonical domain, and HTTPS redirect separately. Frontend
  source is not part of this backend repository.
- Verify production uses explicit HTTPS `FRONTEND_URL` and `CORS_ORIGINS`,
  explicit `TRUSTED_HOSTS`, secure cookies, and non-development secrets.
- Keep `AI_EXECUTION_ENABLED=false` until a concrete adapter, approved
  model, server-side credential, timeout, and error contract pass a
  separate provider readiness review. When AI execution is enabled, do not
  deploy while `DEFAULT_PROVIDER=mock`.
- Keep Telegram disabled unless its server-side credentials, verified identity
  mappings, webhook registration, and operational owner are explicitly
  approved.
- Keep customer market prices disabled. A market credential is not a display
  or redistribution entitlement.

## Non-destructive post-deployment smoke tests

Run these tests with dedicated production smoke identities and workspaces. Do
not use customer records and do not mutate or delete existing customer data.

| Test | Request or action | Expected result | Security expectation |
| --- | --- | --- | --- |
| Readiness | `GET /health/ready` | `200` with `status=ready` and `database=ok` | No database URL, schema, or credential is returned. |
| Authentication | Sign in, refresh once, then replay the old refresh token | Sign-in and first refresh succeed; replay fails | Refresh family is revoked and error text is generic. |
| Workspace isolation | Use workspace A's user to request a workspace B resource | `403` or scoped `404` | No workspace B identifier or content is returned. |
| Dashboard | Load the authenticated dashboard | `200` with only the actor's scoped data | Browser contains no provider or Telegram credential. |
| AI workflow | Execute one approved briefing in a dedicated workspace | Durable conversation, run, output, and terminal status | Explicit membership, role, agent grant, and tool policy are enforced. |
| Audit chain | Inspect the smoke run through the governed audit interface | User, workspace, conversation, agent, run, evidence, tools, output, and action can be reconstructed | No hidden prompt or credential is exposed. |
| Telegram disabled | `POST /api/v1/telegram/webhook` with an invalid secret while disabled | `401` | No message is processed and no identity is inferred from Telegram alone. |
| Market availability | Authenticated `GET /api/v1/market/availability` | All symbols unavailable; no prices or provider identity | Missing auth is `401`; response is non-price and fail-closed. |
| Market route absence | Probe `/price`, `/quote`, `/candles`, and `/time-series` variants | `404` | No provider call occurs. |
| OpenAPI | Fetch `/openapi.json` and compare the approved route set | Expected API, Telegram webhook, and health routes only | No credential schema or customer market-data route appears. |
| Frontend | Load canonical HTTPS domain in a clean browser | Public page and authenticated entry load without console/network errors | HTTP redirects to HTTPS; private routes are not indexed or cached publicly. |
| Secret leakage | Scan response headers, HTML, public bundles, logs, and errors for secret markers | No secret values | Authorization headers, keys, bot tokens, database URLs, and private source credentials are absent. |

## Live checklist

Before declaring the service live, record evidence that the approved deployment
and migration succeeded, readiness is `200`, authentication and tenant tests
passed, the governed AI workflow used a non-mock production adapter, audit and
failure logs are usable, the frontend build and canonical-domain checks passed,
and no secrets leaked. Telegram must be either intentionally disabled or
explicitly approved and configured. Customer-facing market data must remain
disabled until Phase 3.2's licensing, identity, freshness, entitlement, and
security gates are approved.
