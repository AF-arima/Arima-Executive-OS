# Phase 4 governed intelligence foundation

Phase 4 extends the existing provider, agent, orchestration, tool, memory, and
voice foundations. It does not add a second AI stack or a customer-facing
market-data surface.

## Durable ownership and audit chain

Every governed workflow binds an existing `agent_runs` row to one workspace
and one verified Arima user in `ai_workspace_runs`. An explicit active
`workspace_agent_grants` row is required before the agent can run. The binding
is immutable and complements the existing conversation owner and run trigger
checks.

The durable chain is:

`user -> workspace -> conversation -> agent -> run -> retrieved context -> tool executions -> output`

`AuditChainService` reconstructs that chain only after checking the requesting
user's exact workspace membership and run ownership.

## Knowledge and retrieval

Approved inputs are normalized into workspace-owned sources, versioned
documents, and deterministic chunks. Documents require source provenance and
timezone-aware observation timestamps. Freshness-required sources require a
maximum age. Retrieval scopes every joined table to the same workspace,
rejects missing run ownership, skips stale or provenance-less records, and
persists every returned chunk as `ai_retrieved_contexts` evidence.

Provider credentials are not valid provenance and credential-bearing source
URIs are rejected.

## Executive workflows

`ExecutiveWorkflowService` creates the existing durable conversation, user
message, and agent run records; binds the run to its workspace; records
retrieval evidence; invokes the existing orchestration engine; stores the
assistant output; and completes or fails the run durably. Tool authorization,
approvals, and execution auditing remain owned by the existing orchestration
and tool layers.

## Telegram transport

Telegram is disabled by default. Its webhook accepts Telegram's standard
secret-token header and message update shape. The transport requires both a
server-side webhook secret and a verified
mapping from the Telegram user/chat pair to an active, verified Arima user and
workspace membership. Telegram identity is never sufficient authorization by
itself. The normal agent and workspace checks still run.

Accepted updates persist incoming text, response text, Telegram update/chat
and message identifiers, timestamps, processing status, errors, conversation,
run, user, workspace, and identity. Telegram update IDs are unique, making
replays idempotent. Bot and webhook credentials use secret settings and are
never persisted in these records or returned to clients.

## Exposure boundary

The Telegram webhook is the only Phase 4 endpoint. It is server-authenticated,
resolves an exact workspace from a verified identity, re-runs normal Arima
authorization, and exposes no orchestration internals. No customer price,
quote, or time-series endpoint is added. Phase 2 and Phase 3.1 market
availability and fail-closed licensing behavior remain unchanged.
