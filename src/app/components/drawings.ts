// ponytail: user-drawn chart annotations (trendline / horizontal line / text / measure). lightweight-charts
// has no drawing tools, so — like ichimokuCloud.ts / tradeZones.ts — this is an ISeriesPrimitive that canvas-
// paints overlays, converting each anchor's {time, price} to pixels every render so it tracks pan/zoom.
// Ceiling: anchors must be inside the loaded range (timeToCoordinate → null off-screen makes a drawing vanish);
// single fixed color; upgrade path is per-drawing color + off-range clamping if it matters.
import type {
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  ISeriesApi,
  ITimeScaleApi,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export type Pt = { time: Time; price: number };
export type Tool = "cross" | "trendline" | "hline" | "text" | "measure" | "eraser";

export type Drawing =
  | { id: string; type: "trendline"; a: Pt; b: Pt }
  | { id: string; type: "hline"; price: number }
  | { id: string; type: "text"; at: Pt; text: string }
  | { id: string; type: "measure"; a: Pt; b: Pt };

export const DEFAULT_PIP = 0.0001;
const COLOR = "#38d9c4"; // teal, reads on navy
const LABEL_BG = "rgba(19,23,34,0.9)";

// ---- pure geometry helpers (see drawings.selfcheck.ts) --------------------------------------------

export type XY = { x: number; y: number };

/** Shortest distance in pixels from point p to segment a→b. */
export function pointToSegmentDistancePx(p: XY, a: XY, b: XY): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/**
 * Nearest drawing to a click, within `tol` px, else null.
 * `xy` resolves a time+price anchor to pixels (null if off-range); `priceToY` resolves a bare price
 * (hlines have no time, so they can't go through `xy`).
 */
export function hitTest(drawings: Drawing[], click: XY, xy: (pt: Pt) => XY | null, priceToY: (price: number) => number | null, tol = 6): string | null {
  let best: { id: string; d: number } | null = null;
  const consider = (id: string, d: number | null) => {
    if (d != null && d <= tol && (!best || d < best.d)) best = { id, d };
  };
  for (const dr of drawings) {
    if (dr.type === "trendline" || dr.type === "measure") {
      const a = xy(dr.a);
      const b = xy(dr.b);
      if (a && b) consider(dr.id, pointToSegmentDistancePx(click, a, b));
    } else if (dr.type === "hline") {
      const y = priceToY(dr.price);
      if (y != null) consider(dr.id, Math.abs(click.y - y)); // horizontal line: vertical distance only
    } else if (dr.type === "text") {
      const a = xy(dr.at);
      if (a) consider(dr.id, Math.hypot(click.x - a.x, click.y - a.y));
    }
  }
  return best ? (best as { id: string }).id : null;
}

/** Label lines for a measure: Δprice, pips, bar count. */
export function measureStats(aPrice: number, bPrice: number, pipSize: number, bars: number): string[] {
  const d = bPrice - aPrice;
  const pips = d / pipSize;
  const sign = d >= 0 ? "+" : "−";
  return [`${sign}${Math.abs(d).toFixed(5)}`, `${sign}${Math.abs(pips).toFixed(1)} pips`, `${Math.abs(bars)} bars`];
}

// ---- primitive ----------------------------------------------------------------------------------

type Resolved =
  | { kind: "line"; x0: number; y0: number; x1: number; y1: number; dashed: boolean }
  | { kind: "hline"; y: number }
  | { kind: "text"; x: number; y: number; text: string }
  | { kind: "measure"; x0: number; y0: number; x1: number; y1: number; lines: string[] };

class DrawingsRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly src: Drawings) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const shapes = this.src.resolve(mediaSize.width);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = COLOR;
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      for (const s of shapes) {
        if (s.kind === "line" || s.kind === "measure") {
          ctx.setLineDash(s.kind === "measure" ? [4, 3] : []);
          ctx.beginPath();
          ctx.moveTo(s.x0, s.y0);
          ctx.lineTo(s.x1, s.y1);
          ctx.stroke();
          if (s.kind === "measure") label(ctx, s.x1 + 6, s.y1, s.lines);
        } else if (s.kind === "hline") {
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(0, s.y);
          ctx.lineTo(mediaSize.width, s.y);
          ctx.stroke();
        } else if (s.kind === "text") {
          ctx.setLineDash([]);
          ctx.fillStyle = COLOR;
          ctx.fillText(s.text, s.x + 4, s.y - 4);
        }
      }
      ctx.setLineDash([]);
    });
  }
}

function label(ctx: CanvasRenderingContext2D, x: number, y: number, lines: string[]): void {
  const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 10;
  const h = lines.length * 14 + 6;
  ctx.fillStyle = LABEL_BG;
  ctx.fillRect(x, y - h / 2, w, h);
  ctx.fillStyle = COLOR;
  lines.forEach((l, i) => ctx.fillText(l, x + 5, y - h / 2 + 14 * (i + 1) - 2));
}

class DrawingsPaneView implements IPrimitivePaneView {
  constructor(private readonly src: Drawings) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "top"; // above candles, so annotations are visible
  }
  renderer(): IPrimitivePaneRenderer {
    return new DrawingsRenderer(this.src);
  }
}

export class Drawings implements ISeriesPrimitive<Time> {
  private data: Drawing[] = [];
  private draft: Drawing | null = null;
  private pipSize = DEFAULT_PIP;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private timeScale: ITimeScaleApi<Time> | null = null;
  private requestUpdateFn?: () => void;
  private readonly view = new DrawingsPaneView(this);

  attached(p: SeriesAttachedParameter<Time>): void {
    this.series = p.series;
    this.timeScale = p.chart.timeScale();
    this.requestUpdateFn = p.requestUpdate;
  }
  detached(): void {
    this.series = null;
    this.timeScale = null;
    this.requestUpdateFn = undefined;
  }

  setData(drawings: Drawing[]): void {
    this.data = drawings;
    this.requestUpdateFn?.();
  }
  setDraft(draft: Drawing | null): void {
    this.draft = draft;
    this.requestUpdateFn?.();
  }
  setPipSize(pip: number): void {
    this.pipSize = pip || DEFAULT_PIP;
  }

  /** Resolve an anchor to pixel coords (null if off-range). Also used by the click hit-test. */
  xy(pt: Pt): XY | null {
    const x = this.timeScale?.timeToCoordinate(pt.time);
    const y = this.series?.priceToCoordinate(pt.price);
    return x == null || y == null ? null : { x, y };
  }
  /** Resolve a bare price to a y coordinate (for hline hit-testing). */
  priceToY(price: number): number | null {
    const y = this.series?.priceToCoordinate(price);
    return y == null ? null : y;
  }

  drawings(): Drawing[] {
    return this.data;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  resolve(_width: number): Resolved[] {
    const ts = this.timeScale;
    const s = this.series;
    if (!ts || !s) return [];
    const all = this.draft ? [...this.data, this.draft] : this.data;
    const out: Resolved[] = [];
    for (const d of all) {
      if (d.type === "hline") {
        const y = s.priceToCoordinate(d.price);
        if (y != null) out.push({ kind: "hline", y });
      } else if (d.type === "text") {
        const p = this.xy(d.at);
        if (p) out.push({ kind: "text", x: p.x, y: p.y, text: d.text });
      } else {
        const a = this.xy(d.a);
        const b = this.xy(d.b);
        if (!a || !b) continue;
        if (d.type === "trendline") {
          out.push({ kind: "line", x0: a.x, y0: a.y, x1: b.x, y1: b.y, dashed: false });
        } else {
          const barSpacing = ts.options().barSpacing || 6;
          const bars = Math.round((b.x - a.x) / barSpacing);
          out.push({ kind: "measure", x0: a.x, y0: a.y, x1: b.x, y1: b.y, lines: measureStats(d.a.price, d.b.price, this.pipSize, bars) });
        }
      }
    }
    return out;
  }
}
