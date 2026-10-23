// Folds the research-loop custom events (emitted by trade_loop's mode:"research") into a live,
// ordered iteration list. These events ride the SAME `custom` stream as the trading events — useChat
// appends every non-RUN_META/non-SKIP event to `events`, so this reducer runs over that shared buffer.

export type IterMetrics = {
  trades: number;
  expectancy: number;
  win_rate: number;
  profit_factor: number | "inf";
  avg_win_R: number;
  avg_loss_R: number;
  total_pnl: number;
  max_drawdown: number;
};

export type ResearchMeta = {
  run_id: string;
  iterations: number;
  model: string;
  commit: boolean;
  is_range: [string, string];
  baseline_is: IterMetrics;
  // Legacy OOS-gated runs only; proposal-only runs (selection moved to optimize/) omit these.
  oos_range?: [string, string];
  baseline_oos?: IterMetrics;
  best_oos?: number;
};

export type Iteration = {
  iteration: number;
  total?: number;
  status: "running" | "proposed" | "kept" | "reverted" | "skipped";
  phase?: string; // "reasoning" | "evaluating" — sub-step of a running iteration (from PROGRESS events)
  hypothesis?: string;
  rationale?: string;
  overrides?: Record<string, unknown>;
  is_metrics?: IterMetrics;
  oos_metrics?: IterMetrics;
  keep?: boolean;
  improved?: boolean;
  gate?: string;
  best_oos?: number;
  version?: string | null;
  note?: string;
};

export type ResearchState = {
  meta: ResearchMeta | null;
  iterations: Iteration[];
  done: { proposed?: number; accepted?: number; iterations: number; best_oos?: number } | null;
  phase: string | null; // top-level phase before the first iteration, e.g. "baseline" (warming up)
};

export type ResearchEvent = { type?: string } & Record<string, unknown>;

export function reduceResearch(events: ReadonlyArray<ResearchEvent>): ResearchState {
  let meta: ResearchMeta | null = null;
  let done: ResearchState["done"] = null;
  let phase: string | null = null;
  const byIter = new Map<number, Iteration>();

  for (const e of events) {
    switch (e.type) {
      case "RESEARCH_META":
        meta = e as unknown as ResearchMeta;
        break;
      case "RESEARCH_END":
        done = {
          iterations: Number(e.iterations),
          ...(e.proposed != null ? { proposed: Number(e.proposed) } : {}),
          ...(e.accepted != null ? { accepted: Number(e.accepted) } : {}),
          ...(e.best_oos != null ? { best_oos: Number(e.best_oos) } : {}),
        };
        break;
      case "ITER_START": {
        const n = Number(e.iteration);
        byIter.set(n, { ...(byIter.get(n) ?? {}), iteration: n, total: Number(e.total), status: "running", ...(e.best_oos != null ? { best_oos: Number(e.best_oos) } : {}) });
        break;
      }
      case "ITER_RESULT": {
        const n = Number(e.iteration);
        byIter.set(n, { ...(byIter.get(n) ?? { iteration: n, status: "running" }), ...(e as unknown as Iteration) });
        break;
      }
      case "PROGRESS": {
        // sub-step signal that also keeps the stream warm. Iteration-scoped -> set that iteration's phase;
        // otherwise it's the pre-iteration baseline ("warming up").
        if (e.iteration != null) {
          const n = Number(e.iteration);
          byIter.set(n, { ...(byIter.get(n) ?? { iteration: n, status: "running" }), phase: String(e.phase ?? "") });
        } else {
          phase = String(e.phase ?? "");
        }
        break;
      }
      default: // BACKTEST heartbeats and anything else: ignored (keep-warm only)
        break;
    }
  }

  const iterations = [...byIter.values()].sort((a, b) => a.iteration - b.iteration);
  return { meta, iterations, done, phase };
}
