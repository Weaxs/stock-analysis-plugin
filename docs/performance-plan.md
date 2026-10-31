# Workflow performance plan

Baseline: origin/main at 41bf904 (includes PR #69). Keep the four public host contracts unchanged.

1. Share a bounded, per-invocation CLI task pool across a watchlist. Deduplicate exact command/argument/timeout tuples only within that invocation. Fetch market regime once per market. Do not persist quotes or merge distinct data shapes (capital-flow summary versus detail).
2. Feed already fetched daily bars and quote into risk/anomaly analysis; preserve missing-data semantics without silently retrying failed supplied data. Run each stock's anomaly continuation as soon as that stock completes, preserving final caller order.
3. Parallelize independent risk, position and screener I/O while respecting the shared task pool. Retain per-command subprocess budgets and stable result/flag order.
4. Reuse secure exclusive atomic file creation across JSON cache, routing cache, signal store and SEC ZIP download; preserve original store on failure. Do not swallow persistence errors or add indefinite lock waits. Coordinate dataset cache misses and SEC archive downloads across processes with the shared bounded file lock. Keep cache lock timeout as best-effort fallback, and fail durable writes cleanly on timeout.
5. Remove repeated pandas row/column indexing from the sequential backtest loop. Compare complete results across execution modes, price limits, gates and costs before measuring speed.
6. Retain exact portfolio arithmetic and current SEC scan architecture unless a reproducible scale benchmark justifies another algorithm. No new runtime dependencies, service, database, public tool params or JSON keys.

Validation: agreed public workflow/data-write/simulate seams; deterministic counted provider/CLI substitutes; full offline test suite and four registration suites; reproducible before/after call counts and compute timings. Review standards and spec separately, plus correctness/load/lean review, fixing all blocking findings before creating a PR. Reference issues only, never auto-close.
