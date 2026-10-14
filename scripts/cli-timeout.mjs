import budgets from "../tools/cli_budgets.json" with { type: "json" };
import { basename } from "node:path";

// Hosts leave the CLI time to return its own structured failure.
export function commandTimeout(script, args) {
  const key = `${basename(script)}:${args[0] ?? ""}`;
  let budget = budgets[key] ?? budgets.default;
  const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] ?? fallback : fallback;
  const positiveInt = (value, fallback) => /^\d+$/.test(String(value)) && Number(value) > 0 ? Number(value) : fallback;
  if (key === "screener.py:screen") {
    budget += Math.ceil(positiveInt(option("--top", 20), 20) / budgets.l2_workers) * budgets.l2_batch;
  } else if (key === "watchlist.py:analyze" || key === "watchlist_context.py:build") {
    const count = String(args[1] ?? "").split(",").filter(s => s.trim()).length;
    const workers = positiveInt(option("--workers", 3), 3);
    budget += Math.ceil(count / workers) * budgets.watchlist_item;
    if (key === "watchlist_context.py:build") {
      budget += Math.ceil(count / Math.min(workers * 2, 6)) * budgets["anomaly_detect.py:detect"];
      if (args.includes("--include-market-review")) budget += 3 * budgets["market_review.py:review"] + 30;
    }
  } else if (key === "market_review.py:review" && option("--market", "A") === "all") {
    budget = 3 * budget + 30;
  } else if (key === "technical.py:analyze" && args.includes("--periods")) {
    const periods = String(option("--periods", "daily")).split(",").map(p => p.trim()).filter(Boolean);
    budget *= new Set([option("--period", "daily"), ...periods]).size;
  }
  return budget * 1000 + 30_000;
}
