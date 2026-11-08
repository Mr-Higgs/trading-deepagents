"use client";

// Header status: server connection · what's running (visible from every tab, with Stop) · account.
// Answers "is the bot doing anything, and is it talking to the server?" without opening a tab.
import { useEffect, useState } from "react";
import { Square, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { useAccountContext } from "@/providers/AccountProvider";

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

const ago = (now: number, t: number | null) => {
  if (t == null) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
};

const fmt = (v?: string) =>
  v === undefined
    ? "—"
    : parseFloat(v).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });

export function StatusBar({
  onOpenConnection,
}: {
  onOpenConnection: () => void;
}) {
  const now = useNow();
  const {
    isLoading,
    runMeta,
    lastInput,
    lastCheckAt,
    research,
    pnl,
    error,
    stopStream,
    finished,
    trades,
  } = useChatContext();
  const {
    data,
    error: accErr,
    loading: accLoading,
    updatedAt,
  } = useAccountContext();

  // --- connection (driven by the existing account poller; no extra polling) ---
  const conn = accErr
    ? "error"
    : updatedAt
    ? "ok"
    : accLoading
    ? "pending"
    : "error";
  const connDot = {
    ok: "bg-success",
    error: "bg-destructive",
    pending: "bg-muted-foreground/40 animate-pulse",
  }[conn];
  const connLabel = {
    ok: "Connected",
    error: "Not connected",
    pending: "Connecting…",
  }[conn];

  // --- what's running ---
  const mode = (runMeta?.mode ?? lastInput?.mode) as string | undefined;
  const backend =
    runMeta?.backend ??
    (lastInput?.execution === "oanda" ? "oanda" : "simulated");
  const lastIt = research.iterations.at(-1);
  let run: { label: string; cls: string; title: string } | null = null;
  if (isLoading) {
    if (mode === "research")
      run = {
        label: lastIt
          ? `Research · iteration ${lastIt.iteration}${
              lastIt.total ? `/${lastIt.total}` : ""
            }`
          : "Research · warming up",
        cls: "border-primary/50 text-primary",
        title:
          "The strategy-tuning loop is running on past data. No orders are placed.",
      };
    else if (mode === "live" && backend === "oanda")
      run = {
        label: `Live · paper orders on OANDA practice${
          runMeta?.instrument ? ` · ${runMeta.instrument}` : ""
        }`,
        cls: "border-warning/60 bg-warning/10 text-warning",
        title:
          "Signals place real orders on the OANDA practice (demo) account.",
      };
    else if (mode === "live")
      run = {
        label: `Live · simulated fills${
          runMeta?.instrument ? ` · ${runMeta.instrument}` : ""
        }`,
        cls: "border-success/50 text-success",
        title:
          "Watching real prices; fills are simulated locally, nothing is sent to OANDA.",
      };
    else
      run = {
        label: `Backtest running${
          runMeta?.instrument ? ` · ${runMeta.instrument}` : ""
        }`,
        cls: "border-chart-1/50 text-chart-1",
        title: "Replaying historical candles. No money, no orders.",
      };
  }
  const errMsg = error
    ? error instanceof Error
      ? error.message
      : String(error)
    : null;

  const s = data?.summary;
  const upl = parseFloat(s?.unrealizedPL ?? "0");

  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2 text-xs">
      <button
        onClick={onOpenConnection}
        title={
          accErr
            ? `${connLabel}: ${accErr}. Click to check the connection settings.`
            : `${connLabel} to the trading server`
        }
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-muted-foreground hover:text-foreground"
      >
        <span className={`inline-block h-2 w-2 rounded-full ${connDot}`} />
        {connLabel}
      </button>

      {run ? (
        <div
          className={`inline-flex items-center gap-2 rounded-md border px-2 py-0.5 font-medium ${run.cls}`}
          title={run.title}
        >
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
          {run.label}
          {pnl.halted && mode !== "research" && (
            <span className="inline-flex items-center gap-1 text-destructive">
              <AlertTriangle className="h-3 w-3" /> halted: {pnl.haltReason}
            </span>
          )}
          {lastCheckAt && mode === "live" && (
            <span className="font-normal opacity-80">
              last check {ago(now, Date.parse(lastCheckAt))}
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={stopStream}
            className="h-6 px-2 text-xs"
          >
            <Square className="mr-1 h-3 w-3" /> Stop
          </Button>
        </div>
      ) : errMsg ? (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-destructive/40 px-2 py-1 text-destructive"
          title={errMsg}
        >
          <AlertTriangle className="h-3 w-3" /> Last run disconnected
        </span>
      ) : finished || research.done ? (
        <span
          className="rounded-md border border-border px-2 py-1 text-foreground"
          title="The last run has ended. Its results are on the Trade tab (or Research tab for a research loop)."
        >
          {mode === "research"
            ? `Research finished · ${
                research.done?.accepted ?? research.done?.proposed ?? 0
              } of ${research.done?.iterations ?? 0} kept`
            : `${mode === "live" ? "Live run" : "Backtest"} finished · ${
                trades.filter((t) => t.status === "closed").length
              } trades`}
        </span>
      ) : (
        <span className="rounded-md border border-border px-2 py-1 text-muted-foreground">
          Nothing running
        </span>
      )}

      {s && (
        <div
          className="inline-flex items-center gap-3 rounded-md border border-border bg-card px-2 py-1 font-mono"
          title={`OANDA practice account ${data?.account ?? ""}`}
        >
          <span className="text-muted-foreground">
            NAV <span className="text-foreground">{fmt(s.NAV)}</span>
          </span>
          <span className="hidden text-muted-foreground md:inline">
            Bal <span className="text-foreground">{fmt(s.balance)}</span>
          </span>
          <span className="text-muted-foreground">
            Open P/L{" "}
            <span
              className={
                upl > 0
                  ? "text-success"
                  : upl < 0
                  ? "text-destructive"
                  : "text-foreground"
              }
            >
              {upl >= 0 ? "+" : "−"}
              {Math.abs(upl).toFixed(2)}
            </span>
          </span>
          <span className="hidden text-muted-foreground lg:inline">
            {s.currency} · {ago(now, updatedAt)}
          </span>
        </div>
      )}
    </div>
  );
}
