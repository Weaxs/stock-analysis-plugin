#!/usr/bin/env python3
"""Report renderer — turn structured stock/market JSON into markdown via existing j2 templates.

No side effects: does not save, push, or upload. Just returns markdown.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    print(json.dumps({"error": "jinja2 not installed. pip install jinja2"}, ensure_ascii=False))
    sys.exit(1)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_ROOT = PROJECT_ROOT / "templates"

TEMPLATE_MAP = {
    "stock": {
        "brief": "stock_analysis/brief.md.j2",
        "full": "stock_analysis/full.md.j2",
    },
    "market": {
        "full": "market_review/full.md.j2",
    },
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_ROOT)),
        autoescape=select_autoescape(disabled_extensions=("j2", "md")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(kind: str, template: str, report: dict) -> dict:
    if kind not in TEMPLATE_MAP:
        return {"error": f"unknown kind: {kind}. valid: {list(TEMPLATE_MAP)}"}
    if template not in TEMPLATE_MAP[kind]:
        return {"error": f"unknown template '{template}' for {kind}. valid: {list(TEMPLATE_MAP[kind])}"}

    tpl_path = TEMPLATE_MAP[kind][template]
    try:
        tpl = _env().get_template(tpl_path)
    except Exception as e:
        return {"error": f"failed to load template {tpl_path}: {e}"}

    try:
        content = tpl.render(**report)
    except Exception as e:
        return {"error": f"render failed: {e}", "template": tpl_path}

    return {
        "meta": {
            "provider": "renderer",
            "template": tpl_path,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "instant",
            "fallback_used": False,
            "warnings": [],
        },
        "format": "markdown",
        "content": content,
    }


def _load_input(path: str | None, b64: str | None) -> dict:
    if b64:
        import base64

        return json.loads(base64.b64decode(b64).decode("utf-8"))
    if path and path != "-":
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


def main():
    parser = argparse.ArgumentParser(description="Render stock/market report from structured JSON")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("stock", help="Render a stock analysis report")
    p.add_argument("--template", default="full", choices=["brief", "full"])
    p.add_argument("--input", "-i", default="-", help="JSON file path or '-' for stdin")
    p.add_argument("--input-b64", help="Base64-encoded JSON (avoids shell escaping)")

    p2 = sub.add_parser("market", help="Render a market review report")
    p2.add_argument("--template", default="full", choices=["full"])
    p2.add_argument("--input", "-i", default="-", help="JSON file path or '-' for stdin")
    p2.add_argument("--input-b64", help="Base64-encoded JSON (avoids shell escaping)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    report = _load_input(args.input, getattr(args, "input_b64", None))
    result = render(args.command, args.template, report)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
