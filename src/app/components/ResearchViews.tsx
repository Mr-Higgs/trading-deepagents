"use client";

import { useEffect, useRef, useState } from "react";
import { Square, Repeat, CheckCircle2, XCircle, MinusCircle, Loader2, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { IterMetrics, Iteration, ResearchState } from "@/app/utils/reduceResearch";

const signedUsd = (n: number) => `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const expColor = (n: number) => (n > 0 ? "text-success" : n < 0 ? "text-destructive" : "text-muted-foreground");
const pf = (v: number | "inf") => (v === "inf" ? "∞" : v.toFixed(2));
const winPct = (n: number) => `${(n * 100).toFixed(0)}%`;

const RESEARCH_MODELS = [
  { value: "anthropic:claude-sonnet-5", label: "Sonnet 5 (fast)" },
  { value: "anthropic:claude-opus-4-8", label: "Opus 4.8 (deep)" },
];

// The launcher — iterations input + model select + Run/Stop. Shared by the Live toolbar and Backtest tab.
// `active` = a research run is the CURRENT run (show Stop); otherwise Run loop, disabled while any other
// run streams (isLoading) since the server runs one at a time.
export function ResearchControls({ isLoading, active, onStart, onStop }: { isLoading: boolean; active: boolean; onStart: (iterations: number, model: string) => void; onStop: () => void }) {
  const [iters, setIters] = useState(5);
  const [model, setModel] = useState(RESEARCH_MODELS[0].value);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <Repeat className="h-4 w-4" /> iterations
        <input
          type="number"
          min={1}
          max={25}
          value={iters}
          onChange={(e) => setIters(Math.max(1, Math.min(25, Number(e.target.value) || 1)))}
          disabled={isLoading}
          className="w-16 rounded-md border border-border bg-background px-2 py-1 font-mono text-sm"
        />
      </label>
      <select value={model} onChange={(e) => setModel(e.target.value)} disabled={isLoading} className="rounded-md border border-border bg-background px-2 py-1 text-sm">
        {RESEARCH_MODELS.map((m) => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </select>
      {active ? (
        <Button size="sm" variant="outline" onClick={onStop}>
          <Square className="mr-2 h-4 w-4" /> Stop
        </Button>
      ) : (
        <Button size="sm" onClick={() => onStart(iters, model)} disabled={isLoading} className="bg-primary text-primary-foreground">
          <FlaskConical className="mr-2 h-4 w-4" /> Run loop
        </Button>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: Iteration["status"] }) {
  const map = {
    running: { cls: "border-chart-1/50 text-chart-1", icon: <Loader2 className="h-3 w-3 animate-spin" />, label: "running" },
    kept: { cls: "border-success/50 text-success", icon: <CheckCircle2 className="h-3 w-3" />, label: "kept" },
    reverted: { cls: "border-muted-foreground/40 text-muted-foreground", icon: <XCircle className="h-3 w-3" />, label: "reverted" },
    skipped: { cls: "border-warning/50 text-warning", icon: <MinusCircle className="h-3 w-3" />, label: "skipped" },
  }[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${map.cls}`}>
      {map.icon}
      {map.label}
    </span>
  );
}

function MetricPair({ label, is, oos }: { label: string; is: number; oos: number }) {
  return (
    <div className="flex items-baseline gap-2 font-mono text-sm tabular-nums">
      <span className="w-16 text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-muted-foreground">IS</span>
      <span className={expColor(is)}>{signedUsd(is)}</span>
      <span className="ml-2 text-muted-foreground">OOS</span>
      <span className={`font-semibold ${expColor(oos)}`}>{signedUsd(oos)}</span>
    </div>
  );
}

function MetricsLine({ m }: { m: IterMetrics }) {
  return (
    <span className="font-mono text-xs text-muted-foreground">
      {m.trades} trades · win {winPct(m.win_rate)} · PF {pf(m.profit_factor)} · avg {m.avg_win_R.toFixed(2)}R / {m.avg_loss_R.toFixed(2)}R
    </span>
  );
}

function IterationCard({ it }: { it: Iteration }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">Iteration {it.iteration}{it.total ? `/${it.total}` : ""}</span>
          <StatusBadge status={it.status} />
          {it.version && <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{it.version}</span>}
        </div>
        {typeof it.best_oos === "number" && (
          <span className="font-mono text-xs text-muted-foreground">best OOS <span className={expColor(it.best_oos)}>{signedUsd(it.best_oos)}</span></span>
        )}
      </div>
      {it.status === "running" && (
        <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          {it.phase === "evaluating" ? "Running the deterministic in-sample & out-of-sample gate…" : "Agent is proposing a change and probing it with backtests…"}
        </p>
      )}
      {it.hypothesis && <p className="mt-2 text-sm font-medium text-foreground">{it.hypothesis}</p>}
      {it.overrides && Object.keys(it.overrides).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {Object.entries(it.overrides).map(([k, v]) => (
            <span key={k} className="rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-[11px]">{k}: <span className="text-foreground">{JSON.stringify(v)}</span></span>
          ))}
        </div>
      )}
      {it.is_metrics && it.oos_metrics && (
        <div className="mt-2 space-y-1">
          <MetricPair label="expectancy" is={it.is_metrics.expectancy} oos={it.oos_metrics.expectancy} />
          <MetricsLine m={it.oos_metrics} />
        </div>
      )}
      {it.gate && <p className="mt-2 font-mono text-[11px] text-muted-foreground">gate: {it.gate}</p>}
      {it.rationale && <p className="mt-1 text-xs text-muted-foreground">{it.rationale}</p>}
      {it.note && <p className="mt-1 text-xs text-warning">{it.note}</p>}
    </div>
  );
}

function BaselineCard({ meta }: { meta: NonNullable<ResearchState["meta"]> }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Baseline — the bar to beat</div>
        <div className="font-mono text-xs text-muted-foreground">{meta.model}</div>
      </div>
      <div className="mt-2"><MetricPair label="expectancy" is={meta.baseline_is.expectancy} oos={meta.baseline_oos.expectancy} /></div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
        <span>IS {meta.is_range[0]} → {meta.is_range[1]}</span>
        <span>OOS {meta.oos_range[0]} → {meta.oos_range[1]}</span>
        <span>keep if OOS &gt; 0 and ≥ 60% of IS and ≥ 30 trades</span>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">Changes are sandboxed to a config copy — your live trading config and git history are untouched. Run the CLI to adopt a winning config for real.</p>
    </div>
  );
}

// The live display: baseline + scrollable auto-scrolling iteration list + done summary. `className` sizes
// the scroll region (flex-1 for the full Backtest tab, a bounded height for the Live-tab inline view).
export function ResearchStream({ research, className }: { research: ResearchState; className?: string }) {
  const { meta, iterations, done, phase } = research;
  const ref = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [iterations.length]);

  if (!meta && iterations.length === 0) {
    // phase set (baseline) but no meta yet: the loop has started and is running the ~2-3min baseline backtests.
    if (phase) {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="h-8 w-8 animate-spin" />
          <p className="text-sm">Warming up — running baseline in-sample &amp; out-of-sample backtests…</p>
        </div>
      );
    }
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
        <FlaskConical className="h-8 w-8" />
        <p className="max-w-md text-center text-sm">No loop yet. Click <span className="font-medium text-foreground">Run loop</span> to watch the agent propose one change per round, backtest it in-sample &amp; out-of-sample, and keep only what survives.</p>
      </div>
    );
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {meta && <BaselineCard meta={meta} />}
      <div
        ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className={`flex flex-col gap-2 overflow-auto ${className ?? "min-h-0 flex-1"}`}
      >
        {iterations.map((it) => (
          <IterationCard key={it.iteration} it={it} />
        ))}
        {done && (
          <div className="rounded-lg border border-primary/40 bg-primary/5 px-4 py-3 text-sm">
            <span className="font-semibold">Loop finished.</span> {done.accepted}/{done.iterations} changes accepted · final best OOS expectancy{" "}
            <span className={`font-mono font-semibold ${expColor(done.best_oos)}`}>{signedUsd(done.best_oos)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
