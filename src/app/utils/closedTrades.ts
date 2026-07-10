// Map OANDA closed trades (from the `account` graph via useAccount) into the chart's Trade shape, so
// CandlestickChart's existing entry/exit marker logic renders them — no new chart code needed.
import type { ClosedTrade } from "@/app/hooks/useAccount";
import type { Trade } from "@/app/utils/reduceEvents";

const num = (s?: string): number | undefined => {
  if (s == null) return undefined;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : undefined;
};

/** Closed OANDA trades → Trade[], filtered to `instrument` and skipping ids already in `excludeIds`. */
export function closedToTrades(closed: ClosedTrade[], instrument?: string, excludeIds?: Set<string>): Trade[] {
  const out: Trade[] = [];
  for (const t of closed) {
    if (instrument && t.instrument !== instrument) continue;
    if (t.id && excludeIds?.has(t.id)) continue;
    out.push({
      signal_id: t.id ?? `${t.instrument ?? "?"}-${t.closeTime ?? ""}`,
      signal_ts: t.openTime ?? t.closeTime ?? "",
      instrument: t.instrument,
      status: "closed",
      direction: (num(t.initialUnits) ?? 0) < 0 ? "SHORT" : "LONG",
      trade_id: t.id,
      units: num(t.initialUnits),
      fill_ts: t.openTime,
      fill_price: num(t.price),
      exit_ts: t.closeTime,
      exit_price: num(t.averageClosePrice),
      pnl: num(t.realizedPL),
    });
  }
  return out;
}
