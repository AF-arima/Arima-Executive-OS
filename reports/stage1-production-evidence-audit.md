# ARIMA OS — Stage 1 Production Evidence Audit (Corrected Targeted Report)

**Audit type:** read-only repository evidence audit  
**Scope:** A — Verified news/macro data; B — compliance/regulatory technical controls; C — risk-management re-validation  
**Audit date:** 2026-08-21  
**Repository:** `Arima-Executive-OS`  

This is a corrected targeted audit report. It corrects only the four findings
identified in the follow-up adversarial review: risk-input severity, confidence
classification wording, static withdrawal route matching, and reproducible
snapshot anchoring.

## Audit method and evidence boundary

This report is based only on static inspection of the repository: source files,
configuration templates, migration files, documentation, tests, git status, and
git history. No tests, application processes, migrations, database commands,
provider calls, network requests, browser actions, or production systems were
accessed.

The working tree is not clean. It contains uncommitted modifications and
untracked files, including the quant, ledger, withdrawal, portfolio, and risk
workstreams. Therefore a repository label or a test count supplied in prior
conversation cannot be treated as a released revision or production evidence.

Secrets and credential values were not inspected or reproduced.

### Snapshot anchor

- Audited `HEAD`: `2d5d15e9ae849cdf33ae4d78001311f819ac5dfc`
- Branch at inspection: `main`
- Working tree: not clean; application changes and untracked implementation and
  test files were preserved and not reviewed as a release.
- No tests, migrations, scripts, database access, application startup, provider
  calls, network requests, browser actions, or deployment were performed.
- A clean audit-only snapshot base was safely created containing only the
  report: branch `audit/stage1-corrected-evidence`, initial report-only commit
  `04c11e3`. This corrected report is preserved in the subsequent audit-only
  correction commit on the same branch. Both commits are metadata only and are
  not releases or implementation milestones.

Confidence tags used below:

- **Established:** directly supported by source/configuration/test artifacts.
- **Likely:** strongly suggested by implementation, but not established as a
  production fact.
- **Uncertain:** cannot be established from repository evidence and requires
  direct verification.

## 1. Executive Finding

The repository supports a meaningful **code-level, fail-closed foundation** for
market-data verification, historical OHLC normalization, structural evidence,
session evaluation, ledger accounting, withdrawal reservation, and disabled
QTrade execution.

The repository does **not** establish that ARIMA is production-ready for live
signals, live trading, customer capital, or operational withdrawals.

The highest-severity technical finding is the authoritative-risk-input gap.
`PortfolioRiskProvider.snapshot` supplies zero/default values for current
exposure, realized P&L, unrealized P&L, daily loss, strategy exposure, and
concentration where authoritative values are not derived. Unlike the missing
news path, which fails loudly as `NEWS_BLOCKED`, these missing inputs can allow
`RiskEngine` validation to proceed against zero/default values. This is a
potentially silent risk-control failure. The risk system must not be considered
production-risk complete.

The second major finding is the reported news blocker: there is no approved,
verified high-impact news or macro provider implementation/configured adapter
in the repository. `NotConfiguredNewsProvider` explicitly returns
`NOT_CONFIGURED`, and `GatewayQLabResearchProvider` raises `NEWS_BLOCKED`
before signal normalization when news is not clear. The older `NewsConnector`
is explicitly a `DeterministicMockConnector`; it is not provider evidence.

The third major finding is compliance/regulatory non-verification. Technical
authentication, authorization, ledger, withdrawal, audit, and disabled-execution
controls do not establish FCA authorization, KYC/AML satisfaction, client-money
or custody compliance, UK GDPR compliance, or permission to provide regulated
investment/advisory services.

The supplied completion labels are therefore only partially supported:

- The **news blocker is supported** by repository evidence.
- The **market-data and historical-OHLC code path is present**, but real provider
  authentication, entitlement, latency, retention, licensing, and production
  reliability are not established.
- The **session and structural-evidence contracts exist**, but they are not
  production verification of a complete ARIMA strategy or a historical replay
  system.
- The **risk contract exists**, but its production risk completeness is not
  established. The portfolio risk adapter supplies zero current exposure and
  default zero P&L/daily loss/strategy exposure/concentration rather than
  deriving all of those values from authoritative sources. This can silently
  weaken risk controls and is the highest-severity technical finding.
- **QTrade live execution is disabled** in code. This is established as a code
  gate, not as a deployment or operational control proof.
- The claimed **427 passed** count was not independently run, as prohibited by
  this audit. The repository contains the relevant test files, but a passing
  count is not independently established here.
- Migration files form an apparent linear static chain ending at `20260820_0021`.
  A live Alembic-head check was not run, so the operational database head is not
  independently verified.

Based on repository evidence alone, the system should not accept live customer
capital, enable live customer trading, or represent customer withdrawals as
operationally complete. Qualified human regulatory/legal review is also
required before making any regulated-service or customer-capital decision.
The distinction is explicit: code completion is not production completion;
production completion is not production-risk completion; production-risk
completion is not customer-capital safety.

## 2. A — VERIFIED NEWS / MACRO DATA

### A1. Current verified state

**NOT VERIFIED — insufficient repository evidence for a real news/macro source.**

The repository has a provider-neutral news contract, but no approved production
news adapter, provider configuration, stored event history, or provider
verification record for high-impact macro or crypto events.

### A2. What is actually complete

The following code-level elements are established:

1. `app/quant/strategy_evidence.py` defines:
   - `StrategyEvidenceState`, including `NOT_CONFIGURED`, `NEWS_BLOCKED`, and
     `SIGNAL_READY`;
   - `NewsEvent` with event, timestamp, affected market, impact, source, and
     provenance;
   - `NewsEvaluation` with clear/block state, reason, timestamp, events, and
     provenance;
   - a provider-neutral asynchronous `NewsProvider` protocol;
   - `NotConfiguredNewsProvider`, which returns `clear=False` and
     `NOT_CONFIGURED`.
2. `app/quant/market_adapter.py` injects the news provider into the existing
   QLab path. A non-ready or non-clear news evaluation raises `NEWS_BLOCKED`.
3. `app/quant/market_adapter.py` combines structural, session, and news
   references into `StrategyEvidenceProvenance` when the strategy adapter
   constructs evidence.
4. `app/integrations/connectors/catalog.py` contains `NewsConnector`, but it is
   a `DeterministicMockConnector` with mock operations. It is not a verified
   news source and cannot establish event truth, timestamp correctness, or
   provenance.
5. No `NEWS_*`, macro-calendar, economic-calendar, CPI, NFP, FOMC, GDP,
   unemployment, SEC/CFTC, exchange-incident, protocol-event, or crypto-news
   configuration was found in `.env.example`, `app/core/config.py`, the market
   provider configuration, or the quant contracts.

### A3. Existing market-data providers and their actual boundary

#### Twelve Data

**Confidence: Established for code presence; Uncertain for production
verification.**

`app/market/twelve_data.py` implements:

- provider verification and instrument identity checks;
- current quote handling;
- bounded historical candles through `/time_series`;
- OHLC validation, timestamp normalization, duplicate detection, ordering
  checks, stale-data rejection, and provider/source provenance;
- server-side API-key use through `SecretStr` configuration.

`app/market/gateway.py` applies workspace authorization, provider verification,
instrument mapping checks, and provenance checks before returning historical
candles to QLab.

The repository does not establish:

- that a real Twelve Data credential is present in the deployed environment;
- that the provider has been authenticated successfully for the required
  historical endpoint;
- the actual account plan or historical endpoint entitlement;
- observed production latency, rate-limit behavior, uptime, or outage handling;
- historical retention sufficient for the intended backtest/replay horizon;
- a production event store preserving the exact data known at a decision time;
- commercial licensing or redistribution rights for any customer-facing use.

The documentation explicitly requires server-side provisioning and separate
commercial/entitlement review. `.env.example` sets internal non-display scope
and entitlement flags to false.

#### Alpha Vantage

**Confidence: Established.**

`app/market/alpha_vantage.py` implements current-price verification paths. The
provider-neutral base method for historical candles in
`app/market/provider.py` raises `Historical OHLC is not configured for this
provider`. No Alpha Vantage historical OHLC adapter was found. Therefore Alpha
Vantage cannot establish the required candle evidence in this repository.

#### Legacy direct path

`app/data_engine/market_data.py` explicitly raises
`LegacyMarketDataAccessDisabled`. This is a useful fail-closed control, not a
news or historical-replay implementation.

### A4. Point-in-time correctness and historical replay

| Requirement | Repository evidence | Assessment |
|---|---|---|
| Point-in-time event correctness | `NewsEvent` has a timestamp, but no provider or event store exists | **NOT VERIFIED** |
| Historical replay | OHLC candles can be normalized from a live provider response; no persisted news/event history or replay service exists | **NOT VERIFIED** |
| Timestamp integrity | News event/evaluation timestamps must be timezone-aware; no source-clock, ingestion-clock, clock-skew, or revision policy exists | **PARTIALLY SUPPORTED** |
| Source provenance | News contracts require source/provenance strings; no real source populates them | **PARTIALLY SUPPORTED** |
| Auditability | Strategy evidence has provenance fields; no news ingestion/audit record or immutable event lineage exists | **PARTIALLY SUPPORTED** |
| Availability/reliability | Provider protocols exist; no news availability monitor, health history, or production evidence exists | **NOT VERIFIED** |
| Rate-limit handling | Market-data settings include a rate-limit value and provider error states; no news rate-limit adapter exists | **NOT VERIFIED** |
| Data latency | Market OHLC has receipt/freshness checks; no news publication/receipt latency or maximum news age policy is implemented | **NOT VERIFIED** |
| High-impact US coverage | No CPI/NFP/FOMC/GDP/unemployment provider or schema was found | **NOT VERIFIED** |
| Historical retention | No news event persistence/table/repository was found | **NOT VERIFIED** |
| Reproduce historical knowledge | No decision-time news snapshot or immutable event revision model was found | **NOT VERIFIED** |
| Failure behavior | Missing news provider fails closed through `NotConfiguredNewsProvider` and QLab `NEWS_BLOCKED` | **Established for missing provider** |
| Provider selection/routing | News provider injection exists, but no production selection/configuration exists | **PARTIALLY SUPPORTED** |
| Licensing/redistribution | Market-data entitlement fields/docs exist; no news licensing fields or review record exists | **NOT VERIFIED** |
| Support/SLA | No news vendor contract, SLA, escalation, or incident evidence exists | **NOT VERIFIED** |

### A5. Traditional macro/news coverage

The architecture does not currently establish coverage for scheduled or
unscheduled traditional macro events, including CPI, NFP, FOMC decisions,
interest-rate decisions, GDP, unemployment, or comparable high-impact US
releases.

The `NewsEvent` type can represent an event generically, but a generic schema is
not a data source, coverage guarantee, historical archive, or correctness proof.

**Assessment: NOT VERIFIED.**

### A6. Crypto/digital-asset event coverage

No approved provider, adapter, event taxonomy, or retention path was found for
exchange incidents, exchange outages, hacks, protocol events, major blockchain
events, crypto regulatory actions, SEC/CFTC actions, or other material BTC/crypto
events.

The market configuration includes BTCUSD as an instrument, but that establishes
price-instrument mapping only. It does not establish crypto-event coverage.

**Assessment: NOT VERIFIED.**

One traditional macro provider should not be assumed to cover crypto-specific
events. The repository contains no evidence that either category is covered.

### A7. Strategy and signal dependency

`GatewayQLabResearchProvider` obtains verified historical candles, builds
structural evidence, evaluates a server-side session, then evaluates news. The
default news provider is `NotConfiguredNewsProvider`; this causes signal
research to stop before final signal normalization.

`ARIMAStrategyEvidenceProvider` derives levels from structural evidence and
requires liquidity sweep, MSS, and pullback/retest confirmations. It does not
create news data. It also uses a fixed confidence value and does not demonstrate
an implemented, independently sourced score/risk-tier/news-calendar policy.

Therefore the current blocker is supported, but “QLab complete” should be read
as “contracts and fail-closed adapter exist,” not “production-signal evidence is
available.”

### A8. Provider verification classification

| Provider/source referenced | Technically possible | Research-sufficient | Backtest-sufficient | Production-signal-sufficient | Customer-capital-sufficient |
|---|---:|---:|---:|---:|---:|
| Twelve Data historical OHLC | Likely | Likely after real verification | **NOT VERIFIED** | **NOT VERIFIED** | **NOT VERIFIED** |
| Alpha Vantage historical OHLC | **No repository implementation** | No | No | No | No |
| `NewsConnector` | Only as a deterministic mock | No | No | No | No |
| `NotConfiguredNewsProvider` | No | No | No | No | No |

The repository does not contain sufficient evidence to determine pricing,
licensing, historical retention, SLA, or tier capability for any news provider.
Direct provider documentation and vendor confirmation are required. No
commercial conclusion is made here.

### A9. Security, financial, and production impact

- **Security impact:** A false-clear news result could authorize a signal during
  a restricted event window. The current missing-provider path is safer because
  it blocks.
- **Financial/customer risk:** Without historical event correctness and
  point-in-time replay, backtests and live signals could be materially
  misleading. This blocks treating strategy output as customer-capital safe.
- **Production impact:** The system can remain operationally fail-closed, but
  it cannot produce an evidence-backed live ARIMA signal from repository
  evidence alone.

### A10. Complexity and gates

- **Complexity:** Very High.
- **Blocks live customer capital:** Yes.
- **Blocks ARIMA live signal generation:** Yes.

Minimum verification sequence:

1. Founder selects an approved macro/news source and separately decides whether
   crypto-event coverage is required.
2. Obtain direct vendor confirmation of endpoint coverage, historical retention,
   timestamp/revision semantics, rate limits, licensing, and support/SLA.
3. Define an immutable event schema and decision-time snapshot/replay contract.
4. Add a server-side provider adapter with authentication, source identity,
   publication/receipt timestamps, revisions, freshness, and failure states.
5. Verify historical replay against provider records and retained evidence.
6. Verify bounded outage/rate-limit behavior and audit lineage before enabling
   the news gate.

### A11. Independent review questions

1. What exact provider and product tier supplies both scheduled macro events and
   crypto-specific material events, if one is intended to do both?
2. Does the source preserve corrections, cancellations, embargoes, and
   publication-time revisions?
3. Can ARIMA reproduce exactly what it knew at each historical decision time?
4. What is the maximum acceptable publication-to-ingestion latency?
5. What happens when the provider is delayed, rate-limited, partially degraded,
   or retrospectively corrects an event?
6. Are historical events licensed for internal research, backtesting, customer
   display, or redistribution?
7. Is one source sufficient for risk decisions, or is independent redundancy
   required?
8. How are asset/currency mappings and event impact classifications reviewed?

## 3. B — COMPLIANCE / REGULATORY

This section is a technical evidence review, not legal advice. Code inspection
cannot establish FCA authorization, regulatory permissions, UK GDPR compliance,
KYC/AML compliance, suitability compliance, or permission to provide regulated
investment/advisory services.

### B1. Current verified state

**Technical controls exist in several areas. Regulatory status is NOT VERIFIED.**

The repository contains authentication, role checks, workspace membership,
server-side Founder access, audit records, ledger records, withdrawal controls,
and a disabled QTrade execution boundary. These are technical mechanisms, not
proof that a legal or regulatory obligation is satisfied.

### B2. Technical controls actually present

#### Identity and authentication — Confidence: Established

- `get_current_active_user` requires a decoded access token, active user, and
  verified email.
- JWT claims include issuer, audience, type, subject, expiry, JTI, and refresh
  session ID.
- Refresh sessions are stored server-side; refresh rotation consumes the active
  token and revokes the family on reuse detection.
- Passwords use `pwdlib.PasswordHash.recommended()` and are not stored as
  plaintext by the account service.
- Login, registration, and security-sensitive operations have rate-limiter
  infrastructure.

#### Authorization and isolation — Confidence: Uncertain

- Founder control requires a configured allowlist email plus administrator role.
- Platform administration is separately allowlisted in production.
- Customer portfolio routes use the authenticated user for self-service.
- Founder-only portfolio, support, and withdrawal operations routes exist.
- Portfolio and ledger queries are scoped by workspace/user in the inspected
  service code.

Limitations:

- Audit rows do not have first-class tenant/workspace columns; workspace is
  sometimes placed in free-form JSON metadata.
- `RiskSnapshot.tenant_id` is optional and `RiskEngine` does not compare it to a
  signal context.
- Customer support lookup is intentionally global for a Founder surface, but
  the inspected service has no explicit tenant restriction or separate support
  access scope beyond Founder authorization.
- Production evidence of the configured allowlist, session propagation, and
  database enforcement was not available.

#### Customer data and audit — Confidence: Established for code presence

- Customer support responses omit password hashes and secret fields.
- Security events and refresh-session metadata are exposed to the Founder detail
  response, but token values are not returned.
- `record_audit` records actor, action, entity, entity ID, event type, and JSON
  metadata.
- Ledger transactions carry actor/source/provenance fields.
- Withdrawal actions and circuit-breaker changes create audit events.

Limitations:

- Audit logs have no visible append-only database control, hash chain, tamper
  evidence, retention policy, export policy, or independent monitoring.
- The repository does not establish that audit metadata cannot be edited by a
  database operator or that all sensitive operations are covered in production.

#### Ledger and transaction history — Confidence: Uncertain

- Financial transactions and ledger entries use Decimal/Numeric fields.
- Ledger posting requires balanced debit and credit amounts.
- Posted history is intended to be corrected through reversal entries.
- Idempotency keys have database unique constraints scoped by workspace.
- Ledger balances calculate total, available, reserved, and pending buckets.

Limitations:

- Static code inspection identifies a check-then-insert idempotency pattern and
  `with_for_update()` use, but no concurrency/recovery evidence establishes
  safety under the deployed database and transaction isolation.
- There is no inspected reconciliation process against an external custodian,
  chain, broker, or bank.

#### Withdrawals — Confidence: Uncertain

- Input schema requires positive Decimal amounts, ETH, Ethereum Mainnet,
  hexadecimal `0x` address format, confirmation, risk acknowledgement, and an
  idempotency key.
- Withdrawal creation obtains the user workspace, checks circuit state, reads
  the ledger balance, reserves funds through a ledger transfer, and sets the
  request to `UNDER_REVIEW`.
- Founder authorization is required for approval/rejection/block transitions.
- Wallets are masked in notification/API response paths.
- No blockchain signing/private-key or broker call was found in this workstream.

Limitations:

- The schema validates hexadecimal shape, not checksum semantics.
- There is no external wallet ownership verification, sanctions screening,
  transaction monitoring, KYC/AML workflow, or chain reconciliation.
- Static inspection shows no database-level concurrency proof for simultaneous
  reservations.
- Static route matching resolves the previous concern: although
  `GET /{request_id}` is registered before `GET /operations/list`, the
  parameter is typed as `UUID`. The literal segment `operations` does not match
  the UUID path converter, so the generic route does not match that request and
  Starlette continues to the later literal route. The previous route-ordering
  concern is therefore **resolved statically**; no runtime execution was
  needed.
- The repository does not establish an operational notification delivery SLA or
  durable outbox/once-only delivery mechanism.

#### Execution controls — Confidence: Established for disabled state

- `QTradeExecutionService` rejects construction with `execution_enabled=True`.
- `DisabledQTradeExecution.submit_order` raises an explicit disabled error.
- `QTradeDryRunAdapter` labels results `DRY_RUN` and `not_executed=True`.
- The execution service checks the withdrawal circuit state and risk provider
  before producing a dry-run decision.

This does not establish broker/exchange safety because no live adapter or
production execution evidence exists.

### B3. Technical controls missing or not established

The repository does not establish the following:

- KYC identity verification beyond email verification and account identity;
- AML/sanctions screening;
- source-of-funds/source-of-wealth checks;
- suspicious transaction monitoring or escalation;
- customer suitability/appropriateness assessment;
- regulated advice/discretionary-management controls;
- FCA authorization, appointed-representative status, permissions, or perimeter
  analysis;
- customer-money safeguarding, custody, reconciliation, or insolvency controls;
- formal data-retention schedules, legal holds, deletion/erasure workflows, or
  data-subject request handling;
- consent, privacy-notice, processing-purpose, international-transfer, or DPA
  evidence;
- customer complaint handling, vulnerable-customer controls, or incident
  notification workflow;
- production operational resilience, RTO/RPO, backup/restore evidence, or
  incident response testing;
- independent audit or change-control evidence for financial mutations.

### B4. Questions requiring qualified human regulatory/legal counsel

The following are explicitly outside what repository inspection can decide and
require qualified UK regulatory/legal review:

1. Whether the proposed ARIMA service is a regulated investment, advisory,
   arranging, execution, portfolio-management, custody, or payment activity.
2. Whether ARIMA requires FCA authorization, another permission, a partnered
   regulated entity, or a different operating perimeter.
3. Whether accepting customer funds, recording balances, reserving withdrawal
   funds, or processing withdrawals creates client-money, custody, payment, or
   cryptoasset obligations.
4. KYC/AML, sanctions, Travel Rule, transaction monitoring, suspicious activity,
   and source-of-funds obligations for the intended customer and asset model.
5. UK GDPR lawful basis, privacy notices, data minimization, retention,
   deletion, subject access, processor/controller roles, and international data
   transfers.
6. Marketing, performance, signal, and AI-generated communication requirements,
   including whether outputs constitute personal recommendations or financial
   promotions.
7. Record-keeping, best execution, conflict-of-interest, complaints, and
   governance requirements for any live execution or customer-capital activity.

Before accepting real customer capital, operating customer withdrawals, or
providing regulated investment/advisory services, these questions require
qualified UK regulatory/legal review and Founder approval. The code cannot
answer them.

### B5. Security, financial, and production impact

- **Security impact:** Authentication and role controls reduce risk, but missing
  KYC/AML, support-access governance, audit integrity, retention, and production
  evidence leave material gaps.
- **Financial/customer risk:** The repository does not establish customer-money
  safeguarding, external reconciliation, or production recovery. This blocks
  customer-capital acceptance.
- **Production impact:** Technical endpoints exist, but the repository cannot
  establish that the operational and legal control environment is ready.

### B6. Complexity and gates

- **Complexity:** Very High.
- **Blocks live customer capital:** Yes.
- **Blocks ARIMA live signal generation:** Yes where signals could influence
  customer trading or regulated advice; legal perimeter review is required.

Recommended verification sequence:

1. Founder obtains qualified UK regulatory/legal perimeter advice.
2. Define the legal operating model: technology-only, research-only, regulated
   partner, or another approved model.
3. Map KYC/AML, sanctions, suitability, custody/client-money, complaints, and
   record-keeping obligations to explicit technical controls.
4. Define data-retention/deletion/privacy requirements and verify them against
   database, logs, backups, provider stores, and frontend telemetry.
5. Complete threat modeling, access review, audit-integrity review,
   reconciliation design, incident response, and recovery exercises.
6. Obtain Founder/legal sign-off before any capital, withdrawal, advisory, or
   live execution gate.

### B7. Independent review questions

1. What legal activity does the complete ARIMA product constitute in the target
   jurisdictions?
2. Who is the regulated entity, if any, responsible for advice, execution,
   custody, customer money, and complaints?
3. Where is KYC/AML evidence stored, and who reviews alerts and escalations?
4. How are balances and withdrawals reconciled to an independent authoritative
   source?
5. What is the legally required retention period, and how are deletion requests
   handled against immutable audit/ledger records and backups?
6. Is Founder support access appropriately scoped, time-limited, monitored, and
   separately approved?
7. What customer disclosures and consent are required for AI-generated signals,
   communications, and portfolio information?

## 4. C — RISK MANAGEMENT RE-VALIDATION

### C1. Current verified state

**CODE COMPLETE: SUPPORTED for the limited implemented contract.**  
**PRODUCTION RISK COMPLETE: NOT VERIFIED.**

The repository contains a risk contract, Decimal calculations, a ledger-based
portfolio adapter, circuit-breaker checks, idempotency columns, and dry-run
execution records. That is not sufficient evidence for correctness under
concurrency, failure, recovery, provider outages, or customer-capital use.

### C2. What is actually complete at code level

#### Ledger and balance model — Confidence: Established for intended code path

- `FinancialTransaction` and `LedgerEntry` model posted/reversed/cancelled
  financial history.
- `LedgerService.post` requires a single asset, positive amounts, and equal debit
  and credit totals.
- `LedgerService.balance` computes authoritative, available, reserved, and
  pending balances from ledger entries rather than frontend values.
- `transfer_bucket` checks the source bucket and creates compensating entries.
- Database constraints include positive amounts, valid direction/bucket/status,
  and workspace-scoped idempotency.
- Reversal is represented by new entries and status change rather than editing
  original entries.

#### Portfolio/risk adapter — Confidence: Established for limited fields

- `PortfolioRiskProvider` obtains portfolio data through `PortfolioService`.
- Multi-asset balances without an approved valuation provider raise
  `ValuationUnavailableError`.
- Decimal totals are used for balances and valuation results.
- `RiskSnapshot` includes total equity, available/reserved capital, exposure,
  concentration, P&L, daily loss, and strategy exposure fields.

#### Risk validation — Confidence: Established for implemented checks

`RiskEngine.validate` checks:

- workspace and account match between signal and snapshot;
- positive equity and nonnegative capital buckets;
- available plus reserved does not exceed total equity;
- positive maximum risk;
- optional concentration, daily-loss, portfolio-exposure, and
  strategy-exposure limits;
- nonzero stop distance;
- position sizing from maximum risk;
- position cost against available capital;
- negative asset exposure rejection.

#### Circuit breaker and QTrade boundary — Confidence: Established for code gate

- Withdrawal circuit states are `ENABLED`, `PAUSED`, and `EMERGENCY_STOP`.
- Withdrawal creation blocks non-enabled states.
- Founder authorization is required to change the circuit state.
- QTrade evaluation records circuit state and blocks when non-enabled.
- Live QTrade construction is explicitly rejected; the dry-run adapter does not
  contact a broker or exchange.

### C3. Material gaps in the risk implementation

#### Risk inputs are incomplete — Confidence: Established

`PortfolioRiskProvider.snapshot` sets:

- `current_exposure` to `Decimal("0")`;
- `realized_pnl` and `unrealized_pnl` to their `RiskSnapshot` defaults;
- `daily_loss` to its default;
- `strategy_exposure` to its default;
- `concentration` to its default.

It maps positions only to `asset_exposure` quantities. Therefore the contract
has fields for important controls, but the inspected production adapter does
not derive those controls from authoritative data. Optional risk checks can be
skipped when these fields are absent.

#### Tenant identity is not enforced end-to-end — Confidence: Established

`ResearchSignal` carries workspace and account IDs but not tenant ID.
`RiskSnapshot.tenant_id` is optional. `RiskEngine.validate` checks workspace and
account equality but does not compare tenant identity. `QTradeExecutionService`
records a caller-supplied `tenant_id` but does not validate it against a signal
or portfolio owner. This is a material gap for a system claiming strict
tenant/account isolation.

#### Concentration semantics are not established — Confidence: Established

The risk engine compares a supplied concentration value to a limit, but the
repository does not establish how concentration is calculated, whether the
value is a ratio or amount, or how multi-asset valuation feeds it.

#### Database concurrency is not proven — Confidence: Established

`LedgerService` performs a preflight idempotency lookup before inserting a
transaction and uses `with_for_update()` when retrieving an account. The
repository contains no concurrency test or production database isolation proof
showing that two simultaneous requests cannot both pass the balance check and
reserve the same funds. A unique constraint can reject one insert, but the
resulting transaction handling and retry behavior are not established.

The same concern applies to withdrawal creation: it checks idempotency, reads
available balance, and then reserves funds. No static evidence establishes
atomic behavior under concurrent requests, process crashes, deadlocks, or
serialization failures.

#### Circuit-breaker recovery is not proven — Confidence: Partially supported

State values, authorization, and audit calls exist. No repository evidence
establishes restart persistence, emergency-stop recovery procedures, stale
operator sessions, multi-region consistency, or that every future execution
entry point consults the same breaker.

#### Transaction rollback/recovery is not proven — Confidence: Established

The service uses async SQLAlchemy transactions and commits in application
services, but no crash/restart/recovery tests, reconciliation process, or
database-failure test evidence was found in the inspected risk tests. No live
broker/exchange integration exists to validate partial execution or external
failure reconciliation.

#### Authorization of execution decisions is incomplete — Confidence: Likely

`QTradeExecutionService.evaluate` accepts `actor_id` but does not itself call a
role/authorization service. The caller may be expected to authorize earlier,
but the inspected boundary does not establish that every execution decision is
server-authorized immediately before evaluation. The disabled state limits the
current financial effect, but this remains a future live-execution risk.

#### Audit integrity is incomplete — Confidence: Established

Audit records contain actor/entity/action/event metadata, and trade records
contain risk/circuit/provenance fields. The audit schema does not show:

- tenant/workspace as first-class columns;
- immutable append-only enforcement;
- tamper-evident hashing;
- sequence integrity;
- independent export or retention controls;
- guaranteed transaction coupling for every state mutation.

### C4. Adversarial, financial, and operational evidence matrix

| Area | Repository evidence | Assessment |
|---|---|---|
| Malformed requests | Pydantic withdrawal validation; risk input checks | **Partially supported** |
| Boundary conditions | Unit tests and Decimal checks are present in files | **Code evidence only** |
| Conflicting requests | State transition maps exist | **Partially supported** |
| Repeated requests | Idempotency columns/lookup exist | **Production concurrency not verified** |
| Duplicate requests | Unique constraints exist | **Production race behavior not verified** |
| Race conditions | No concurrency test/evidence found | **NOT VERIFIED** |
| Double-spend prevention | Reservation and balance checks exist | **Production concurrency not verified** |
| Withdrawal/risk interaction | Shared ledger/circuit concepts exist | **Partially supported** |
| Exposure limits | Fields/checks exist, but adapter supplies zero/default values | **NOT VERIFIED** |
| Concentration limits | Field/check exists, calculation absent | **NOT VERIFIED** |
| Daily-loss controls | Field/check exists, authoritative source absent | **NOT VERIFIED** |
| Position limits | No separate authoritative position-limit implementation found | **NOT VERIFIED** |
| Portfolio limits | Optional risk checks exist | **Partially supported** |
| Circuit activation | State and founder gate exist | **Code-supported** |
| PAUSED/EMERGENCY_STOP | Withdrawal/QTrade checks exist | **Code-supported; operational evidence absent** |
| Circuit recovery | No recovery exercise or runbook evidence | **NOT VERIFIED** |
| Stale prices | Market provider freshness checks exist | **Code-supported for market path** |
| Missing prices/valuation | Multi-asset valuation fails closed | **Code-supported** |
| Provider failure | Market provider maps failure states | **Code-supported; real failure not verified** |
| Partial provider failure | Fallback loop exists for configured market providers | **Production behavior not verified** |
| Inconsistent market data | Identity/OHLC validation exists | **Partially supported** |
| Partial execution | No live execution adapter | **NOT APPLICABLE YET / NOT VERIFIED** |
| Duplicate execution requests | Unique execution idempotency exists | **Production race behavior not verified** |
| Transaction rollback | SQL transaction usage exists | **Crash/recovery not verified** |
| Database failure | No failure/restart evidence found | **NOT VERIFIED** |
| Restart/recovery | No deployment/restart exercise evidence | **NOT VERIFIED** |
| Tenant isolation | Workspace/account checks exist; tenant propagation is incomplete | **PARTIALLY SUPPORTED** |
| Customer isolation | Portfolio queries are user/workspace scoped | **Code-supported; adversarial production evidence absent** |
| Founder boundaries | Founder dependency and allowlist exist | **Code-supported; configuration not verified** |
| Audit integrity | Metadata audit records exist | **PARTIALLY SUPPORTED** |

### C5. Test evidence boundary

The repository contains focused tests under `tests/ledger`, `tests/operations`,
`tests/market`, and `tests/quant` covering ledger balance/reversal, withdrawal
validation/circuit states, risk checks, dry-run labeling, historical OHLC
validation, and strategy evidence.

This audit did not execute any tests. The supplied “427 passed” count is not
independently verified. Static inspection also did not find tests proving all of
the following:

- concurrent withdrawals/reservations;
- concurrent ledger idempotency races;
- process crash between reservation and request persistence;
- restart/recovery/reconciliation;
- database failover or transaction retry;
- real provider rate-limit/outage behavior;
- broker/exchange rejection or partial fill;
- cross-tenant risk-context mismatch;
- complete exposure/concentration/daily-loss derivation;
- tamper-evident audit integrity;
- production configuration and deployed migration state.

### C6. Code-complete versus production-risk-complete classification

| Component | Code Complete | Production Risk Complete |
|---|---|---|
| Ledger posting/buckets | **SUPPORTED** | **NOT VERIFIED** |
| Portfolio query/isolation path | **SUPPORTED** | **NOT VERIFIED** |
| Withdrawal reservation/state machine | **PARTIALLY SUPPORTED** | **NOT VERIFIED** |
| Risk contract/schema | **SUPPORTED** | **NOT VERIFIED** |
| Risk snapshot completeness | **NOT SUPPORTED as complete** | **NOT VERIFIED** |
| Circuit breaker | **SUPPORTED** | **NOT VERIFIED** |
| QTrade disabled gate | **SUPPORTED** | **NOT VERIFIED operationally** |
| QTrade dry-run | **SUPPORTED** | **NOT VERIFIED under adversarial conditions** |
| Tenant/account isolation | **PARTIALLY SUPPORTED** | **NOT VERIFIED** |
| Auditability | **PARTIALLY SUPPORTED** | **NOT VERIFIED** |

### C7. Security, financial, and production impact

- **Security impact:** Missing tenant propagation and incomplete authorization
  enforcement at the QTrade boundary are material, even while live execution
  is disabled.
- **Financial/customer risk:** Default/zero exposure and loss inputs can make
  risk checks appear to pass without representing the real portfolio. This is a
  critical blocker for customer capital or live trading.
- **Production impact:** Dry-run-only behavior reduces immediate external
  financial effect, but it does not establish that a future enablement would be
  safe.

### C8. Complexity and gates

- **Complexity:** Very High.
- **Blocks live customer capital:** Yes.
- **Blocks ARIMA live signal generation:** Yes for any signal used to make or
  recommend customer-capital decisions.

Recommended verification sequence:

1. Define and enforce tenant/workspace/account identity through signal, risk,
   execution, ledger, and audit records.
2. Build authoritative exposure, concentration, position, realized/unrealized
   P&L, daily-loss, and strategy-exposure inputs.
3. Establish database transaction/isolation semantics and test concurrent
   reservations/idempotency under the production database.
4. Add crash/restart/recovery and reconciliation procedures.
5. Add provider outage/rate-limit/staleness and valuation failure exercises.
6. Keep live QTrade disabled until broker/exchange, authorization, audit,
   rollback, partial-fill, and operational controls are independently approved.
7. Obtain Founder and qualified legal/regulatory approval before customer
   capital or live execution.

### C9. Independent review questions

1. Can two concurrent withdrawal requests both pass the balance check before
   either reservation commits?
2. What database isolation level and row-lock behavior apply in production?
3. What exact source computes current exposure, concentration, daily loss, and
   strategy exposure?
4. Can any caller supply a tenant ID or actor ID inconsistent with the account?
5. Does every execution entry point re-authorize immediately before risk and
   execution decisions?
6. How are ledger, portfolio, withdrawal, and external-custody balances
   reconciled after crashes or provider outages?
7. What prevents audit records from being altered or deleted by privileged
   database access?
8. How are partial fills, rejected orders, duplicate callbacks, and settlement
   failures handled before any live gate is enabled?
9. What evidence demonstrates the production migration head and configuration?
10. Are the risk limits measured in the same units and valuation basis as the
    portfolio balances and positions?

## 5. CONTRADICTIONS

| Claim | Repository evidence | Assessment |
|---|---|---|
| Ledger foundation: COMPLETE | Ledger models, Decimal balance calculations, double-entry checks, reversal method, and migration files exist | **PARTIALLY SUPPORTED** — concurrency, reconciliation, and production evidence absent |
| Portfolio foundation: COMPLETE | Portfolio/account/position models and scoped summary service exist | **PARTIALLY SUPPORTED** — production isolation and mutation/reconciliation evidence absent |
| Withdrawal balance integration: COMPLETE | Ledger reservation path and idempotency field exist | **PARTIALLY SUPPORTED** — concurrent reservation and operational delivery behavior not verified |
| Risk contract: COMPLETE | `RiskSnapshot`, `RiskLimits`, and `RiskEngine` exist | **CONTRADICTED as a complete risk implementation** — portfolio adapter supplies zero/default exposure, P&L, daily-loss, strategy-exposure, and concentration inputs |
| QLab: COMPLETE | Contracts, OHLC structural evidence, session provider, and news interface exist | **PARTIALLY SUPPORTED** — default news provider is explicitly not configured |
| QTrade contract: COMPLETE | Provider-neutral protocols and disabled execution boundary exist | **SUPPORTED as a contract only** |
| Execution state machine: COMPLETE | Explicit transition maps exist | **PARTIALLY SUPPORTED** — no live execution/recovery/partial-fill evidence |
| Circuit breaker: COMPLETE | Enabled/paused/emergency states, Founder gate, and checks exist | **PARTIALLY SUPPORTED** — recovery, consistency, and production evidence absent |
| Customer portfolio connection: COMPLETE | Scoped portfolio queries and Founder inspection route exist | **PARTIALLY SUPPORTED** — tenant handling and production IDOR evidence absent |
| Auditability: COMPLETE | Audit helper, event metadata, ledger/trade/withdrawal audit calls exist | **PARTIALLY SUPPORTED** — no append-only/tamper evidence, first-class tenant/workspace fields, or complete coverage proof |
| Historical OHLC: COMPLETE | Twelve Data adapter and validation exist; Alpha Vantage inherits unsupported method | **PARTIALLY SUPPORTED** — no real provider/entitlement/retention/replay evidence |
| Session provider: COMPLETE | Server-clock timezone/session/weekend evaluator exists | **SUPPORTED as code**; production clock/configuration behavior not independently verified |
| Strategy evidence: COMPLETE | Structural and deterministic ARIMA evidence adapter exists | **PARTIALLY SUPPORTED** — no external event evidence, score-tier proof, or production signal verification |
| QLab evidence: COMPLETE | Evidence objects retain provider/source/reference fields | **PARTIALLY SUPPORTED** — provenance is contract-level, not independently verified in production |
| Signal → Risk: COMPLETE | QTrade calls `RiskEngine` and risk checks exist | **CONTRADICTED as complete** — risk snapshot omits authoritative exposure/loss/concentration inputs and tenant validation |
| QTrade dry-run: COMPLETE | Dry-run output is labeled `DRY_RUN`/`not_executed` and no live adapter exists | **SUPPORTED as code**; concurrency and production evidence absent |
| Market data: COMPLETE | Twelve Data/Alpha adapters and verification states exist | **PARTIALLY SUPPORTED** — credentials, real verification, entitlements, SLA, and deployment state not established |
| QTrade live execution intentionally disabled | Constructor rejects enabled mode; disabled adapter raises | **SUPPORTED** |
| Multi-asset valuation fail-closed | Portfolio risk provider raises without valuation provider when multiple balances exist | **SUPPORTED for that branch**; complete valuation/risk behavior is not established |
| Current test count: 427 passed | Test files are present; tests were not run in this audit | **NOT VERIFIED** |
| Migration head: 20260820_0021, single head | Static revision/down-revision chain appears linear from 0018→0019→0020→0021 | **PARTIALLY SUPPORTED** — no operational Alembic-head/database check was run |
| Current blocker is no approved verified news provider | `NotConfiguredNewsProvider`, no news config/adapter, mock `NewsConnector`, and QLab `NEWS_BLOCKED` path exist | **SUPPORTED** |

## 6. CROSS-WORKSTREAM DEPENDENCIES

```text
Approved news/macro source
        ↓
Point-in-time strategy evidence
        ↓
ARIMA signal generation
        ↓
Authoritative risk snapshot and limits
        ↓
Circuit breaker + authorization
        ↓
QTrade execution boundary
        ↓
Ledger / portfolio / reconciliation
        ↓
Withdrawal operations
        ↓
Customer capital
```

Compliance/regulatory governance applies across every edge, not only the final
customer-capital edge:

- **News → Signal:** data licensing, timestamp correctness, event coverage, and
  research reproducibility are technical/vendor gates.
- **Signal → Risk:** authoritative valuation, exposure, loss, and identity
  propagation are technical gates.
- **Risk → Execution:** authorization, circuit breaker, broker controls,
  idempotency, partial-fill handling, and audit are technical gates; Founder
  approval is required for enabling live execution.
- **Execution → Ledger:** settlement, reconciliation, rollback, and custody
  controls are technical and operational gates.
- **Ledger → Withdrawal:** customer-money, AML/sanctions, approval, delivery,
  and reconciliation controls are technical plus legal/regulatory gates.
- **Withdrawal → Customer Capital:** legal perimeter, custody/client-money,
  safeguarding, disclosures, complaints, and regulatory permissions require
  Founder and qualified counsel decisions.

## 7. HARD GATES

The following are minimum evidence gates, not approvals.

### 1. ARIMA generates a live signal

Required:

- approved news/macro source, and separate crypto-event coverage if required;
- real provider authentication and entitlement verification;
- point-in-time event timestamps, historical retention, revision handling, and
  replay evidence;
- verified OHLC freshness/provenance;
- session and structural evidence;
- complete risk inputs, including exposure, concentration, daily loss, and
  valuation;
- tenant/workspace/account identity validation;
- audit and provider failure evidence;
- Founder/legal approval for the intended use if signals influence customers.

**Current status: BLOCKED.**

### 2. ARIMA executes a dry-run

Required:

- all research/risk evidence above except external order submission;
- explicit `DRY_RUN`/`NOT_EXECUTED` result;
- no ledger mutation or broker call;
- idempotency and audit evidence;
- testable tenant/account isolation.

**Current code status: CODE-SUPPORTED, production evidence not verified.**

### 3. ARIMA executes against a broker/exchange

Required:

- Founder authorization;
- qualified legal/regulatory perimeter decision;
- approved broker/exchange and credentials in a server-side secret boundary;
- pre-trade authorization and risk checks at the final boundary;
- idempotency, duplicate prevention, partial-fill/rejection/callback handling;
- reconciliation and rollback/compensation design;
- operational monitoring, emergency stop, incident response, and recovery
  exercises;
- tamper-evident audit and complete tenant/account identity.

**Current status: BLOCKED. Live execution is disabled.**

### 4. Customer capital is accepted

Required:

- qualified UK regulatory/legal review and approved operating model;
- KYC/AML/sanctions/source-of-funds controls;
- custody/client-money/safeguarding and independent reconciliation design;
- customer disclosures, terms, privacy, complaints, and record keeping;
- production resilience and incident response evidence;
- Founder approval.

**Current status: BLOCKED.**

### 5. Customer withdrawals become operational

Required:

- all customer-capital gates;
- verified balance/custody source and reservation concurrency proof;
- withdrawal monitoring, sanctions/AML checks, address/network validation,
  approval segregation, notification/outbox reliability, and reconciliation;
- approved legal/regulatory operating model and Founder approval.

**Current status: BLOCKED.**

### 6. Live customer trading is enabled

Required:

- every prior gate;
- live broker/exchange failure and partial-execution evidence;
- independent security/risk review;
- production deployment, rollback, restart, recovery, and incident exercises;
- explicit Founder and qualified legal/regulatory approval.

**Current status: BLOCKED.**

## 8. FOUNDER-LEVEL OPEN DECISIONS

1. Whether ARIMA will remain research-only/dry-run or move toward live signals,
   execution, customer capital, and withdrawals.
2. Whether to purchase a paid macro/news provider and/or a separate crypto-event
   provider.
3. Which provider coverage, retention, timestamp, licensing, and support/SLA
   terms are acceptable.
4. Whether customer display, redistribution, or internal-only market-data rights
   are required and legally/commercially approved.
5. Whether to commission qualified UK regulatory/legal review and what operating
   model to adopt.
6. Whether to accept any material residual financial, provider, concurrency, or
   recovery risk after independent review.
7. Whether and when to enable customer capital, operational withdrawals, broker
   connectivity, or live trading.
8. Whether separate independent review, penetration testing, financial controls,
   or external audit is required before each gate.

## 9. INDEPENDENT CLAUDE REVIEW QUESTIONS

1. Does this report distinguish a provider-neutral interface from a verified
   provider, or has it accidentally treated contracts as evidence?
2. Is the absence of a news provider fully established, including hidden
   configuration, dynamic imports, and deployment-only adapters that were not
   visible in the repository?
3. Does `NotConfiguredNewsProvider` block every signal path, or can another
   orchestration path bypass it?
4. Does the historical OHLC adapter actually have sufficient historical
   retention and licensed use for backtesting and customer decisions?
5. Can a historical decision be reproduced using the exact OHLC, news, session,
   valuation, and configuration state available at that time?
6. Are traditional macro events and crypto-specific material events covered by
   separate verified sources?
7. Are all risk inputs authoritative, or do zero/default fields silently bypass
   exposure, concentration, daily-loss, and strategy limits?
8. Can tenant identity be mismatched because `ResearchSignal` and
   `RiskSnapshot` do not enforce the same tenant context?
9. Can concurrent ledger reservations or withdrawal requests double-spend funds
   despite idempotency constraints?
10. What is the production database isolation level, and does `with_for_update`
    have the intended effect on that database?
11. Can a process crash leave reserved funds, a withdrawal request, or a risk
    decision in an unreconciled state?
12. Does route ordering make Founder withdrawal operations reachable?
13. Is Founder support access time-limited, purpose-bound, separately approved,
    and fully audited?
14. Are audit records tamper-evident, retained, exportable, and coupled to the
    financial mutation transaction?
15. What independent evidence supports the supplied 427-test count and the
    claimed migration head in the deployed database?
16. Which technical controls are being incorrectly treated as FCA, AML, KYC,
    client-money, custody, or UK GDPR compliance?
17. What prevents a future QTrade enablement from bypassing authorization,
    circuit-breaker, tenant, or account checks?
18. Are market-data and news provider licenses compatible with customer display,
    redistribution, signal generation, and historical storage?

## 10. FINAL STATUS MATRIX

| Workstream | Code Evidence | Production Evidence | Status | Launch Impact |
|---|---|---|---|---|
| News/Macro | Provider-neutral contract and explicit fail-closed missing-provider path; no approved adapter | None for real provider, coverage, retention, replay, licensing, SLA, or deployment | **BLOCKED / NOT VERIFIED** | Blocks live ARIMA signals and customer-capital decisions |
| Compliance | Authentication, authorization, ledger, withdrawal, audit, and disabled execution controls exist | No legal opinion, regulatory permission, KYC/AML, custody/client-money, retention, resilience, or production-control evidence | **NOT VERIFIED** | Blocks customer capital, operational withdrawals, and regulated/live use |
| Risk | Ledger, Decimal balances, risk contract, circuit checks, and dry-run code exist | No concurrency/recovery/production evidence; risk snapshot omits authoritative exposure/loss/concentration inputs; tenant propagation incomplete | **PARTIALLY SUPPORTED / NOT PRODUCTION-RISK COMPLETE** | Blocks live execution and customer capital |

### Final conclusion

The repository supports a fail-closed research/risk architecture in code, but
the supplied completion labels are stronger than the available evidence. The
high-impact-news blocker is genuinely supported. No evidence in this repository
supports live ARIMA signals, live broker/exchange execution, customer-capital
acceptance, or operational customer withdrawals.
