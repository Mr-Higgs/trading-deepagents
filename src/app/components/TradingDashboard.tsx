"use client";

import { useEffect, useRef } from "react";
import { Play, Radio, Banknote, Square, TrendingUp, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { getGateMode } from "@/lib/runPrefs";
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

function PnlTiles({ pnl }: { pnl: PnlSummary }) {
  const ret = pnl.equity - pnl.startingEquity;
  const retPct = pnl.startingEquity ? (ret / pnl.startingEquity) * 100 : 0;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Tile label="Equity" value={usd(pnl.equity)} sub={`start ${usd(pnl.startingEquity)}`} />
      <Tile label="Realized P/L" value={signedUsd(pnl.realizedPnl)} sub={`${retPct >= 0 ? "+" : ""}${retPct.toFixed(2)}%`} valueClass={pnlColor(pnl.realizedPnl)} />
      <Tile label="Day P/L" value={signedUsd(pnl.dayPnl)} valueClass={pnlColor(pnl.dayPnl)} />
      <Tile label="Win rate" value={pct(pnl.winRate)} sub={`${pnl.wins}W / ${pnl.losses}L`} />
      <Tile label="Open" value={String(pnl.openCount)} sub={`${pnl.signals} signals`} />
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
    <div className="overflow-auto">
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
                  <span className={`text-[10px] font-medium uppercase ${t.verdict.decision === "approve" ? "text-success" : "text-destructive"}`} title={t.verdict.rationale}>
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
  const { startRun, stopStream, isLoading, events, runMeta, trades, equityCurve, pnl, openPositions } = useChatContext();
  const version = runMeta?.strategy_version ?? events[0]?.strategy_version;

  const goLivePaper = () => {
    if (window.confirm("Place REAL orders on the OANDA practice (101-) account?\n\nLive signals during the London session will submit demo orders that appear in your OANDA history.")) {
      startRun({ mode: "live", execution: "oanda", strategy: getGateMode() });
    }
  };

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => startRun({ mode: "backtest" })} disabled={isLoading} className="bg-primary text-primary-foreground">
          <Play className="mr-2 h-4 w-4" /> Backtest
        </Button>
        <Button size="sm" variant="outline" onClick={() => startRun({ mode: "live", execution: "simulated", strategy: getGateMode() })} disabled={isLoading}>
          <Radio className="mr-2 h-4 w-4" /> Go Live (sim)
        </Button>
        <Button size="sm" variant="outline" onClick={goLivePaper} disabled={isLoading} className="border-success/60 text-success hover:bg-success/10">
          <Banknote className="mr-2 h-4 w-4" /> Go Live — Paper
        </Button>
        {isLoading && (
          <Button size="sm" variant="outline" onClick={stopStream}>
            <Square className="mr-2 h-4 w-4" /> Stop
          </Button>
        )}
        <div className="ml-2 flex items-center gap-2 text-sm text-muted-foreground">
          <span className={`inline-block h-2 w-2 rounded-full ${isLoading ? "animate-pulse bg-success" : "bg-muted-foreground/40"}`} />
          {isLoading ? "streaming" : "idle"} · {events.length} events
          {version && <span className="font-mono text-xs">· {version}</span>}
        </div>
        <div className="ml-auto">
          <ExecBadge meta={runMeta} />
        </div>
      </div>

      {pnl.halted && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> Trading halted — {pnl.haltReason}
        </div>
      )}

      {events.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
          <TrendingUp className="h-8 w-8" />
          <p className="text-sm">No run yet. Click <span className="font-medium text-foreground">Backtest</span> to stream a run, or <span className="font-medium text-foreground">Go Live</span> to poll OANDA.</p>
        </div>
      ) : (
        <>
          <PnlTiles pnl={pnl} />
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Equity" className="h-56">
              <EquityCurve curve={equityCurve} />
            </Panel>
            <Panel title="Live feed" className="h-56">
              <EventFeed events={events} />
            </Panel>
          </div>
          <Panel title={`Trades — ${trades.length}${openPositions.length ? ` (${openPositions.length} open)` : ""}`} className="max-h-96">
            <TradesTable trades={trades} />
          </Panel>
        </>
      )}
    </div>
  );
}
