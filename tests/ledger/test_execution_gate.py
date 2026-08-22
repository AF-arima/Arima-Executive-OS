from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.trading_contracts import DisabledQTradeExecution


@pytest.mark.asyncio
async def test_qtrade_execution_is_disabled_fail_closed():
    with pytest.raises(RuntimeError, match="not enabled"):
        await DisabledQTradeExecution().submit_order(
            workspace_id=uuid4(), account_id=uuid4(), asset="ETH", quantity=Decimal("1")
        )
