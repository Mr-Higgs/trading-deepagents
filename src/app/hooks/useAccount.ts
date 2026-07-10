"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useClient } from "@/providers/ClientProvider";

// OANDA numeric fields come back as strings.
export type AccountSummary = {
  NAV?: string;
  balance?: string;
  unrealizedPL?: string;
  pl?: string;
  marginUsed?: string;
  marginAvailable?: string;
  marginRate?: string;
  openTradeCount?: number;
  openPositionCount?: number;
  positionValue?: string;
  currency?: string;
};

export type OpenPosition = {
  instrument?: string;
  long?: { units?: string; averagePrice?: string; unrealizedPL?: string };
  short?: { units?: string; averagePrice?: string; unrealizedPL?: string };
  unrealizedPL?: string;
};

export type OpenTrade = {
  id?: string;
  instrument?: string;
  currentUnits?: string;
  price?: string;
  unrealizedPL?: string;
  openTime?: string;
  // OANDA attaches these dependent orders when the trade was opened with SL/TP on fill (the paper backend does).
  stopLossOrder?: { price?: string };
  takeProfitOrder?: { price?: string };
  // our loop tags orders with clientExtensions.id (== the stream trade_id) — used to dedupe against the run's trades.
  clientExtensions?: { id?: string };
};

export type ClosedTrade = {
  id?: string;
  instrument?: string;
  initialUnits?: string;
  price?: string; // entry
  averageClosePrice?: string;
  realizedPL?: string;
  openTime?: string; // RFC3339 — backend returns it; used for the entry marker
  closeTime?: string;
};

export type AccountSnapshot = {
  account?: string;
  summary?: AccountSummary;
  positions?: OpenPosition[];
  trades?: OpenTrade[];
  closed_trades?: ClosedTrade[]; // raw graph-state key (snake_case)
  error?: string;
};

/** Poll the read-only `account` LangGraph graph for a live OANDA practice-account snapshot. */
export function useAccount(pollMs = 10_000) {
  const client = useClient();
  const [data, setData] = useState<AccountSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return; // don't stack polls: a slow/hung run must not spawn more (else runs pile up busy)
    inFlight.current = true;
    try {
      const res = (await client.runs.wait(null, "account", { input: {}, signal: AbortSignal.timeout(30_000) })) as AccountSnapshot;
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
  }, [client]);

  useEffect(() => {
    let active = true;
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

  return { data, loading, error, refresh };
}
