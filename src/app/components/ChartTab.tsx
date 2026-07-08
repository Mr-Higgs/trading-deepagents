"use client";

import { useState } from "react";
import { RefreshCw, AlertTriangle, Square, Grid2x2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { useCandles, TIMEFRAMES } from "@/app/hooks/useCandles";
import { CandlestickChart } from "@/app/components/CandlestickChart";
import { MultiChart } from "@/app/components/MultiChart";
import { SignalsPanel } from "@/app/components/SignalsPanel";

export function ChartTab() {
  const { data, loading, error, refresh, timeframe, setTimeframe } = useCandles();
  const { trades } = useChatContext();
  const [layout, setLayout] = useState<"single" | "grid">("single");
  const candles = data?.candles ?? [];
  const grid = layout === "grid";

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm text-foreground">{data?.instrument ?? "…"}</span>
          {!grid && (
            <div className="inline-flex rounded-md border border-border p-0.5">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={`rounded px-2.5 py-1 text-xs font-medium ${timeframe === tf ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">candles + Ichimoku (Tenkan · Kijun · cloud)</span>
          <div className="inline-flex rounded-md border border-border p-0.5">
            <button
              onClick={() => setLayout("single")}
              title="Single chart"
              className={`rounded p-1 ${!grid ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              <Square className="h-4 w-4" />
            </button>
            <button
              onClick={() => setLayout("grid")}
              title="All timeframes (D · H4 · H1 · M15)"
              className={`rounded p-1 ${grid ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              <Grid2x2 className="h-4 w-4" />
            </button>
          </div>
          {!grid && (
            <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
          )}
        </div>
      </div>

      {error && !grid && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {grid ? (
        <MultiChart trades={trades} />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
          <div className="min-h-[340px] flex-1 rounded-lg border border-border bg-card p-2">
            {candles.length ? (
              <CandlestickChart candles={candles} ichimoku={data?.ichimoku} trades={trades} />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">{loading ? "Loading candles…" : "No candle data."}</div>
            )}
          </div>
          <div className="w-full lg:w-80">
            <SignalsPanel signals={data?.signals} instrument={data?.instrument} timeframe={data?.timeframe} />
          </div>
        </div>
      )}
    </div>
  );
}
