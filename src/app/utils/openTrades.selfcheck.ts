// Runnable self-check for openToTrades. No framework — run: npx tsx src/app/utils/openTrades.selfcheck.ts
import assert from "node:assert";
import type { OpenTrade } from "@/app/hooks/useAccount";
import { openToTrades } from "./openTrades";

const open: OpenTrade[] = [
  { id: "71", instrument: "AUD_USD", currentUnits: "90430", price: "0.69598", openTime: "2026-07-09T05:15:00Z", stopLossOrder: { price: "0.69263" }, takeProfitOrder: { price: "0.69834" } },
  { id: "67", instrument: "AUD_USD", currentUnits: "-100000", price: "0.69598", openTime: "2026-07-09T00:30:00Z" }, // no SL/TP attached
  { id: "9", instrument: "EUR_USD", currentUnits: "500", price: "1.10", openTime: "2026-07-09T00:00:00Z" },
];

const mapped = openToTrades(open, "AUD_USD");
assert.equal(mapped.length, 2, "filters to AUD_USD");
assert.equal(mapped[0].direction, "LONG", "positive units → LONG");
assert.equal(mapped[1].direction, "SHORT", "negative units → SHORT");
assert.equal(mapped[0].status, "open", "status open");
assert.equal(mapped[0].fill_price, 0.69598, "entry (fill) price parsed");
assert.equal(mapped[0].stop, 0.69263, "SL parsed from stopLossOrder");
assert.equal(mapped[0].tp, 0.69834, "TP parsed from takeProfitOrder");
assert.equal(mapped[1].stop, undefined, "no SL order → stop undefined (chart skips the line)");
assert.equal(mapped[1].tp, undefined, "no TP order → tp undefined");

const deduped = openToTrades(open, "AUD_USD", new Set(["71"]));
assert.equal(deduped.length, 1, "excludeIds drops id 71");
assert.equal(deduped[0].trade_id, "67", "remaining is id 67");

// a run-placed trade carries clientExtensions.id (== the stream trade_id); dedupe on that too, not just the ticket
const withClient: OpenTrade[] = [{ id: "88", instrument: "AUD_USD", currentUnits: "1000", price: "0.69", clientExtensions: { id: "AUD_USD-t9" } }];
assert.equal(openToTrades(withClient, "AUD_USD", new Set(["AUD_USD-t9"])).length, 0, "dropped by clientExtensions.id match");
assert.equal(openToTrades(withClient, "AUD_USD", new Set(["nope"])).length, 1, "kept when neither id matches");

console.log("openTrades.selfcheck: all assertions passed");
