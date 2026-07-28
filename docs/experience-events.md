# Experience events

The Living Neural Intelligence Experience is driven by a response-only
experience-event envelope. It is intentionally an adapter over existing voice,
orchestration, tool, integration, background, approval, telemetry, audit and
permission services; it does not execute work or introduce a second
orchestration pipeline.

## Contract

VoiceGatewayResponse.experience_events contains zero or more ExperienceEvent
values. Each event includes:

- event_id, session_id, correlation_id and timestamp
- type, priority, source and optional target_chamber
- serialisable payload
- optional duration_hint
- dismissible and requires_attention

The stable chambers are executive, portfolio, quant, growth, projects,
publications, approvals and health.

Supported visual event types are avatar state, neural activity, chamber
transition, data object, task, watchlist, performance, approval, warning,
system pulse and background-job visualisations.

## Mapping

ExperienceEventMapper translates the existing VoiceEvent lifecycle into visual
instructions. It maps thought, tool, approval, response, navigation, speaking,
completion and failure events without changing their security or execution
semantics.

When a request reaches the existing orchestration engine, the same mapper also
derives object, telemetry, task, background, portfolio, quant, approval and
warning visualisations from the returned OrchestrationResult.

Existing services remain authoritative:

- RBAC and agent permissions are enforced before tools, integrations and jobs.
- Existing audit and execution logging continue to record work.
- Approval engines own approval state; the experience event only asks the
  client to display it.
- Provider, model and tool selection remain within orchestration.

## Client behaviour

Clients should treat events as presentation instructions:

- map avatar events to visual state;
- perform a staged local chamber transition before updating the displayed
  chamber route;
- label demo payloads as simulated;
- expose approvals as a local UI review path until a real approval API action is
  intentionally connected;
- degrade safely when events are absent by using the existing voice events.

The frontend does not upload raw audio. Browser recognition and browser speech
synthesis remain client-side.

## Current limitations

Events are delivered within normal voice-gateway responses; there is no
websocket or server-sent-event transport in this milestone. The mapper creates
deterministic architecture-safe events and does not claim live market data,
external execution, OAuth access or real-time job streaming.
