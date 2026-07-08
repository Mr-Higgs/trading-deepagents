/**
 * Reduce the raw trading event stream (RUN_START/SIGNAL/SUBMIT/FILL/EXIT/REJECT/HALT/RUN_END) into
 * the shapes the dashboard renders: trade rows, an equity curve, a P/L summary, and open positions.
 *
 * Ported from trading-agent/trading_agent/journal.py:_build_trades (join by signal_id/trade_id).
 * Pure + deterministic. Run `npx tsx src/app/utils/reduceEvents.ts` for the self-check.
 */

const DAY_RESET_UTC_HOUR = 21; // config/risk.yaml trading_day_reset_utc: 21:00 (bars at/after 21:00 UTC belong to the next trading day)

export type Verdict = { decision: string; confidence: number; rationale: string };

/** Run header emitted once at run start (RUN_META event) — drives the execution badge. */
export type RunMeta = {
  mode: string; // "backtest" | "live"
  backend: string; // "simulated" | "oanda"
  account?: string | null; // masked 101-… when backend === "oanda"
  instrument?: string;
  strategy_version?: string;
  start?: string;
  end?: string;
  poll?: number;
  strategy_mode?: string;
};

export type TradingEvent = {
  ts: string;
  seq: number;
  run_id: string;
  strategy_version: string;
  type: string;
  reason?: string;
  signal_id?: string;
  instrument?: string;
  direction?: "LONG" | "SHORT";
  entry_ref?: number;
  stop?: number;
  new_stop?: number; // STOP_MODIFIED
  tp?: number;
  rr?: number;
  setup_id?: string;
  features?: Record<string, unknown> & { llm_verdict?: Verdict };
  trade_id?: string;
  units?: number;
  risk_amount?: number;
  price?: number;
  pnl?: number;
  exit_reason?: string;
  starting_equity?: number;
  final_equity?: number;
};

export type TradeStatus = "signal" | "rejected" | "submitted" | "open" | "closed";

export type Trade = {
  signal_id: string;
  signal_ts: string;
  instrument?: string;
  direction?: string;
  entry_ref?: number;
  stop?: number;
  tp?: number;
  rr?: number;
  verdict?: Verdict;
  status: TradeStatus;
  reject_reason?: string;
  trade_id?: string;
  units?: number;
  risk_amount?: number;
  submit_ts?: string;
  fill_price?: number;
  fill_ts?: string;
  exit_price?: number;
  exit_reason?: string;
  pnl?: number;
  exit_ts?: string;
  breakeven?: boolean; // stop moved to breakeven
  stopMoves?: number; // count of STOP_MODIFIED events
  scaled?: boolean; // scale-out partials banked
  bankedPnl?: number; // accumulated partial P/L while runner is open
  remainingUnits?: number; // signed units still open after a partial
};

export type EquityPoint = { ts: string; equity: number };

export type PnlSummary = {
  startingEquity: number;
  equity: number;
  realizedPnl: number;
  closed: number;
  wins: number;
  losses: number;
  winRate: number | null;
  openCount: number;
  dayPnl: number;
  halted: boolean;
  haltReason?: string;
  signals: number;
  rejected: number;
};

export type Reduced = {
  trades: Trade[];
  equityCurve: EquityPoint[];
  pnl: PnlSummary;
  openPositions: Trade[];
};

/** Trading-day bucket (YYYY-MM-DD) honoring the 21:00 UTC reset, matching risk.trading_day. */
function tradingDay(ts: string): string {
  const d = new Date(ts);
  if (d.getUTCHours() >= DAY_RESET_UTC_HOUR) d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

export function reduceEvents(events: TradingEvent[]): Reduced {
  const bySignal = new Map<string, Trade>();
  const byTrade = new Map<string, Trade>();
  const order: Trade[] = []; // preserve first-seen (signal) order

  let startingEquity = 10_000;
  let realizedPnl = 0;
  let signals = 0;
  let rejected = 0;
  let lastDay = ""; // trading-day of the most recent event
  const equityCurve: EquityPoint[] = [];
  const exitDayPnl = new Map<string, number>(); // trading-day -> realized pnl
  const haltDays = new Map<string, string>(); // trading-day -> halt reason (backend resets the halt each new day)

  for (const e of events) {
    if (e.ts) lastDay = tradingDay(e.ts);
    switch (e.type) {
      case "RUN_START":
        startingEquity = e.starting_equity ?? startingEquity;
        equityCurve.push({ ts: e.ts, equity: startingEquity });
        break;
      case "SIGNAL": {
        if (!e.signal_id) break;
        signals += 1;
        const t: Trade = {
          signal_id: e.signal_id,
          signal_ts: e.ts,
          instrument: e.instrument,
          direction: e.direction,
          entry_ref: e.entry_ref,
          stop: e.stop,
          tp: e.tp,
          rr: e.rr,
          verdict: e.features?.llm_verdict,
          status: "signal",
        };
        bySignal.set(e.signal_id, t);
        order.push(t);
        break;
      }
      case "REJECT": {
        rejected += 1;
        const t = e.signal_id ? bySignal.get(e.signal_id) : undefined;
        if (t) {
          t.status = "rejected";
          t.reject_reason = e.reason;
        }
        break;
      }
      case "SUBMIT": {
        const t = e.signal_id ? bySignal.get(e.signal_id) : undefined;
        if (t && e.trade_id) {
          t.status = "submitted";
          t.trade_id = e.trade_id;
          t.units = e.units;
          t.risk_amount = e.risk_amount;
          t.submit_ts = e.ts;
          if (e.stop !== undefined) t.stop = e.stop;
          if (e.tp !== undefined) t.tp = e.tp;
          if (e.rr !== undefined) t.rr = e.rr;
          byTrade.set(e.trade_id, t);
        }
        break;
      }
      case "FILL": {
        const t = e.trade_id ? byTrade.get(e.trade_id) : undefined;
        if (t) {
          t.status = "open";
          t.fill_price = e.price;
          t.fill_ts = e.ts;
        }
        break;
      }
      case "PARTIAL_EXIT": {
        const t = e.trade_id ? byTrade.get(e.trade_id) : undefined;
        const pnl = e.pnl ?? 0;
        realizedPnl += pnl;
        if (t) {
          t.scaled = true;
          t.bankedPnl = (t.bankedPnl ?? 0) + pnl;
          t.pnl = t.bankedPnl;
          t.remainingUnits = e.remaining_units;
          if (e.remaining_units === 0) {
            t.status = "closed";
            t.exit_price = e.price;
            t.exit_reason = e.exit_reason ?? e.reason;
            t.exit_ts = e.ts;
          } else {
            t.status = "open";
          }
        }
        equityCurve.push({ ts: e.ts, equity: startingEquity + realizedPnl });
        exitDayPnl.set(tradingDay(e.ts), (exitDayPnl.get(tradingDay(e.ts)) ?? 0) + pnl);
        break;
      }
      case "EXIT": {
        const t = e.trade_id ? byTrade.get(e.trade_id) : undefined;
        const pnl = e.pnl ?? 0;
        realizedPnl += pnl;
        if (t) {
          t.status = "closed";
          t.exit_price = e.price;
          t.exit_reason = e.exit_reason ?? e.reason;
          t.pnl = t.scaled ? (t.bankedPnl ?? 0) + pnl : pnl;
          t.exit_ts = e.ts;
        }
        equityCurve.push({ ts: e.ts, equity: startingEquity + realizedPnl });
        exitDayPnl.set(tradingDay(e.ts), (exitDayPnl.get(tradingDay(e.ts)) ?? 0) + pnl);
        break;
      }
      case "STOP_MODIFIED": {
        const t = e.trade_id ? byTrade.get(e.trade_id) : undefined;
        if (t) {
          if (e.new_stop !== undefined) t.stop = e.new_stop;
          t.stopMoves = (t.stopMoves ?? 0) + 1;
          if (e.reason === "breakeven") t.breakeven = true;
        }
        break;
      }
      case "HALT":
        haltDays.set(tradingDay(e.ts), e.reason ?? "halted");
        break;
      default:
        break; // SKIP, RUN_END — nothing to reduce
    }
  }

  const closedTrades = order.filter((t) => t.status === "closed");
  const wins = closedTrades.filter((t) => (t.pnl ?? 0) > 0).length;
  const losses = closedTrades.filter((t) => (t.pnl ?? 0) < 0).length;
  const openPositions = order.filter((t) => t.status === "open");

  const pnl: PnlSummary = {
    startingEquity,
    equity: startingEquity + realizedPnl,
    realizedPnl,
    closed: closedTrades.length,
    wins,
    losses,
    winRate: wins + losses > 0 ? wins / (wins + losses) : null,
    openCount: openPositions.length,
    dayPnl: exitDayPnl.get(lastDay) ?? 0, // P/L on the latest trading day
    halted: haltDays.has(lastDay), // is the CURRENT trading day halted (not "ever halted")
    haltReason: haltDays.get(lastDay),
    signals,
    rejected,
  };

  return { trades: order, equityCurve, pnl, openPositions };
}

// --- self-check (runs only under `npx tsx`, never in the browser bundle) --------------------------
const _proc = (globalThis as { process?: { argv?: string[] } }).process;
if (_proc?.argv?.[1]?.includes("reduceEvents")) {
  // Real events from data/logs/events-live-20260706T032436Z.jsonl (one full trade sequence).
  const fixture: TradingEvent[] = [
    { ts: "2026-07-01T13:15:00+00:00", seq: 0, run_id: "t", strategy_version: "v1", type: "RUN_START", starting_equity: 10000 },
    { ts: "2026-07-02T08:00:00+00:00", seq: 76, run_id: "t", strategy_version: "v1", type: "SIGNAL", signal_id: "S1", instrument: "AUD_USD", direction: "SHORT", entry_ref: 0.68904, stop: 0.69031, tp: 0.6865, rr: 2.0 },
    { ts: "2026-07-02T08:00:00+00:00", seq: 77, run_id: "t", strategy_version: "v1", type: "SUBMIT", trade_id: "S1-t1", signal_id: "S1", units: -78740, stop: 0.69031, tp: 0.6865, risk_amount: 100 },
    { ts: "2026-07-02T08:15:00+00:00", seq: 78, run_id: "t", strategy_version: "v1", type: "FILL", trade_id: "S1-t1", price: 0.68887, units: -78740 },
    { ts: "2026-07-02T08:30:00+00:00", seq: 80, run_id: "t", strategy_version: "v1", type: "EXIT", reason: "STOP", exit_reason: "STOP", trade_id: "S1-t1", price: 0.69031, units: -78740, pnl: -113.3856 },
  ];
  const r = reduceEvents(fixture);
  const ok = (cond: boolean, msg: string) => {
    if (!cond) throw new Error("self-check FAILED: " + msg);
  };
  ok(r.trades.length === 1, "one trade");
  ok(r.trades[0].status === "closed", "trade closed");
  ok(r.trades[0].exit_reason === "STOP", "exit reason STOP");
  ok((r.trades[0].pnl ?? 0) < 0, "losing trade");
  ok(Math.abs(r.pnl.equity - (10000 - 113.3856)) < 1e-6, "equity = start + pnl");
  ok(r.pnl.closed === 1 && r.pnl.wins === 0 && r.pnl.losses === 1, "win/loss counts");
  ok(r.pnl.winRate === 0, "win rate 0");
  ok(r.equityCurve.length === 2, "equity curve start + one exit");
  ok(r.openPositions.length === 0, "no open positions");

  const partialFixture: TradingEvent[] = [
    { ts: "2026-07-01T13:15:00+00:00", seq: 0, run_id: "t", strategy_version: "v1", type: "RUN_START", starting_equity: 10000 },
    { ts: "2026-07-02T08:00:00+00:00", seq: 1, run_id: "t", strategy_version: "v1", type: "SIGNAL", signal_id: "S2", instrument: "AUD_USD", direction: "LONG", entry_ref: 0.67, stop: 0.668, tp: 0.68, rr: 2.5 },
    { ts: "2026-07-02T08:00:00+00:00", seq: 2, run_id: "t", strategy_version: "v1", type: "SUBMIT", trade_id: "S2-t1", signal_id: "S2", units: 50000, stop: 0.668, tp: 0.68, rr: 2.5, risk_amount: 100 },
    { ts: "2026-07-02T08:15:00+00:00", seq: 3, run_id: "t", strategy_version: "v1", type: "FILL", trade_id: "S2-t1", price: 0.6701, units: 50000 },
    { ts: "2026-07-02T08:30:00+00:00", seq: 4, run_id: "t", strategy_version: "v1", type: "PARTIAL_EXIT", trade_id: "S2-t1", price: 0.672, units: 25000, pnl: 47.5, remaining_units: 25000, exit_reason: "SCALE_OUT" },
    { ts: "2026-07-02T09:00:00+00:00", seq: 5, run_id: "t", strategy_version: "v1", type: "EXIT", reason: "TP", exit_reason: "TP", trade_id: "S2-t1", price: 0.68, units: 25000, pnl: 247.5 },
  ];
  const pr = reduceEvents(partialFixture);
  ok(pr.trades.length === 1 && pr.trades[0].scaled === true, "scaled trade");
  ok(pr.trades[0].status === "closed", "closed after final exit");
  ok(Math.abs((pr.trades[0].pnl ?? 0) - 295) < 1e-6, "banked + final pnl");
  ok(pr.pnl.realizedPnl === 295, "realized pnl sums partials");

  // eslint-disable-next-line no-console
  console.log("reduceEvents self-check OK", JSON.stringify(r.pnl));
}
