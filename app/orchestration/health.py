from datetime import datetime, timezone

from app.orchestration.schemas import ComponentHealth


class HealthContract:
    component_name = "component"

    async def health(self) -> ComponentHealth:
        return ComponentHealth(
            component=self.component_name,
            available=True,
            healthy=True,
            checked_at=datetime.now(timezone.utc),
            detail="Deterministic orchestration component",
        )
