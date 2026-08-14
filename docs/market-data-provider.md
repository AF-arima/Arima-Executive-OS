# Market data architecture and activation

Phase 3 uses a provider-agnostic, server-side contract with Twelve Data as the
selected adapter. The public API remains non-price and fail-closed. A provider
credential alone never authorizes customer display or redistribution.

## Architecture

```text
Twelve Data
    -> TwelveDataProvider (authentication and non-price metadata verification)
    -> strict canonical identity validation
    -> ProviderVerification and per-instrument entitlement state
    -> MarketDataProvenance and freshness validation
    -> MarketDataService (workspace authorization and consumer allowlist)
    -> canonical non-price MarketSnapshot
    -> approved internal consumer
```

Q Lab, QResearch, Leadership, avatars, and other agents must use
`MarketDataService`. They must not call Twelve Data directly. The service
rejects provider/source mismatches, unverified symbols, missing real-time
evidence, stale or unknown timestamps, cross-workspace access, and unapproved
consumer identifiers.

## Canonical mappings

- `XAUUSD`: `XAU/USD`, `Gold Spot / US Dollar`, instrument type `Commodity`,
  source/exchange `Commodity`, currency `USD`. It is not modeled as ordinary FX.
- `BTCUSD`: `BTC/USD`, `Bitcoin to US Dollar`, instrument type
  `Digital Currency`, exchange `Coinbase Pro`, currency `USD`. Alternate
  exchanges do not silently satisfy this mapping.
- `SPX`: `SPX`, `S&P 500 Index`, instrument type `Index`, candidate exchange
  `CBOE`, currency `USD`. SPY, IVV, VOO, ETFs, CFDs, stocks, and proxies fail
  identity validation. SPX stays unavailable until an authenticated provider
  response proves this exact identity and entitlement.

## Verification workflow

Credentialed verification uses `httpx` and only runs when
`TWELVE_DATA_API_KEY` is configured server-side:

1. `GET /api_usage` verifies authentication, provider reachability, and the
   configured account-plan category.
2. `GET /symbol_search?symbol=...&show_plan=true` verifies the exact
   `symbol`, `instrument_name`, `exchange`, `instrument_type`, `currency`, and
   `access.global`, `access.plan`, and `access.plan_business` fields.
3. `GET /quote` verifies the provider timestamp and exact response identity.
   Price-bearing fields are ignored by the typed parser, are never retained,
   and never reach persistence or an API response.
4. Responses are validated with strict typed schemas. HTTP success alone is not
   authentication, symbol, real-time, or entitlement evidence.

The API key is read through `Settings` as `SecretStr`, sent only in the server
authorization header, and excluded from responses, snapshots, logs, and
diagnostics. The verification result retains normalized evidence only; it never
stores raw provider payloads.

Each run is persisted append-only in `market_provider_verifications`. The table
contains normalized verification booleans, state, reason, canonical symbol,
freshness, and timestamps only. It has no credential, raw-payload, price,
quote, candle, or other market-value column.

## Verification states

The internal state machine distinguishes:

- `NOT_CONFIGURED`
- `CONFIGURED`
- `AUTHENTICATION_FAILED`
- `PROVIDER_UNAVAILABLE`
- `SYMBOL_UNVERIFIED`
- `ENTITLEMENT_UNVERIFIED`
- `VERIFIED_INTERNAL`
- `VERIFIED_CUSTOMER_DISPLAY`
- `STALE`
- `RATE_LIMITED`
- `ERROR`

Each instrument separately records provider, symbol, source, real-time, catalog
access, customer-display, and redistribution verification. These concepts are
not inferred from one another.

## Entitlement model

Basic access is internal non-display only. It may reach `VERIFIED_INTERNAL`
after authentication, identity, plan, and real-time evidence are satisfied, but
it cannot enable customer display.

Customer display and redistribution require all of the following:

- a plan that satisfies provider metadata;
- `MARKET_DATA_REAL_TIME_ENTITLED=true`;
- `MARKET_DATA_CUSTOMER_DISPLAY_ENTITLED=true`;
- for redistribution, `MARKET_DATA_REDISTRIBUTION_ENTITLED=true`;
- a non-empty server-only `MARKET_DATA_ENTITLEMENT_REFERENCE` identifying the
  written commercial approval or contract.

Redistribution requires customer-display rights. Invalid combinations prevent
configuration from loading. The entitlement reference is a secret and is never
serialized. These flags represent reviewed written rights; they must never be
enabled merely because an API request succeeds.

## Provenance, freshness, and snapshot contract

`MarketDataProvenance` records canonical symbol, provider, source, provider
symbol, exchange, provider timestamp, receipt timestamp, and stale threshold.
Timestamps must be timezone-aware. Freshness is `FRESH`, `STALE`, or `UNKNOWN`.
Missing provider timestamps produce `UNKNOWN`; stale and unknown observations
fail closed.

`MarketSnapshot` contains only:

- symbol
- provider and source
- record type
- `as_of` and `fetched_at`
- verification status
- provenance
- freshness

There are no price, candle, quote, signal, fake, zero, default, or demo fields.
A later price-bearing contract must pass through the same verification,
entitlement, freshness, and workspace gates before it can be implemented.

## Failure behavior

Missing credentials return `NOT_CONFIGURED` without an HTTP request. Invalid
credentials return `AUTHENTICATION_FAILED`. Timeouts and connection failures
return `PROVIDER_UNAVAILABLE`. HTTP 429 returns `RATE_LIMITED`. Malformed
responses return `ERROR`. Identity and source mismatches return
`SYMBOL_UNVERIFIED`. Missing plan or commercial evidence returns
`ENTITLEMENT_UNVERIFIED` or `VERIFIED_INTERNAL`, never customer availability.

The authenticated `GET /api/v1/market/availability` endpoint exposes none of
these internal diagnostics. Until customer-display activation is separately
approved, every instrument returns `available=false`, no provider, and no price.

## Activation procedure

1. Provision `TWELVE_DATA_API_KEY` in the deployment secret manager. Never put
   it in source control, browser configuration, chat, or client payloads.
2. Set the reviewed account plan and internal usage scope.
3. Run the server-side verification workflow and review exact identity and plan
   evidence for XAUUSD, BTCUSD, and especially SPX.
4. Obtain written customer-display and, if needed, redistribution rights.
5. Store the approval identifier in `MARKET_DATA_ENTITLEMENT_REFERENCE`, then
   enable only the approved entitlement flags.
6. Re-run verification. Do not add customer price exposure unless every target
   instrument reaches `VERIFIED_CUSTOMER_DISPLAY` and the product has passed a
   separate security and licensing review.

Official contract references:

- https://twelvedata.com/docs/advanced
- https://twelvedata.com/markets/300755/commodity/xau-usd
- https://twelvedata.com/exchanges/COMMODITY?group=reference
- https://twelvedata.com/markets/499377/crypto/coinbase-pro/btc-usd
- https://twelvedata.com/docs/markets/exchanges
- https://twelvedata.com/indices
- https://twelvedata.com/pricing
- https://twelvedata.com/pricing-business
