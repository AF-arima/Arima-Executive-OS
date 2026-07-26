from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from app.execution.types import CostBreakdown, StructuredPrompt

GBP_QUANTUM = Decimal("0.000001")
TOKEN_UNIT = Decimal("1000")


class TokenEstimator(Protocol):
    def estimate_text(self, text: str) -> int: ...

    def estimate_prompt(self, prompt: StructuredPrompt) -> int: ...


class MockTokenEstimator:
    def estimate_text(self, text: str) -> int:
        return max(1, len(text.split())) if text else 0

    def estimate_prompt(self, prompt: StructuredPrompt) -> int:
        return self.estimate_text(prompt.text())


@dataclass(frozen=True, slots=True)
class Pricing:
    prompt_per_thousand_gbp: Decimal
    completion_per_thousand_gbp: Decimal

    def __post_init__(self) -> None:
        if self.prompt_per_thousand_gbp < 0:
            raise ValueError("Prompt pricing cannot be negative")
        if self.completion_per_thousand_gbp < 0:
            raise ValueError("Completion pricing cannot be negative")


class PricingStrategy(Protocol):
    def pricing(self, provider_name: str, model_name: str | None) -> Pricing: ...


class ZeroPricingStrategy:
    def pricing(self, provider_name: str, model_name: str | None) -> Pricing:
        del provider_name, model_name
        return Pricing(
            prompt_per_thousand_gbp=Decimal("0"),
            completion_per_thousand_gbp=Decimal("0"),
        )


class StaticPricingStrategy:
    def __init__(self, pricing: Pricing) -> None:
        self._pricing = pricing

    def pricing(self, provider_name: str, model_name: str | None) -> Pricing:
        del provider_name, model_name
        return self._pricing


class CostEstimator:
    def __init__(self, strategy: PricingStrategy) -> None:
        self.strategy = strategy

    def estimate(
        self,
        *,
        provider_name: str,
        model_name: str | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> CostBreakdown:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("Token counts cannot be negative")
        pricing = self.strategy.pricing(provider_name, model_name)
        prompt_cost = (
            Decimal(prompt_tokens)
            / TOKEN_UNIT
            * pricing.prompt_per_thousand_gbp
        ).quantize(GBP_QUANTUM, rounding=ROUND_HALF_UP)
        completion_cost = (
            Decimal(completion_tokens)
            / TOKEN_UNIT
            * pricing.completion_per_thousand_gbp
        ).quantize(GBP_QUANTUM, rounding=ROUND_HALF_UP)
        return CostBreakdown(
            prompt_cost_gbp=prompt_cost,
            completion_cost_gbp=completion_cost,
            total_cost_gbp=(prompt_cost + completion_cost).quantize(
                GBP_QUANTUM,
                rounding=ROUND_HALF_UP,
            ),
        )
