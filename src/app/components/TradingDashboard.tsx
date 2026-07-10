"use client";

import { useEffect, useMemo, useRef } from "react";
import { Play, Radio, Banknote, Square, TrendingUp, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { useAccountContext } from "@/providers/AccountProvider";
import { openToTrades } from "@/app/utils/openTrades";
import { getGateMode, getAgentOverrides } from "@/lib/runPrefs";
import { ResearchControls, ResearchStream } from "@/app/components/ResearchViews";
import type { AccountSummary } from "@/app/hooks/useAccount";
import type { EquityPoint, PnlSummary, RunMeta, Trade, TradingEvent } from "@/app/utils/reduceEvents";

// --- formatting -----------------------------------------------------------------------------------
const usd = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signedUsd = (n: number) => `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const price = (n?: number) => (n === undefined ? "—" : n.toFixed(5));
const pct = (n: number | null) => (n === null ? "—" : `${(n * 100).toFixed(0)}%`);
const pnlColor = (n?: number) => (n === undefined || n === 0 ? "text-muted-foreground" : n > 0 ? "text-success" : "text-destructive");
const time = (ts?: string) => (ts ? new Date(ts).toISOString().slice(5, 16).replace("T", " ") : "—");

// --- P/L tiles ------------------------------------------------------------------------------------
function Tile({ label, value, sub, valueClass }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold tabular-nums ${valueClass ?? "text-foreground"}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

// When `summary` is passed (live/idle with a real OANDA snapshot) the money tiles mirror the account —
// NAV, live unrealized P/L, real open count. Run-scoped stats (win rate, rejected, day P/L) stay from the
// streamed run. Without it (backtest) the tiles show the run's simulated ledger.
function PnlTiles({ pnl, summary }: { pnl: PnlSummary; summary?: AccountSummary }) {
  const ret = pnl.equity - pnl.startingEquity;
  const retPct = pnl.startingEquity ? (ret / pnl.startingEquity) * 100 : 0;
  const nav = summary?.NAV != null ? parseFloat(summary.NAV) : null;
  const bal = summary?.balance != null ? parseFloat(summary.balance) : null;
  const uPL = summary?.unrealizedPL != null ? parseFloat(summary.unrealizedPL) : null;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {nav != null ? (
        <Tile label="Equity (NAV)" value={usd(nav)} sub={bal != null ? `bal ${usd(bal)}` : undefined} />
      ) : (
        <Tile label="Equity" value={usd(pnl.equity)} sub={`start ${usd(pnl.startingEquity)}`} />
      )}
      {uPL != null ? (
        <Tile label="Unrealized P/L" value={signedUsd(uPL)} valueClass={pnlColor(uPL)} />
      ) : (
        <Tile label="Realized P/L" value={signedUsd(pnl.realizedPnl)} sub={`${retPct >= 0 ? "+" : ""}${retPct.toFixed(2)}%`} valueClass={pnlColor(pnl.realizedPnl)} />
      )}
      <Tile label="Day P/L" value={signedUsd(pnl.dayPnl)} valueClass={pnlColor(pnl.dayPnl)} />
      <Tile label="Win rate" value={pct(pnl.winRate)} sub={`${pnl.wins}W / ${pnl.losses}L`} />
      <Tile label="Open" value={String(summary?.openTradeCount ?? pnl.openCount)} sub={`${pnl.signals} signals`} />
      <Tile label="Rejected" value={String(pnl.rejected)} sub={pnl.halted ? "HALTED" : "active"} valueClass={pnl.halted ? "text-destructive" : undefined} />
    </div>
  );
}

// --- equity curve (inline SVG, no chart dep in Phase A) -------------------------------------------
function EquityCurve({ curve }: { curve: EquityPoint[] }) {
  if (curve.length < 2) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Equity curve appears once trades close.</div>;
  }
  const eqs = curve.map((p) => p.equity);
  const start = eqs[0];
  const min = Math.min(...eqs, start);
  const max = Math.max(...eqs, start);
  const span = max - min || 1;
  const H = 100;
  const y = (e: number) => H - ((e - min) / span) * H;
  const x = (i: number) => (i / (curve.length - 1)) * 100;
  const line = curve.map((p, i) => `${x(i).toFixed(2)},${y(p.equity).toFixed(2)}`).join(" ");
  const area = `0,${H} ${line} 100,${H}`;
  const up = eqs[eqs.length - 1] >= start;
  const stroke = up ? "hsl(var(--chart-2))" : "hsl(var(--destructive))";
  return (
    <div className="flex h-full flex-col">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
        <polygon points={area} fill={stroke} opacity={0.08} />
        <line x1={0} x2={100} y1={y(start)} y2={y(start)} stroke="hsl(var(--border))" strokeWidth={1} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
        <polyline points={line} fill="none" stroke={stroke} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
      <div className="flex justify-between px-1 pt-1 font-mono text-[10px] text-muted-foreground">
        <span>min {usd(min)}</span>
        <span>last {usd(eqs[eqs.length - 1])}</span>
        <span>max {usd(max)}</span>
      </div>
    </div>
  );
}

// --- trades table ---------------------------------------------------------------------------------
function statusBadge(t: Trade) {
  const map: Record<string, string> = {
    signal: "bg-muted text-muted-foreground",
    rejected: "bg-destructive/15 text-destructive",
    submitted: "bg-chart-4/15 text-foreground",
    open: "bg-chart-1/15 text-foreground",
    closed: "bg-muted text-muted-foreground",
  };
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${map[t.status] ?? "bg-muted"}`}>{t.status}</span>;
}

function TradesTable({ trades }: { trades: Trade[] }) {
  if (!trades.length) return <div className="p-6 text-center text-sm text-muted-foreground">No trades yet.</div>;
  const rows = [...trades].reverse();
  return (
    // explicit max-height so the table always scrolls internally, independent of the parent flex chain
    <div className="max-h-[55vh] overflow-auto">
      <table className="w-full min-w-[820px] text-sm">
        <thead className="sticky top-0 bg-card text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
            <th>Time</th><th>Dir</th><th>Entry</th><th>Stop</th><th>TP</th><th>R:R</th><th>Units</th><th>Fill</th><th>Exit</th><th className="text-right">P/L</th><th>Status</th><th>Gate</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums [&>tr]:border-t [&>tr]:border-border">
          {rows.map((t) => (
            <tr key={t.signal_id} className="[&>td]:px-3 [&>td]:py-2">
              <td className="whitespace-nowrap text-muted-foreground">{time(t.submit_ts ?? t.signal_ts)}</td>
              <td className={t.direction === "LONG" ? "text-success" : "text-destructive"}>{t.direction}</td>
              <td>{price(t.entry_ref)}</td>
              <td className="text-muted-foreground">{price(t.stop)}</td>
              <td className="text-muted-foreground">{price(t.tp)}</td>
              <td className="text-muted-foreground">{t.rr !== undefined ? t.rr.toFixed(2) : "—"}</td>
              <td>
                {t.units?.toLocaleString("en-US") ?? "—"}
                {t.scaled ? <span className="ml-1 text-[10px] text-chart-1">scaled</span> : null}
                {t.remainingUnits ? <span className="ml-1 text-[10px] text-muted-foreground">rem {t.remainingUnits.toLocaleString("en-US")}</span> : null}
              </td>
              <td>{price(t.fill_price)}</td>
              <td>{price(t.exit_price)}{t.exit_reason ? <span className="ml-1 text-[10px] text-muted-foreground">{t.exit_reason}</span> : null}</td>
              <td className={`text-right ${pnlColor(t.pnl)}`}>{t.pnl === undefined ? "—" : signedUsd(t.pnl)}</td>
              <td>{statusBadge(t)}</td>
              <td>
                {t.verdict ? (
                  <span className={`text-[10px] font-medium uppercase ${t.verdict.decision === "approve" ? "text-success" : t.verdict.decision === "modify" ? "text-chart-1" : "text-destructive"}`} title={t.verdict.rationale}>
                    {t.verdict.decision} {(t.verdict.confidence * 100).toFixed(0)}%
                  </span>
                ) : (
                  <span className="text-[10px] text-muted-foreground">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- live event feed ------------------------------------------------------------------------------
const EVENT_COLOR: Record<string, string> = {
  RUN_START: "text-muted-foreground",
  SIGNAL: "text-chart-1",
  SUBMIT: "text-chart-4",
  FILL: "text-chart-2",
  EXIT: "text-foreground",
  REJECT: "text-warning",
  HALT: "text-destructive",
  RUN_END: "text-muted-foreground",
};

function eventSummary(e: TradingEvent): string {
  switch (e.type) {
    case "RUN_START": return `${e.instrument} — starting equity ${usd(e.starting_equity ?? 0)}`;
    case "SIGNAL": return `${e.direction} ${e.instrument} @ ${price(e.entry_ref)}  sl ${price(e.stop)} tp ${price(e.tp)}${e.features?.llm_verdict ? `  · gate ${e.features.llm_verdict.decision}` : ""}`;
    case "SUBMIT": return `${e.trade_id}  ${e.units?.toLocaleString("en-US")}u  risk ${usd(e.risk_amount ?? 0)}`;
    case "FILL": return `${e.trade_id} filled @ ${price(e.price)}`;
    case "EXIT": return `${e.trade_id} ${e.exit_reason} @ ${price(e.price)}  ${signedUsd(e.pnl ?? 0)}`;
    case "REJECT": return `${e.signal_id} — ${e.reason}`;
    case "HALT": return e.reason ?? "halted";
    case "RUN_END": return `final equity ${usd(e.final_equity ?? 0)}`;
    default: return e.reason ?? "";
  }
}

function EventFeed({ events }: { events: TradingEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);
  if (!events.length) return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Waiting for events…</div>;
  return (
    <div ref={ref} className="h-full overflow-auto px-1 font-mono text-xs">
      {events.map((e, i) => (
        <div key={`${e.run_id}-${e.seq}-${i}`} className="flex gap-2 border-b border-border/50 py-1">
          <span className="w-24 shrink-0 text-muted-foreground">{time(e.ts)}</span>
          <span className={`w-16 shrink-0 font-semibold ${EVENT_COLOR[e.type] ?? "text-muted-foreground"}`}>{e.type}</span>
          <span className="truncate text-foreground/90">{eventSummary(e)}</span>
        </div>
      ))}
    </div>
  );
}

// --- panel + toolbar ------------------------------------------------------------------------------
function Panel({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`flex flex-col overflow-hidden rounded-lg border border-border bg-card ${className ?? ""}`}>
      <div className="border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</div>
      <div className="min-h-0 flex-1 p-3">{children}</div>
    </div>
  );
}

function ExecBadge({ meta }: { meta: RunMeta | null }) {
  if (!meta) return null;
  const paper = meta.backend === "oanda";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${
        paper ? "border-success/50 text-success" : "border-warning/50 text-warning"
      }`}
      title={paper ? "Live signals place REAL orders on the OANDA practice account" : "Fills are locally simulated — nothing is sent to OANDA"}
    >
      {paper ? `OANDA Paper · ${meta.account ?? "101-…"}` : "Simulated"}
    </span>
  );
}

export function TradingDashboard() {
  const { startRun, stopStream, isLoading, events, runMeta, lastCheckAt, trades, equityCurve, pnl, openPositions, research, error } = useChatContext();
  const { data: account } = useAccountContext();
  const errMsg = error ? (error instanceof Error ? error.message : String(error)) : null;
  const version = runMeta?.strategy_version ?? events[0]?.strategy_version;
  // A research run rides this same stream but carries no trades — show the iteration loop instead.
  const isResearchRun = research.meta !== null || research.iterations.length > 0;

  // Mirror the real OANDA account for a live run or when idle; a backtest keeps its own simulated ledger.
  const mirror = runMeta?.mode !== "backtest";
  const summary = mirror ? account?.summary : undefined;
  // Real open OANDA positions (e.g. tickets opened in a prior session) merged into the run's trades,
  // deduped by trade id — so they show as OPEN rows even when not in the current event stream.
  const realOpen = useMemo(() => {
    if (!mirror) return [];
    const streamIds = new Set(trades.map((t) => t.trade_id).filter((id): id is string => !!id));
    return openToTrades(account?.trades ?? [], undefined, streamIds);
  }, [mirror, account?.trades, trades]);
  const allTrades = useMemo(() => [...trades, ...realOpen], [trades, realOpen]);
  const openCount = openPositions.length + realOpen.length;
  const hasData = events.length > 0 || !!summary || realOpen.length > 0;

  const goLivePaper = () => {
    if (window.confirm("Place REAL orders on the OANDA practice (101-) account?\n\nLive signals during the London session will submit demo orders that appear in your OANDA history.")) {
      startRun({ mode: "live", execution: "oanda", strategy: getGateMode(), agents: getAgentOverrides(), poll: 15 });
    }
  };

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => startRun({ mode: "backtest", strategy: getGateMode(), agents: getAgentOverrides() })} disabled={isLoading} className="bg-primary text-primary-foreground">
          <Play className="mr-2 h-4 w-4" /> Backtest
        </Button>
        <Button size="sm" variant="outline" onClick={() => startRun({ mode: "live", execution: "simulated", strategy: getGateMode(), agents: getAgentOverrides(), poll: 15 })} disabled={isLoading}>
          <Radio className="mr-2 h-4 w-4" /> Go Live (sim)
        </Button>
        <Button size="sm" variant="outline" onClick={goLivePaper} disabled={isLoading} className="border-success/60 text-success hover:bg-success/10">
          <Banknote className="mr-2 h-4 w-4" /> Go Live — Paper
        </Button>
        {isLoading && !isResearchRun && (
          <Button size="sm" variant="outline" onClick={stopStream}>
            <Square className="mr-2 h-4 w-4" /> Stop
          </Button>
        )}
        <span className="mx-1 h-6 w-px shrink-0 bg-border" aria-hidden />
        <ResearchControls
          isLoading={isLoading}
          active={isResearchRun && isLoading}
          onStart={(iterations, model) => startRun({ mode: "research", iterations, model })}
          onStop={stopStream}
        />
        <div className="ml-2 flex items-center gap-2 text-sm text-muted-foreground">
          <span className={`inline-block h-2 w-2 rounded-full ${errMsg ? "bg-destructive" : isLoading ? "animate-pulse bg-success" : "bg-muted-foreground/40"}`} />
          {errMsg ? "disconnected" : isLoading ? "streaming" : "idle"} · {events.length} events
          {isLoading && lastCheckAt && <span className="font-mono text-xs">· last check {new Date(lastCheckAt).toISOString().slice(11, 19)}Z</span>}
          {version && <span className="font-mono text-xs">· {version}</span>}
        </div>
        <div className="ml-auto">
          <ExecBadge meta={runMeta} />
        </div>
      </div>

      {errMsg && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">Stream disconnected — {errMsg}. Re-run to reconnect.</span>
        </div>
      )}

      {pnl.halted && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> Trading halted — {pnl.haltReason}
        </div>
      )}

      {isResearchRun ? (
        <ResearchStream research={research} className="max-h-[60vh]" />
      ) : !hasData ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
          <TrendingUp className="h-8 w-8" />
          <p className="text-sm">No run yet. Click <span className="font-medium text-foreground">Backtest</span> to stream a run, <span className="font-medium text-foreground">Go Live</span> to poll OANDA, or <span className="font-medium text-foreground">Run loop</span> to sharpen the strategy.</p>
        </div>
      ) : (
        <>
          <PnlTiles pnl={pnl} summary={summary} />
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Equity" className="h-56">
              <EquityCurve curve={equityCurve} />
            </Panel>
            <Panel title="Live feed" className="h-56">
              <EventFeed events={events} />
            </Panel>
          </div>
          <Panel title={`Trades — ${allTrades.length}${openCount ? ` (${openCount} open)` : ""}`} className="max-h-96">
            <TradesTable trades={allTrades} />
          </Panel>
        </>
      )}
    </div>
  );
}
