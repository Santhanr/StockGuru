"""Thin wrapper around slack-sdk WebClient."""
from __future__ import annotations

import config


def post_message(text: str, thread_ts: str | None = None, token: str | None = None) -> str:
    """Post to SLACK_CHANNEL_ID. Returns ts for threading.

    thread_ts=None opens a new thread; pass an existing ts to reply.
    token overrides the default SLACK_BOT_TOKEN (used by analyst/auditor bots).
    Returns empty string and prints a warning if Slack is not configured.
    """
    effective_token = token or config.SLACK_BOT_TOKEN
    if not effective_token or not config.SLACK_CHANNEL_ID:
        print(f"[Slack not configured] {text}")
        return ""

    try:
        from slack_sdk import WebClient  # type: ignore
        client = WebClient(token=effective_token)
        kwargs: dict = {"channel": config.SLACK_CHANNEL_ID, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp = client.chat_postMessage(**kwargs)
        return resp["ts"]
    except Exception as exc:
        safe_exc = str(exc).encode("ascii", errors="replace").decode("ascii")
        safe_text = text.encode("ascii", errors="replace").decode("ascii")
        print(f"[Slack error] {safe_exc}\n[Message] {safe_text}")
        return ""
