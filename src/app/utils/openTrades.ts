// Map OANDA open trades (from the `account` graph via useAccount) into the chart's Trade shape, so
// CandlestickChart's existing open-trade Entry/SL/TP line logic renders real positions — no new chart code.
// Mirror of closedToTrades for status:"open".
import type { OpenTrade } from "@/app/hooks/useAccount";
import type { Trade } from "@/app/utils/reduceEvents";

const num = (s?: string): number | undefined => {
  if (s == null) return undefined;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : undefined;
};

/** Open OANDA trades → Trade[], filtered to `instrument` and skipping ids already in `excludeIds`. */
export function openToTrades(open: OpenTrade[], instrument?: string, excludeIds?: Set<string>): Trade[] {
  const out: Trade[] = [];
  for (const t of open) {
    if (instrument && t.instrument !== instrument) continue;
    // dedupe against the run's event-stream trades — by OANDA ticket id OR our client id (clientExtensions.id)
    if (t.id && excludeIds?.has(t.id)) continue;
    if (t.clientExtensions?.id && excludeIds?.has(t.clientExtensions.id)) continue;
    out.push({
      signal_id: t.id ?? `${t.instrument ?? "?"}-${t.openTime ?? ""}`,
      signal_ts: t.openTime ?? "",
      instrument: t.instrument,
      status: "open",
      direction: (num(t.currentUnits) ?? 0) < 0 ? "SHORT" : "LONG",
      trade_id: t.id,
      units: num(t.currentUnits),
      fill_ts: t.openTime,
      fill_price: num(t.price),
      stop: num(t.stopLossOrder?.price), // absent → chart just skips the SL line
      tp: num(t.takeProfitOrder?.price),
    });
  }
  return out;
}
