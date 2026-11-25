"""LLM calls through an OpenAI-compatible endpoint (POSTECH GenAI by default) with a weekly token cap."""
import datetime as dt
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

import db

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = os.getenv("LLM_BASE_URL", "https://genai.postech.ac.kr/agent/openai_compatible/a1/v1")
MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")   # answers without hidden reasoning; claude-haiku-5-5 spent every token on it
WEEKLY_CAP = int(os.getenv("LLM_WEEKLY_TOKEN_CAP", "15000"))


class TokenCapReached(Exception):
    pass


class EmptyAnswer(Exception):
    """The model answered with no text (its tokens went to hidden reasoning)."""


def week_start():
    today = dt.date.today()
    return (today - dt.timedelta(days=today.weekday())).isoformat()  # Monday


def used_this_week(con):
    return db.tokens_since(con, week_start())


def chat(con, messages, purpose, max_tokens=400, model=None):
    """Send one chat request. Refuses to call when the weekly cap would be exceeded. Returns the reply text."""
    model = model or MODEL
    used = used_this_week(con)
    estimate = sum(len(m["content"]) for m in messages) + max_tokens  # rough upper bound (1 token per character)
    if used + estimate > WEEKLY_CAP:
        raise TokenCapReached(f"used {used} of {WEEKLY_CAP} this week; this call may need up to {estimate}")

    headers = {"Content-Type": "application/json"}
    key = os.getenv("POSTECH_API_KEY")
    if key:
        headers["x-api-key"] = key
    r = requests.post(
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": max_tokens},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage") or {}
    if not usage:
        print("Warning: the response has no usage field; tokens for this call are recorded as 0.")
    db.log_tokens(con, model, purpose, usage)
    choice = data["choices"][0]
    text = choice["message"].get("content") or ""
    if not text.strip():
        # Models that think by default (Claude Haiku 5.5, for one) can spend every allowed token on hidden
        # reasoning and return no text. The proxy rejects the settings that would turn that off.
        raise EmptyAnswer(f"{model} returned no text ({choice.get('finish_reason')}, "
                          f"{usage.get('completion_tokens', '?')} of {max_tokens} tokens used on hidden reasoning)")
    return text


if __name__ == "__main__":
    con = db.connect()
    print("model:", MODEL, "| endpoint:", BASE_URL)
    print(f"tokens used this week (since {week_start()}): {used_this_week(con)} of {WEEKLY_CAP}")