// Runnable self-check for the pure drawing geometry. No test framework — run: npx tsx src/app/components/drawings.selfcheck.ts
import assert from "node:assert";
import type { Time } from "lightweight-charts";
import { pointToSegmentDistancePx, hitTest, measureStats, type Drawing, type Pt, type XY } from "./drawings";

// pointToSegmentDistancePx
const a = { x: 0, y: 0 };
const b = { x: 10, y: 0 };
assert.equal(pointToSegmentDistancePx({ x: 5, y: 0 }, a, b), 0, "point on segment");
assert.equal(pointToSegmentDistancePx({ x: 5, y: 3 }, a, b), 3, "perpendicular distance");
assert.equal(pointToSegmentDistancePx({ x: 15, y: 0 }, a, b), 5, "clamps past endpoint");
assert.equal(pointToSegmentDistancePx({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 }), 5, "degenerate segment");

// measureStats
assert.deepEqual(measureStats(0.69, 0.695, 0.0001, 10), ["+0.00500", "+50.0 pips", "10 bars"], "up move");
assert.deepEqual(measureStats(0.695, 0.69, 0.0001, 10), ["−0.00500", "−50.0 pips", "10 bars"], "down move");

// hitTest — resolver maps time→x, price→y directly
const xy = (pt: Pt): XY => ({ x: Number(pt.time), y: pt.price });
const yOf = (price: number) => price;
const t = (n: number) => n as unknown as Time;
const drawings: Drawing[] = [
  { id: "tl", type: "trendline", a: { time: t(0), price: 1 }, b: { time: t(10), price: 1 } },
  { id: "hl", type: "hline", price: 5 },
];
assert.equal(hitTest(drawings, { x: 5, y: 1 }, xy, yOf), "tl", "click on trendline");
assert.equal(hitTest(drawings, { x: 5, y: 5.2 }, xy, yOf), "hl", "click near hline (vertical distance)");
assert.equal(hitTest(drawings, { x: 5, y: 20 }, xy, yOf), null, "far click misses");

console.log("drawings.selfcheck: all assertions passed");
