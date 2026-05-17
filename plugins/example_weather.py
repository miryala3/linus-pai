"""
AIO Example Plugin — weather tool + /weather endpoint
Drop this file (or your own .py) into aio_data/plugins/ to auto-load.

Plugin contract:
  TOOLS dict[str, callable]   → agent tools  (key = tool name, value = fn(args_str)->str)
  register_routes(app, **ctx) → FastAPI routes
"""
import json
import urllib.request


def _weather(location: str) -> str:
    location = location.strip().strip('"\'')
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
        with urllib.request.urlopen(url, timeout=8) as r:
            return r.read().decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        return f"[weather error: {exc}]"


import urllib.parse   # noqa: E402  (imported after use in _weather to keep example readable)


TOOLS = {"weather": _weather}


def register_routes(app, **ctx):
    """Register a /weather/{location} endpoint on the AIO FastAPI app."""
    @app.get("/weather/{location}")
    def weather(location: str):
        return {"location": location, "weather": _weather(location)}
