"""Test-only hook: record tool names, never arguments or results."""

import json
import math
import os
from pathlib import Path


def register(ctx):
    def observe(tool_name, result=None, status=None, **_kwargs):
        if tool_name != "get_quote" or status != "ok":
            return
        try:
            quote = json.loads(result) if isinstance(result, str) else result
        except ValueError:
            return
        if not isinstance(quote, dict) or "error" in quote:
            return
        price = quote.get("price")
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
            return
        with Path(os.environ["HERMES_SMOKE_TOOL_LOG"]).open("a", encoding="utf-8") as log:
            log.write(json.dumps(tool_name) + "\n")

    ctx.register_hook("post_tool_call", observe)
