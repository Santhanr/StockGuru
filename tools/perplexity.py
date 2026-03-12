"""Search the web via Perplexity sonar (cheap, real-time)."""
import requests
from langchain_core.tools import tool
import config


@tool
def search_web(query: str) -> str:
    """Search the web for real-time financial news and research."""
    if not config.PERPLEXITY_API_KEY:
        return "[search_web] PERPLEXITY_API_KEY not set — skipping web search."

    headers = {
        "Authorization": f"Bearer {config.PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.PERPLEXITY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a financial research assistant. "
                    "Provide concise, factual answers with source context."
                ),
            },
            {"role": "user", "content": query},
        ],
    }

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"[search_web error] {exc}"
