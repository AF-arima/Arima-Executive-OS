# Voice Gateway

The Voice Gateway is the provider-neutral text boundary between browser speech
capabilities and Arima's existing orchestration platform. The browser performs
speech recognition and synthesis; the backend receives only final transcript
text and returns response text, ordered events, navigation/panel actions, and
approval requests.

## Architecture

- `VoiceSessionStore` durably persists session state in PostgreSQL.
- `VoiceGateway` validates session ownership and state transitions, resolves
  deterministic UI commands, and delegates every unknown request to
  `OrchestrationEngine.execute()`.
- `VoiceOrchestrationContextFactory` creates or resumes an Agent Platform
  conversation and creates a run using existing services and RBAC.
- Growth Studio commands require `administrator`, `executive`, or `manager`.
- Existing orchestration permissions, approvals, tools, integrations, memory,
  telemetry, and audit behavior remain authoritative.
- `SpeechToTextProvider` and `TextToSpeechProvider` are future-facing contracts.
  Their mock implementations do not call a network or speech SDK.

## Endpoints

All endpoints require an active authenticated user:

- `POST /api/v1/voice/sessions`
- `GET /api/v1/voice/sessions/{session_id}`
- `POST /api/v1/voice/sessions/{session_id}/transcript`
- `POST /api/v1/voice/sessions/{session_id}/interrupt`
- `POST /api/v1/voice/sessions/{session_id}/cancel`
- `GET /api/v1/voice/health`

The transcript endpoint accepts browser-generated text, never raw audio.

## Environment

| Variable | Default |
| --- | --- |
| `ARIMA_VOICE_ENABLED` | `true` |
| `ARIMA_VOICE_DEFAULT_LANGUAGE` | `en` |
| `ARIMA_VOICE_DEFAULT_LOCALE` | `en-GB` |
| `ARIMA_VOICE_MAX_TRANSCRIPT_LENGTH` | `10000` |
| `ARIMA_VOICE_SESSION_TIMEOUT_SECONDS` | `1800` |

No variable contains a secret.

## Local use

Run the API using the repository's normal FastAPI command, authenticate through
the existing `/api/v1/auth` routes, create a voice session, then submit final
transcript text. Configure the website's `NEXT_PUBLIC_ARIMA_API_URL` to this API
origin and make an access token available through the website authentication
integration.

## Known limitations

- Sessions are process-local and are not shared across workers or restarts.
- Session timeout is configuration for the future shared-store adapter; no
  background expiration worker is introduced in this milestone.
- Events are returned as ordered arrays; there is no WebSocket transport.
- Speech quality, supported languages, microphone behavior, and available
  voices are controlled by the user's browser and operating system.
- A seeded active default agent is required for non-command orchestration.
