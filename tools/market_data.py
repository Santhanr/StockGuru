"""Market data tools via yfinance (free, no API key)."""
import json
from langchain_core.tools import tool


@tool
def get_pe_ratio(ticker: str) -> str:
    """Get the current P/E ratio and key valuation metrics for a stock."""
    try:
        import yfinance as yf  # type: ignore
        info = yf.Ticker(ticker).info
        pe = info.get("trailingPE") or info.get("forwardPE")
        return json.dumps({
            "ticker": ticker,
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "priceToBook": info.get("priceToBook"),
            "marketCap": info.get("marketCap"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
        }, indent=2)
    except Exception as exc:
        return f"[get_pe_ratio error] {exc}"


@tool
def get_price_history(ticker: str, period: str = "3mo") -> str:
    """Get recent price history for a stock. period examples: 1mo, 3mo, 6mo, 1y."""
    try:
        import yfinance as yf  # type: ignore
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return f"No price history found for {ticker}."
        # Return last 20 rows as compact JSON
        recent = hist.tail(20)[["Open", "High", "Low", "Close", "Volume"]]
        records = []
        for dt, row in recent.iterrows():
            records.append({
                "date": str(dt.date()),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        return json.dumps({"ticker": ticker, "period": period, "prices": records}, indent=2)
    except Exception as exc:
        return f"[get_price_history error] {exc}"


@tool
def get_technical_indicators(ticker: str) -> str:
    """Get technical indicators: 50-day MA, 200-day MA, RSI(14), volume trend."""
    try:
        import yfinance as yf  # type: ignore
        hist = yf.Ticker(ticker).history(period="1y")
        if hist.empty or len(hist) < 14:
            return f"Insufficient data for technical indicators for {ticker}."

        close = hist["Close"]
        volume = hist["Volume"]

        ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        # RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float(100 - (100 / (1 + rs.iloc[-1])))

        # Volume trend: avg last 5 days vs avg prior 20 days
        vol_recent = float(volume.iloc[-5:].mean())
        vol_prior = float(volume.iloc[-25:-5].mean()) if len(volume) >= 25 else vol_recent
        vol_ratio = round(vol_recent / vol_prior, 2) if vol_prior else 1.0

        return json.dumps({
            "ticker": ticker,
            "current_price": round(float(close.iloc[-1]), 2),
            "ma_50": round(ma50, 2) if ma50 else None,
            "ma_200": round(ma200, 2) if ma200 else None,
            "rsi_14": round(rsi, 2),
            "volume_ratio_5d_vs_20d": vol_ratio,
            "note": "volume_ratio > 1 = recent volume above 20-day avg",
        }, indent=2)
    except Exception as exc:
        return f"[get_technical_indicators error] {exc}"
