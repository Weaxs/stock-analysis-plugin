// Worked example: warmup winners get raw weights 2.5 and 1; next week's weighted P&L is 30 on 700.
const dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"];
export const rotationRequests: Record<string, Record<string, unknown>> = {
  calculate_rotation_metrics: {trade_counts: [{symbol: "XLK", buys: 4, wins: 3, streak: 2}]},
  backtest_rotation_allocation: {
    datasets: [["AAA", 110], ["BBB", 90]].map(([symbol, last]) => ({
      symbol, currency: "USD", price_basis: "qfq",
      bars: dates.map((date, i) => {
        const close = i === 4 || i === 9 ? Number(last) : 100;
        return {date, open: 100, high: Math.max(100, close), low: Math.min(100, close), close, volume: 100};
      }),
    })),
    start_date: "2026-01-12", end_date: "2026-01-16", warmup_weeks: 1, capital: 700, cost_per_side: 0,
  },
};
