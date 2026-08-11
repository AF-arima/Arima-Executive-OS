# Founder Control Center

Founder Control is a server-authorized operational surface at
`/api/v1/admin/founder`. It never returns provider credentials or creates
business metrics. Manual entries are provenance records only and are recorded
with an audit event and request correlation ID.

## Production setup

Configure the allowlist only in the backend deployment environment:

```dotenv
FOUNDER_CONTROL_EMAILS=founder@example.com
```

The value may be a comma-separated list. It is intentionally empty by default,
which denies every request without preventing the API from starting.

Each allowlisted user must also be active, email-verified, and assigned the
existing `administrator` role through the controlled platform-operator
workflow. Registration grants `manager`, never Founder access; there is no
self-elevation endpoint.

## Endpoints

- `GET /api/v1/admin/founder/system-health`
- `GET /api/v1/admin/founder/data-feeds`
- `POST /api/v1/admin/founder/data-feeds/{feed_key}/observations`

The POST endpoint requires the authenticated access token and the existing
double-submit CSRF cookie/header pair. It accepts source, timezone-aware
observed time, optional expiry time, and optional factual notes. Feed keys are
server-catalogued; unknown keys return `404`.

The current feed catalog reports unavailable/manual provenance only. It does
not represent a market, portfolio, document, or Quant Engine data contract.
