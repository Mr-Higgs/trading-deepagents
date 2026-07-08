// ponytail: this primitive exists because lightweight-charts can't fill between two curves natively
// (AreaSeries/BaselineSeries only fill to a fixed baseline). The Ichimoku cloud is the band between
// leading span A and B, colored green where A > B and red where A < B, flipping at each crossover.
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

export type CloudPoint = { t: Time; a: number; b: number }; // a = senkou_a, b = senkou_b

const GREEN = "rgba(34,197,94,0.15)"; // A > B
const RED = "rgba(239,68,68,0.15)"; // A < B

type XY = { x: number; ya: number; yb: number; up: boolean }; // up = A above B (in price)

class CloudRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly pts: XY[]) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      const pts = this.pts;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[i];
        const p1 = pts[i + 1];
        if (p0.up === p1.up) {
          fill(ctx, p0.up ? GREEN : RED, [p0.x, p0.ya], [p1.x, p1.ya], [p1.x, p1.yb], [p0.x, p0.yb]);
          continue;
        }
        // Crossover between p0 and p1: split at the x where the two coordinate lines meet.
        const d0 = p0.ya - p0.yb;
        const d1 = p1.ya - p1.yb;
        const f = d0 / (d0 - d1); // in [0,1]
        const xc = p0.x + f * (p1.x - p0.x);
        const yc = p0.ya + f * (p1.ya - p0.ya); // ya == yb here
        fill(ctx, p0.up ? GREEN : RED, [p0.x, p0.ya], [xc, yc], [p0.x, p0.yb]);
        fill(ctx, p1.up ? GREEN : RED, [xc, yc], [p1.x, p1.ya], [p1.x, p1.yb]);
      }
    });
  }
}

function fill(ctx: CanvasRenderingContext2D, color: string, ...poly: [number, number][]): void {
  ctx.beginPath();
  ctx.moveTo(poly[0][0], poly[0][1]);
  for (let i = 1; i < poly.length; i++) ctx.lineTo(poly[i][0], poly[i][1]);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

class CloudPaneView implements IPrimitivePaneView {
  constructor(private readonly src: IchimokuCloud) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "bottom"; // draw under the Ichimoku lines
  }
  renderer(): IPrimitivePaneRenderer | null {
    const coords = this.src.coords();
    return coords.length > 1 ? new CloudRenderer(coords) : null;
  }
}

export class IchimokuCloud implements ISeriesPrimitive<Time> {
  private data: CloudPoint[] = [];
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private timeScale: ITimeScaleApi<Time> | null = null;
  private requestUpdateFn?: () => void;
  private readonly view = new CloudPaneView(this);

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

  setData(points: CloudPoint[]): void {
    this.data = points;
    this.requestUpdateFn?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  // Recomputed each render so it tracks pan/zoom (timeToCoordinate/priceToCoordinate are viewport-aware).
  coords(): XY[] {
    const ts = this.timeScale;
    const s = this.series;
    if (!ts || !s) return [];
    const out: XY[] = [];
    for (const p of this.data) {
      const x = ts.timeToCoordinate(p.t);
      const ya = s.priceToCoordinate(p.a);
      const yb = s.priceToCoordinate(p.b);
      if (x == null || ya == null || yb == null) continue;
      out.push({ x, ya, yb, up: p.a > p.b });
    }
    return out;
  }
}
