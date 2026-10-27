import type { Context } from "@deepseek-ai/cordis";
import { defineTool } from "@deepseek-ai/dsh-tools";
import type { ToolSchema } from "@deepseek-ai/dsh-llm";
import type {} from "@deepseek-ai/dsh-system-prompt";

const SEARCH = "stock_tool_search";

export function registerToolDiscovery(ctx: Context, names: Set<string>) {
  if (typeof ctx.on !== "function" || typeof ctx.tools.get !== "function"
      || typeof ctx.tools.presentAs !== "function" || names.size === 0 || ctx.tools.get(SEARCH)) return;

  const states = new WeakMap<object, {catalog: ToolSchema[]; loaded: Set<string>}>();
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
      const state = exec.agent && states.get(exec.agent);
      if (!state) return JSON.stringify({error: "Tool discovery is unavailable for this agent; use its directly exposed tools"});
      const candidates = state.catalog
        .filter(tool => ctx.tools.get(tool.name, exec.agent))
        .map(tool => ({tool, text: `${tool.name} ${tool.description}`.toLowerCase()}));
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
      for (const {tool} of matches) state.loaded.add(tool.name);
      return JSON.stringify({loaded: matches.map(({tool}) => ({name: tool.name, description: tool.description}))});
    },
  }));

  ctx.on("system-prompt/assemble", async (_assembly, context, next) => {
    const result = await next();
    if (!context.scope) return result;
    // PTC carries a generated SDK; leave that host-owned presentation intact.
    if (!result.tools.some(tool => tool.name === SEARCH) || result.tools.some(tool => tool.name === "run_code")) {
      states.delete(context.scope);
      return result;
    }
    const state = states.get(context.scope) ?? {catalog: [], loaded: new Set<string>()};
    state.catalog = result.tools.filter(tool => names.has(tool.name));
    states.set(context.scope, state);
    return {...result, tools: result.tools.filter(tool => !names.has(tool.name) || state.loaded.has(tool.name))};
  });
}
