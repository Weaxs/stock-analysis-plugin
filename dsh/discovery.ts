import type { Context } from "@deepseek-ai/cordis";
import { defineTool, type ToolRegistrationOptions } from "@deepseek-ai/dsh-tools";
import type {} from "@deepseek-ai/dsh-system-prompt";

const SEARCH = "stock_tool_search";

// Returns the per-tool registration option; stock tools stay hidden per agent until searched.
export function createToolDiscovery(ctx: Context): (tool: {name: string; description: string}) => ToolRegistrationOptions | undefined {
  if (typeof ctx.tools.resolve !== "function" || ctx.tools.get(SEARCH)) return () => undefined;
  const names = new Map<string, string>();
  const loaded = new WeakMap<object, Set<string>>();
  const ptc = new WeakSet<object>();
  // Search restricted away, or a PTC SDK presentation: expose the full catalog and reset loading.
  const active = (agent: object) => {
    if (ctx.tools.get(SEARCH, agent as never) && !ptc.has(agent)) return true;
    loaded.delete(agent);
    return false;
  };
  ctx.tools.register(defineTool({
    name: SEARCH,
    description: "搜索并加载股票分析工具；支持中文、英文关键词或确切工具名，如财务、get_quote、run_backtest。匹配工具将在下一次模型请求提供完整参数，可按原名直接调用。不会执行分析或扩大权限。",
    parameters: {
      query: {type: "string", required: true, description: "工具名称或功能关键词"},
      limit: {type: "integer", description: "最多加载数量，默认5，范围1到20"},
    },
    output: {schema: {type: "string"}, render: (_args, value) => [{type: "text", text: value}]},
    async execute({query, limit = 5}, exec) {
      const needle = query.trim().toLowerCase();
      const tokens = needle.match(/[a-z0-9_]+|\p{Script=Han}/gu) ?? [];
      const bigrams = (needle.match(/\p{Script=Han}+/gu) ?? [])
        .flatMap(text => [...text].slice(0, -1).map((_, index) => text.slice(index, index + 2)));
      const terms = [...new Set([...tokens, ...bigrams])];
      if (!terms.length || !Number.isInteger(limit) || limit < 1 || limit > 20) {
        return JSON.stringify({error: "query must contain searchable text and limit must be an integer from 1 to 20"});
      }
      if (!exec.agent) return JSON.stringify({error: "Tool discovery is unavailable for this agent; use its directly exposed tools"});
      const agent = exec.agent;
      const candidates = [...names]
        .filter(([name]) => ctx.tools.get(name, agent))
        .map(([name, description]) => ({tool: {name, description}, text: `${name} ${description}`.toLowerCase()}));
      // Generic stock terms should not outrank the specific capability being searched.
      const weights = terms.map(term => 1 / (1 + candidates.filter(({text}) => text.includes(term)).length));
      const matches = candidates
        .map(({tool, text}) => {
          const score = (tool.name === needle ? 1000 : 0) + (text.includes(needle) ? 10 : 0)
            + terms.reduce((sum, term, index) => sum + weights[index]
              * (tool.name.includes(term) ? 3 : text.includes(term) ? 1 : 0), 0);
          return {tool, score};
        })
        .filter(match => match.score > 0)
        .sort((a, b) => b.score - a.score || a.tool.name.length - b.tool.name.length || a.tool.name.localeCompare(b.tool.name))
        .slice(0, limit);
      const set = loaded.get(agent) ?? new Set<string>();
      for (const {tool} of matches) set.add(tool.name);
      loaded.set(agent, set);
      return JSON.stringify({loaded: matches.map(({tool}) => tool)});
    },
  }));

  // `when` runs before assembly in a step, so PTC detection applies from the next step.
  ctx.on("system-prompt/assemble", async (_assembly, context, next) => {
    const result = await next();
    if (context.scope) {
      if (result.sections.some(section => section.name === "tools:sdk")) ptc.add(context.scope);
      else ptc.delete(context.scope);
    }
    return result;
  });

  return ({name, description}) => {
    names.set(name, description);
    return {when: ({agent}) => !active(agent) || (loaded.get(agent)?.has(name) ?? false)};
  };
}
