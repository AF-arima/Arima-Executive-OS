from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.market import (
    CanonicalInstrument,
    MarketDataConsumer,
    MarketDataService,
    MarketDataUnavailableError,
    MarketVerificationService,
    TwelveDataProvider,
    AlphaVantageProvider,
    get_market_data_configuration,
)
from app.tools.base import InternalToolAdapter
from app.tools.context import ToolExecutionContext
from app.tools.schemas import ToolCapability, ToolCategory


class MarketPriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: CanonicalInstrument


class WeatherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str | None = Field(default=None, max_length=120)


class RuntimeDateTool(InternalToolAdapter):
    name = "runtime.current_date"
    description = "Return the current date and weekday from the server runtime."
    category = ToolCategory.RUNTIME
    tool_capabilities = frozenset({ToolCapability.READ})

    def __init__(self, session: Any) -> None:
        self.session = session

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        local_now = context.current_timestamp.astimezone(ZoneInfo(context.timezone))
        evidence_id = str(uuid4())
        return {
            "date": local_now.date().isoformat(),
            "weekday": local_now.strftime("%A"),
            "timezone": context.timezone,
            "evidence": {
                "evidence_id": evidence_id,
                "content": (
                    "The server runtime date is "
                    f"{local_now.date().isoformat()} ({local_now.strftime('%A')}) "
                    f"in {context.timezone}."
                ),
            },
        }


class MarketPriceTool(InternalToolAdapter):
    name = "market.current_price"
    description = "Return an entitlement-verified current BTC/USD or XAU/USD price."
    category = ToolCategory.MARKET_DATA
    tool_capabilities = frozenset({ToolCapability.READ})
    input_model = MarketPriceInput

    def __init__(self, session: Any) -> None:
        self.session = session

    async def execute(self, payload: BaseModel, context: ToolExecutionContext) -> Any:
        request = MarketPriceInput.model_validate(payload)
        configuration = get_market_data_configuration()
        provider = (
            AlphaVantageProvider(configuration)
            if configuration.provider.value == "alpha_vantage"
            else TwelveDataProvider(configuration)
        )
        verification, _ = await MarketVerificationService(self.session).verify_and_record(
            provider, run_id=context.run.id
        )
        price, provenance = await provider.current_price(request.instrument)
        workspace_id = context.conversation.metadata_.get("workspace_id")
        if not isinstance(workspace_id, str):
            raise MarketDataUnavailableError("Market workspace context is unavailable")
        snapshot = await MarketDataService(self.session, configuration).snapshot_for(
            user=context.current_user,
            workspace_id=UUID(workspace_id),
            consumer=MarketDataConsumer.LEADERSHIP,
            verification=verification,
            provenance=provenance,
            now=provenance.received_at,
            customer_display=True,
        )
        evidence_id = str(uuid4())
        return {
            "instrument": request.instrument.value,
            "price": str(price),
            "currency": "USD",
            "provider": snapshot.provider.value,
            "observed_at": snapshot.as_of.isoformat(),
            "evidence": {
                "evidence_id": evidence_id,
                "content": (
                    f"Verified current {request.instrument.value} price is {Decimal(price)} USD "
                    f"from {snapshot.provider.value}, observed at {snapshot.as_of.isoformat()}."
                ),
            },
        }


class WeatherTool(InternalToolAdapter):
    name = "weather.current"
    description = "Return current observed weather for an explicitly supplied location."
    category = ToolCategory.WEATHER
    tool_capabilities = frozenset({ToolCapability.READ})
    input_model = WeatherInput

    def __init__(self, session: Any) -> None:
        self.session = session

    async def execute(self, payload: BaseModel, context: ToolExecutionContext) -> Any:
        request = WeatherInput.model_validate(payload)
        location = (request.location or "").strip()
        evidence_id = str(uuid4())
        if not location:
            return {"location_required": True, "evidence": {"evidence_id": evidence_id, "content": "A weather location was not supplied. Ask the user for a city or location; do not guess."}}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                geocode = await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": location, "count": 1, "language": "en", "format": "json"})
                geocode.raise_for_status()
                results = geocode.json().get("results", [])
                if not results:
                    raise RuntimeError("location_not_found")
                place = results[0]
                weather = await client.get("https://api.open-meteo.com/v1/forecast", params={"latitude": place["latitude"], "longitude": place["longitude"], "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m", "timezone": "UTC"})
                weather.raise_for_status()
                current = weather.json()["current"]
        except (httpx.HTTPError, KeyError, TypeError, RuntimeError, ValueError) as error:
            raise RuntimeError("Weather provider is unavailable") from error
        observed_at = current.get("time")
        if not isinstance(observed_at, str):
            raise RuntimeError("Weather provider is unavailable")
        display_location = ", ".join(filter(None, [place.get("name"), place.get("country")]))
        return {"location": display_location, "temperature_c": current.get("temperature_2m"), "apparent_temperature_c": current.get("apparent_temperature"), "weather_code": current.get("weather_code"), "wind_speed_kmh": current.get("wind_speed_10m"), "observed_at": observed_at, "provider": "open_meteo", "evidence": {"evidence_id": evidence_id, "content": f"Current weather from Open-Meteo for {display_location}: temperature {current.get('temperature_2m')} °C, apparent temperature {current.get('apparent_temperature')} °C, weather code {current.get('weather_code')}, wind {current.get('wind_speed_10m')} km/h, observed at {observed_at} UTC."}}
