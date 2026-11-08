"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useClient } from "@/providers/ClientProvider";

export type Candle = { t: number; o: number; h: number; l: number; c: number };
export type Ichimoku = {
  tenkan: (number | null)[];
  kijun: (number | null)[];
  senkou_a: (number | null)[];
  senkou_b: (number | null)[];
  chikou: (number | null)[];
};
export type SignalState = {
  price_vs_cloud: "bullish" | "bearish" | "neutral" | "unknown";
  tenkan_gt_kijun: boolean | null;
  chikou_above_price: boolean | null;
  price_above_kijun: boolean | null;
  price_above_tenkan: boolean | null;
};
export type FiredSignal = { name: string; dir: "up" | "down"; strength: "strong" | "alert" };
export type Signals = { state: SignalState; signals: FiredSignal[] };

export type CandlesSnapshot = {
  instrument?: string;
  timeframe?: string;
  candles?: Candle[];
  ichimoku?: Ichimoku;
  signals?: Signals;
  error?: string;
};

export const TIMEFRAMES = ["M5", "M15", "H1", "H4", "D"] as const;

/** Poll the read-only `candles` graph: OHLC + Ichimoku lines + the alert/signal taxonomy. */
export function useCandles(pollMs = 45_000, fixedTimeframe?: string) {
  const client = useClient();
  const [timeframe, setTimeframe] = useState<string>(fixedTimeframe ?? "M15");
  const [data, setData] = useState<CandlesSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return; // don't stack polls: a slow/hung run must not spawn more (else runs pile up busy)
    inFlight.current = true;
    try {
      const res = (await client.runs.wait(null, "candles", { input: { timeframe }, signal: AbortSignal.timeout(30_000) })) as CandlesSnapshot;
      if (res?.error) setError(res.error);
      else {
        setData(res);
        setError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [client, timeframe]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const tick = () => {
      if (active) void refresh();
    };
    tick();
    const id = setInterval(tick, pollMs);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [refresh, pollMs]);

  return { data, loading, error, refresh, timeframe, setTimeframe };
}
