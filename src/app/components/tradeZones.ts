// ponytail: mirrors IchimokuCloud — lightweight-charts has no native "box between two prices over a
// time span" primitive. Per open trade it paints a translucent reward band (entry↔tp, green) and a
// risk band (entry↔stop, red) from the fill time to the latest bar, so R:R is visible on the chart.
// Ceiling: a trade whose fill scrolls off the left edge disappears (timeToCoordinate → null); upgrade
// path is clamping x to the pane edges if that matters.
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

export type TradeZone = { tFrom: Time; tTo: Time; entry: number; stop: number; tp: number };

const REWARD = "rgba(38,166,154,0.10)"; // entry → tp
const RISK = "rgba(239,83,80,0.10)"; // entry → stop

type Box = { x0: number; x1: number; yEntry: number; yStop: number; yTp: number };

class ZonesRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly boxes: Box[]) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      for (const b of this.boxes) {
        rect(ctx, REWARD, b.x0, b.x1, b.yEntry, b.yTp);
        rect(ctx, RISK, b.x0, b.x1, b.yEntry, b.yStop);
      }
    });
  }
}

function rect(ctx: CanvasRenderingContext2D, color: string, x0: number, x1: number, y0: number, y1: number): void {
  ctx.fillStyle = color;
  ctx.fillRect(x0, Math.min(y0, y1), x1 - x0, Math.abs(y1 - y0));
}

class ZonesPaneView implements IPrimitivePaneView {
  constructor(private readonly src: TradeZones) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "bottom"; // draw under candles/lines
  }
  renderer(): IPrimitivePaneRenderer | null {
    const boxes = this.src.boxes();
    return boxes.length ? new ZonesRenderer(boxes) : null;
  }
}

export class TradeZones implements ISeriesPrimitive<Time> {
  private data: TradeZone[] = [];
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private timeScale: ITimeScaleApi<Time> | null = null;
  private requestUpdateFn?: () => void;
  private readonly view = new ZonesPaneView(this);

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

  setData(zones: TradeZone[]): void {
    this.data = zones;
    this.requestUpdateFn?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  // Recomputed each render so it tracks pan/zoom (time/priceToCoordinate are viewport-aware).
  boxes(): Box[] {
    const ts = this.timeScale;
    const s = this.series;
    if (!ts || !s) return [];
    const out: Box[] = [];
    for (const z of this.data) {
      const x0 = ts.timeToCoordinate(z.tFrom);
      const x1 = ts.timeToCoordinate(z.tTo);
      const yEntry = s.priceToCoordinate(z.entry);
      const yStop = s.priceToCoordinate(z.stop);
      const yTp = s.priceToCoordinate(z.tp);
      if (x0 == null || x1 == null || yEntry == null || yStop == null || yTp == null) continue;
      out.push({ x0, x1, yEntry, yStop, yTp });
    }
    return out;
  }
}
