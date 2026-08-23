from __future__ import annotations

from dataclasses import dataclass
import re

_MAX_RESPONSE_LENGTH = 4_000
_ACTION_CLAIM = re.compile(
    r"\b(?:i|we|arima)\s+(?:have\s+)?(?:executed|sent|created|updated|"
    r"modified|changed|granted|revoked|traded|bought|sold|emailed|deleted)\b",
    re.IGNORECASE,
)
_ACTION_DIRECTIVE = re.compile(
    r"\b(?:execute|send|create|update|modify|change|grant|revoke|trade|buy|"
    r"sell|email|delete)\s+(?:a\s+|an\s+|the\s+|your\s+)?"
    r"(?:trade|order|email|record|setting|permission|portfolio)\b",
    re.IGNORECASE,
)
_STATE_CLAIM = re.compile(
    r"(?:\b(?:portfolio|risk|trade|balance|permission|execution)\b|"
    r"\bsystem\s+(?:state|setting|configuration)\b)",
    re.IGNORECASE,
)
_UNAVAILABLE = re.compile(r"\b(?:unavailable|not available|cannot verify)\b", re.IGNORECASE)
_INTERNAL_RESPONSE = re.compile(
    r"(?:"
    r"\bthe user's request\s*(?:is|was)\b|"
    r"\baccording to (?:the )?(?:system|developer) prompt\b|"
    r"\bthe evidence payload\s+is\b|"
    r"\bthe canonical payload\s+(?:does not|is)\b|"
    r"\bi must respond according to\b|"
    r"<\/?think(?:\s|>)"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ValidatedResponse:
    content: str
    accepted: bool
    reason: str | None = None


class ResponseValidator:
    """Small fail-closed boundary for provider text before it reaches Voice."""

    fallback = (
        "I can only provide read-only, evidence-backed information. The "
        "requested information is unavailable in the authorised workspace context."
    )

    def validate(
        self,
        content: str,
        *,
        allowed_evidence_ids: frozenset[str],
    ) -> ValidatedResponse:
        value = content.strip()
        if not value or len(value) > _MAX_RESPONSE_LENGTH:
            return self._fallback("empty_or_too_long")
        if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
            return self._fallback("malformed")
        if _INTERNAL_RESPONSE.search(value):
            return self._fallback("internal_response")
        if _ACTION_CLAIM.search(value) or _ACTION_DIRECTIVE.search(value):
            return self._fallback("action_claim")
        if _STATE_CLAIM.search(value) and not _UNAVAILABLE.search(value):
            cited = frozenset(re.findall(r"\[evidence:([^\]]+)\]", value))
            if not cited.intersection(allowed_evidence_ids):
                return self._fallback("unsupported_state_claim")
        return ValidatedResponse(content=value, accepted=True)

    def _fallback(self, reason: str) -> ValidatedResponse:
        return ValidatedResponse(
            content=self.fallback,
            accepted=False,
            reason=reason,
        )
