from decimal import Decimal

SUPPORTED_ASSETS = frozenset({"BTC", "ETH", "USD"})
ASSET_QUANTUM = Decimal("0.000000000000000001")


def normalize_asset(value: str) -> str:
    asset = value.strip().upper()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError("Unsupported financial asset")
    return asset


def quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(ASSET_QUANTUM)
