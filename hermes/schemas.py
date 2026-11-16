import json

from .tools import TOOLS_DIR

# One schema file for every host: Pi/OpenClaw/dsh import the same JSON (see scripts/tool-calls.mjs).
TOOL_SCHEMAS = json.loads((TOOLS_DIR / "tool_schemas.json").read_text(encoding="utf-8"))
