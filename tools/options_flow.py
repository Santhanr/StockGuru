"""Unusual options flow via yfinance (free, no API key)."""
import json
from langchain_core.tools import tool


@tool
def get_unusual_options_flow(ticker: str) -> str:
    """Get unusual options activity: strikes where volume > 2x open interest."""
    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return f"No options data available for {ticker}."

        # Check the nearest 3 expirations
        unusual = []
        for exp in expirations[:3]:
            chain = t.option_chain(exp)
            for side, df in [("calls", chain.calls), ("puts", chain.puts)]:
                for _, row in df.iterrows():
                    vol = row.get("volume", 0) or 0
                    oi = row.get("openInterest", 0) or 0
                    if oi > 0 and vol > 2 * oi and vol > 500:
                        unusual.append({
                            "expiration": exp,
                            "type": side,
                            "strike": float(row["strike"]),
                            "volume": int(vol),
                            "open_interest": int(oi),
                            "vol_oi_ratio": round(vol / oi, 1),
                            "implied_volatility": round(float(row.get("impliedVolatility", 0)), 3),
                        })

        if not unusual:
            return json.dumps({
                "ticker": ticker,
                "unusual_flow": [],
                "note": "No unusual options flow detected (no strike with volume > 2x OI and volume > 500).",
            }, indent=2)

        unusual.sort(key=lambda x: x["vol_oi_ratio"], reverse=True)
        return json.dumps({
            "ticker": ticker,
            "unusual_flow": unusual[:20],
            "note": "Sorted by volume/OI ratio descending. Flags volume > 2x open interest.",
        }, indent=2)

    except Exception as exc:
        return f"[get_unusual_options_flow error] {exc}"
