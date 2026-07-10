// Runnable self-check for closedToTrades. No framework — run: npx tsx src/app/utils/closedTrades.selfcheck.ts
import assert from "node:assert";
import type { ClosedTrade } from "@/app/hooks/useAccount";
import { closedToTrades } from "./closedTrades";

const closed: ClosedTrade[] = [
  { id: "1", instrument: "AUD_USD", initialUnits: "1000", price: "0.6900", averageClosePrice: "0.6950", realizedPL: "5.00", openTime: "2026-07-01T08:00:00Z", closeTime: "2026-07-01T12:00:00Z" },
  { id: "2", instrument: "AUD_USD", initialUnits: "-2000", price: "0.6950", averageClosePrice: "0.6900", realizedPL: "10.00", openTime: "2026-07-02T08:00:00Z", closeTime: "2026-07-02T10:00:00Z" },
  { id: "3", instrument: "EUR_USD", initialUnits: "500", price: "1.10", averageClosePrice: "1.11", realizedPL: "5.00", closeTime: "2026-07-03T00:00:00Z" },
];

const mapped = closedToTrades(closed, "AUD_USD");
assert.equal(mapped.length, 2, "filters to AUD_USD");
assert.equal(mapped[0].direction, "LONG", "positive units → LONG");
assert.equal(mapped[1].direction, "SHORT", "negative units → SHORT");
assert.equal(mapped[0].fill_price, 0.69, "entry price parsed");
assert.equal(mapped[0].exit_price, 0.695, "close price parsed");
assert.equal(mapped[1].pnl, 10, "realizedPL parsed");
assert.equal(mapped[0].status, "closed", "status closed");

const deduped = closedToTrades(closed, "AUD_USD", new Set(["1"]));
assert.equal(deduped.length, 1, "excludeIds drops id 1");
assert.equal(deduped[0].trade_id, "2", "remaining is id 2");

console.log("closedTrades.selfcheck: all assertions passed");
