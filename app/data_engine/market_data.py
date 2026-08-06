import yfinance as yf


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
