// Interactive SL/TP/entry lines for OPEN positions — like drawings.ts/tradeZones.ts, an ISeriesPrimitive
// that canvas-paints horizontal lines. Unlike the (non-interactive) native price lines it replaces, these
// expose hitTest()/setPreview() so CandlestickChart can drag a stop/target and commit on drop, and offers an
// × on the entry line to market-close. The primitive only draws + hit-tests; the money action lives in the
// parent (account_action graph). Ceiling: overlapping positions on one instrument hit-test to the nearest.
import type {
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  ISeriesApi,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export type ManagedPosition = { tradeId: string; instrument?: string; direction?: string; units?: number; entry?: number; stop?: number; tp?: number };
export type PosHit = { kind: "sl" | "tp" | "close"; tradeId: string };

const RED = "#ef5350";
const GREEN = "#26a69a";
const BLUE = "#3b82f6";
const LABEL_BG = "rgba(19,23,34,0.92)";
const CLOSE_W = 16; // × button box (px)
const TOL = 6; // vertical hit tolerance for a line (px)

type Leg = { kind: "entry" | "sl" | "tp"; tradeId: string; y: number; color: string; label: string };
type Preview = { tradeId: string; kind: "sl" | "tp"; price: number };

class Renderer implements IPrimitivePaneRenderer {
  constructor(private readonly src: PositionControls) {}
  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      this.src.setWidth(mediaSize.width);
      const w = mediaSize.width;
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      for (const leg of this.src.resolve()) {
        ctx.setLineDash(leg.kind === "entry" ? [] : [6, 3]);
        ctx.lineWidth = leg.kind === "entry" ? 1 : 1.25;
        ctx.strokeStyle = leg.color;
        ctx.beginPath();
        ctx.moveTo(0, leg.y);
        ctx.lineTo(w, leg.y);
        ctx.stroke();
        // left label
        const lw = ctx.measureText(leg.label).width + 8;
        ctx.setLineDash([]);
        ctx.fillStyle = LABEL_BG;
        ctx.fillRect(4, leg.y - 8, lw, 16);
        ctx.fillStyle = leg.color;
        ctx.fillText(leg.label, 8, leg.y + 4);
        // right affordance
        const x = w - CLOSE_W - 4;
        if (leg.kind === "entry") {
          ctx.fillStyle = "rgba(239,83,80,0.92)"; // × close box
          ctx.fillRect(x, leg.y - 8, CLOSE_W, 16);
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(x + 5, leg.y - 4);
          ctx.lineTo(x + CLOSE_W - 5, leg.y + 4);
          ctx.moveTo(x + CLOSE_W - 5, leg.y - 4);
          ctx.lineTo(x + 5, leg.y + 4);
          ctx.stroke();
        } else {
          ctx.fillStyle = leg.color; // drag grip
          ctx.fillRect(x, leg.y - 5, CLOSE_W, 10);
        }
      }
      ctx.setLineDash([]);
    });
  }
}

class PaneView implements IPrimitivePaneView {
  constructor(private readonly src: PositionControls) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "top";
  }
  renderer(): IPrimitivePaneRenderer {
    return new Renderer(this.src);
  }
}

export class PositionControls implements ISeriesPrimitive<Time> {
  private data: ManagedPosition[] = [];
  private preview: Preview | null = null;
  private width = 0;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdateFn?: () => void;
  private readonly view = new PaneView(this);

  attached(p: SeriesAttachedParameter<Time>): void {
    this.series = p.series;
    this.requestUpdateFn = p.requestUpdate;
  }
  detached(): void {
    this.series = null;
    this.requestUpdateFn = undefined;
  }
  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setData(positions: ManagedPosition[]): void {
    this.data = positions;
    this.requestUpdateFn?.();
  }
  setPreview(p: Preview | null): void {
    this.preview = p;
    this.requestUpdateFn?.();
  }
  setWidth(w: number): void {
    this.width = w;
  }

  private priceOf(p: ManagedPosition, kind: "sl" | "tp"): number | undefined {
    if (this.preview && this.preview.tradeId === p.tradeId && this.preview.kind === kind) return this.preview.price;
    return kind === "sl" ? p.stop : p.tp;
  }

  resolve(): Leg[] {
    const s = this.series;
    if (!s) return [];
    const dp = (v: number, inst?: string) => v.toFixed(inst?.includes("JPY") ? 3 : 5);
    const legs: Leg[] = [];
    for (const p of this.data) {
      const push = (kind: Leg["kind"], price: number | undefined, color: string, tag: string) => {
        if (price == null) return;
        const y = s.priceToCoordinate(price);
        if (y == null) return;
        legs.push({ kind, tradeId: p.tradeId, y, color, label: `${tag} ${dp(price, p.instrument)}` });
      };
      push("tp", this.priceOf(p, "tp"), GREEN, "TP");
      push("entry", p.entry, BLUE, `#${p.tradeId} ×`);
      push("sl", this.priceOf(p, "sl"), RED, "SL");
    }
    return legs;
  }

  /** What's under the cursor (media coords): a draggable SL/TP handle, the × close box, or nothing.
   *  Named `pick`, not `hitTest`, to avoid clashing with lightweight-charts' ISeriesPrimitive.hitTest. */
  pick(x: number, y: number): PosHit | null {
    const s = this.series;
    if (!s) return null;
    let best: { hit: PosHit; d: number } | null = null;
    const inCloseBox = x >= this.width - CLOSE_W - 4 && x <= this.width - 4;
    for (const p of this.data) {
      if (p.entry != null && inCloseBox) {
        const ey = s.priceToCoordinate(p.entry);
        if (ey != null && Math.abs(y - ey) <= 8) return { kind: "close", tradeId: p.tradeId }; // × takes priority
      }
      for (const kind of ["sl", "tp"] as const) {
        const price = this.priceOf(p, kind);
        if (price == null) continue;
        const ly = s.priceToCoordinate(price);
        if (ly == null) continue;
        const d = Math.abs(y - ly);
        if (d <= TOL && (!best || d < best.d)) best = { hit: { kind, tradeId: p.tradeId }, d };
      }
    }
    return best?.hit ?? null;
  }
}
