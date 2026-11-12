import functools

from . import schemas, tools

PROJECT_ROOT = tools.PROJECT_ROOT
SKILLS_DIR = PROJECT_ROOT / "skills"


def _without_nulls(handler):
    # Explicit null optional args would otherwise reach argv as "None"; dropping them applies defaults.
    @functools.wraps(handler)
    def wrapper(args, **kwargs):
        if isinstance(args, dict):
            args = {key: value for key, value in args.items() if value is not None}
        return handler(args, **kwargs)

    return wrapper


def register(ctx):
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=_without_nulls(getattr(tools, name)),
        )

    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            # Hermes PluginContext.register_skill 期望 Path（内部调用 path.exists()），
            # 传 str 会报 'str' object has no attribute 'exists' 导致整个插件加载失败
            ctx.register_skill(child.name, skill_md)
