from pathlib import Path

from . import schemas, tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"


def register(ctx):
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        handler = getattr(tools, name)
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=handler,
        )

    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            # Hermes PluginContext.register_skill 期望 Path（内部调用 path.exists()），
            # 传 str 会报 'str' object has no attribute 'exists' 导致整个插件加载失败
            ctx.register_skill(child.name, skill_md)
