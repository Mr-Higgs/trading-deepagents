"use client";

// ponytail: 4 separate chart instances — lightweight-charts has no native multi-layout, and a single
// chart can't host 4 independent timeframes. Crosshair is synced (same instrument → price maps 1:1);
// each pane pans/zooms independently.
import { useCallback, useEffect, useState } from "react";
import type { MouseEventParams, Time } from "lightweight-charts";
import { useCandles } from "@/app/hooks/useCandles";
import { CandlestickChart, type ChartHandle } from "@/app/components/CandlestickChart";
import type { Trade } from "@/app/utils/reduceEvents";

const PANES = ["D", "H4", "H1", "M15"] as const;

function Pane({ tf, trades, pollMs, onReady }: { tf: string; trades: Trade[]; pollMs: number; onReady: (tf: string, h: ChartHandle | null) => void }) {
  const { data, loading, error } = useCandles(pollMs, tf);
  const candles = data?.candles ?? [];
  const handleReady = useCallback((h: ChartHandle | null) => onReady(tf, h), [tf, onReady]);
  return (
    <div className="relative flex min-h-0 flex-col rounded-lg border border-border bg-card p-1.5">
      <div className="absolute left-2 top-1.5 z-10 rounded bg-background/70 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">{tf}</div>
      <div className="min-h-0 flex-1">
        {candles.length ? (
          <CandlestickChart candles={candles} ichimoku={data?.ichimoku} trades={trades} onReady={handleReady} />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">{error ?? (loading ? "Loading…" : "No data")}</div>
        )}
      </div>
    </div>
  );
}

export function MultiChart({ trades, pollMs = 45_000 }: { trades: Trade[]; pollMs?: number }) {
  const [handles, setHandles] = useState<Record<string, ChartHandle | null>>({});

  const onReady = useCallback((tf: string, h: ChartHandle | null) => {
    setHandles((prev) => ({ ...prev, [tf]: h }));
  }, []);

  // Wire crosshair sync once ≥2 panes are live; each pane keeps its own independent time range.
  useEffect(() => {
    const hs = PANES.map((tf) => handles[tf]).filter((h): h is ChartHandle => !!h);
    if (hs.length < 2) return;
    const unsubs: (() => void)[] = [];

    for (const src of hs) {
      const others = hs.filter((h) => h !== src);
      const onCross = (param: MouseEventParams<Time>) => {
        const price = param.point ? src.candle.coordinateToPrice(param.point.y) : null;
        for (const o of others) {
          if (price != null && param.time != null) o.chart.setCrosshairPosition(price, param.time, o.candle);
          else o.chart.clearCrosshairPosition();
        }
      };
      src.chart.subscribeCrosshairMove(onCross);
      unsubs.push(() => src.chart.unsubscribeCrosshairMove(onCross));
    }

    return () => {
      for (const u of unsubs) u();
    };
  }, [handles]);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-4 gap-2 md:grid-cols-2 md:grid-rows-2">
      {PANES.map((tf) => (
        <Pane key={tf} tf={tf} trades={trades} pollMs={pollMs} onReady={onReady} />
      ))}
    </div>
  );
}
