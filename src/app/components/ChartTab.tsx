"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, AlertTriangle, Square, Grid2x2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { useCandles, TIMEFRAMES } from "@/app/hooks/useCandles";
import { useAccountContext } from "@/providers/AccountProvider";
import { closedToTrades } from "@/app/utils/closedTrades";
import { openToTrades } from "@/app/utils/openTrades";
import { useDrawings } from "@/app/hooks/useDrawings";
import { CandlestickChart } from "@/app/components/CandlestickChart";
import { ChartToolbar } from "@/app/components/ChartToolbar";
import { MultiChart } from "@/app/components/MultiChart";
import { SignalsPanel } from "@/app/components/SignalsPanel";
import type { Tool } from "@/app/components/drawings";
import type { Trade } from "@/app/utils/reduceEvents";

export function ChartTab() {
  const { data, loading, error, refresh, timeframe, setTimeframe } =
    useCandles();
  const { trades } = useChatContext();
  const { data: account, closeTrade, modifyTrade } = useAccountContext();
  const [layout, setLayout] = useState<"single" | "grid">("single");

  // human-driven position management: drag a stop/target → modify; × on the entry line → confirm + close
  const onClosePosition = useCallback(
    (tradeId: string) => {
      if (window.confirm(`Market-close position #${tradeId} now?`))
        void closeTrade(tradeId, "ALL");
    },
    [closeTrade]
  );
  // A drag commits immediately, so offer Undo (restores the level the line had before the drag).
  const tradesRef = useRef<Trade[]>([]);
  const onModifyPosition = useCallback(
    async (tradeId: string, kind: "sl" | "tp", price: number) => {
      const key = kind === "sl" ? "stop" : "tp";
      const before = tradesRef.current.find((t) => t.trade_id === tradeId)?.[
        key
      ];
      const r = await modifyTrade(tradeId, {
        [key]: price,
        instrument: data?.instrument,
      });
      if (r?.ok && before != null)
        toast(
          `${kind.toUpperCase()} of #${tradeId} moved to ${price.toFixed(5)}`,
          {
            action: {
              label: "Undo",
              onClick: () =>
                void modifyTrade(tradeId, {
                  [key]: before,
                  instrument: data?.instrument,
                }),
            },
          }
        );
    },
    [modifyTrade, data?.instrument]
  );
  const [tool, setTool] = useState<Tool>("cross");
  const [showIchimoku, setShowIchimoku] = useState(true);
  const [drawings, setDrawings] = useDrawings(data?.instrument, timeframe);

  // event-stream trades + real OANDA open positions + closed OANDA positions (all deduped by trade id),
  // for this instrument. Open positions carry Entry/SL/TP so the chart draws their lines like OANDA does,
  // even for trades opened outside the current run.
  const chartTrades = useMemo(() => {
    const seen = new Set(
      trades.map((t) => t.trade_id).filter((id): id is string => !!id)
    );
    return [
      ...trades,
      ...openToTrades(account?.trades ?? [], data?.instrument, seen),
      ...closedToTrades(account?.closed_trades ?? [], data?.instrument, seen),
    ];
  }, [trades, account?.trades, account?.closed_trades, data?.instrument]);
  useEffect(() => {
    tradesRef.current = chartTrades;
  }, [chartTrades]);

  const candles = data?.candles ?? [];
  const grid = layout === "grid";
  const last = candles.at(-1);
  const prev = candles.at(-2);
  const change = last && prev ? last.c - prev.c : 0;
  const pct = last && prev && prev.c ? (change / prev.c) * 100 : 0;
  const pipSize = data?.instrument?.includes("JPY") ? 0.01 : 0.0001;
  const dp = pipSize === 0.01 ? 3 : 5;
  const sign = (n: number) => (n >= 0 ? "+" : "−");

  return (
    <div className="flex h-full flex-col">
      {/* Quote + controls bar */}
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm font-semibold text-foreground">
            {data?.instrument ?? "…"}
          </span>
          {last && (
            <>
              <span className="font-mono text-xl tabular-nums text-foreground">
                {last.c.toFixed(dp)}
              </span>
              <span
                className={`font-mono text-xs tabular-nums ${
                  change >= 0 ? "text-success" : "text-destructive"
                }`}
              >
                {sign(change)}
                {Math.abs(change).toFixed(dp)} ({sign(pct)}
                {Math.abs(pct).toFixed(2)}%)
              </span>
              <span className="text-xxs uppercase tracking-wide text-muted-foreground">
                last close
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowIchimoku((v) => !v)}
            aria-pressed={showIchimoku}
            title="Toggle Ichimoku overlay"
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              showIchimoku
                ? "text-primary-foreground border-primary bg-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            Ichimoku
          </button>
          {!grid && (
            <div className="inline-flex rounded-md border border-border p-0.5">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={`rounded px-2.5 py-1 text-xs font-medium ${
                    timeframe === tf
                      ? "text-primary-foreground bg-primary"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
          <div className="inline-flex rounded-md border border-border p-0.5">
            <button
              onClick={() => setLayout("single")}
              title="Single chart"
              className={`rounded p-1 ${
                !grid
                  ? "text-primary-foreground bg-primary"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Square className="h-4 w-4" />
            </button>
            <button
              onClick={() => setLayout("grid")}
              title="All timeframes (D · H4 · H1 · M15)"
              className={`rounded p-1 ${
                grid
                  ? "text-primary-foreground bg-primary"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Grid2x2 className="h-4 w-4" />
            </button>
          </div>
          {!grid && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void refresh()}
              disabled={loading}
            >
              <RefreshCw
                className={`h-4 w-4 ${
                  loading ? "motion-safe:animate-spin" : ""
                }`}
              />
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="mx-3 mt-2 flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {grid ? (
        <div className="flex min-h-0 flex-1 p-3">
          <MultiChart
            trades={chartTrades}
            showIchimoku={showIchimoku}
            onModifyPosition={onModifyPosition}
            onClosePosition={onClosePosition}
          />
        </div>
      ) : (
        // ponytail: drawing tools only in single mode (grid has 4 charts) — an acceptable ceiling.
        <div className="flex min-h-0 flex-1">
          <ChartToolbar
            tool={tool}
            onTool={setTool}
            onClear={() => setDrawings([])}
          />
          <div className="min-h-0 flex-1">
            {candles.length ? (
              <CandlestickChart
                candles={candles}
                ichimoku={data?.ichimoku}
                trades={chartTrades}
                tool={tool}
                drawings={drawings}
                onDrawingsChange={setDrawings}
                pipSize={pipSize}
                showIchimoku={showIchimoku}
                onModifyPosition={onModifyPosition}
                onClosePosition={onClosePosition}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {loading ? "Loading candles…" : "No candle data."}
              </div>
            )}
          </div>
          <div className="hidden w-80 shrink-0 p-2 lg:block">
            <SignalsPanel
              signals={data?.signals}
              instrument={data?.instrument}
              timeframe={data?.timeframe}
            />
          </div>
        </div>
      )}
    </div>
  );
}
