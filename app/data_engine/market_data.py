# This internal experimental module is deliberately not mounted by the API
# until it has an authenticated provider contract and a declared dependency.
import yfinance as yf  # type: ignore[import-untyped]


def get_market_price(symbol: str):
    ticker = yf.Ticker(symbol)

    data = ticker.history(period="1d")

    if data.empty:
        return None

    price = data["Close"].iloc[-1]

    return {
        "symbol": symbol,
        "price": float(price)
    }


if __name__ == "__main__":
    print(get_market_price("GC=F"))
