"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type Time,
  type SeriesMarker,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
} from "lightweight-charts";
import type { Candle, Ichimoku } from "@/app/hooks/useCandles";
import type { Trade } from "@/app/utils/reduceEvents";
import { IchimokuCloud, type CloudPoint } from "./ichimokuCloud";
import { TradeZones, type TradeZone } from "./tradeZones";
import { Drawings, DEFAULT_PIP, hitTest, type Drawing, type Tool, type Pt } from "./drawings";
import { PositionControls, type ManagedPosition } from "./positionControls";

export type ChartHandle = { chart: IChartApi; candle: ISeriesApi<"Candlestick"> };

type Props = {
  candles: Candle[];
  ichimoku?: Ichimoku;
  trades: Trade[];
  onReady?: (handle: ChartHandle | null) => void;
  tool?: Tool;
  drawings?: Drawing[];
  onDrawingsChange?: (next: Drawing[]) => void;
  pipSize?: number;
  showIchimoku?: boolean;
  onModifyPosition?: (tradeId: string, kind: "sl" | "tp", price: number) => void;
  onClosePosition?: (tradeId: string) => void;
};

const newId = () => `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

type Refs = {
  candle: ISeriesApi<"Candlestick">;
  tenkan: ISeriesApi<"Line">;
  kijun: ISeriesApi<"Line">;
  senkouA: ISeriesApi<"Line">;
  senkouB: ISeriesApi<"Line">;
  chikou: ISeriesApi<"Line">;
  cloud: IchimokuCloud;
  zones: TradeZones;
  markers: ISeriesMarkersPluginApi<Time>;
  draw: Drawings;
  pos: PositionControls;
};

export function CandlestickChart({ candles, ichimoku, trades, onReady, tool = "cross", drawings, onDrawingsChange, pipSize, showIchimoku = true, onModifyPosition, onClosePosition }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const refs = useRef<Refs | null>(null);
  const onReadyRef = useRef(onReady); // ref so the create-once effect can call the latest without re-running
  onReadyRef.current = onReady;
  // refs so the mount-once drag effect reads the latest handlers without re-subscribing
  const onModifyRef = useRef(onModifyPosition);
  onModifyRef.current = onModifyPosition;
  const onCloseRef = useRef(onClosePosition);
  onCloseRef.current = onClosePosition;
  // refs so the single subscribe effect reads the latest without re-subscribing on every commit
  const toolRef = useRef(tool);
  toolRef.current = tool;
  const drawingsRef = useRef(drawings ?? []);
  drawingsRef.current = drawings ?? [];
  const onChangeRef = useRef(onDrawingsChange);
  onChangeRef.current = onDrawingsChange;
  const draftStart = useRef<Pt | null>(null); // first anchor of a two-click tool

  // create once
  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#787b86", attributionLogo: false },
      grid: { vertLines: { color: "rgba(42,46,57,0.6)" }, horzLines: { color: "rgba(42,46,57,0.6)" } },
      rightPriceScale: { borderColor: "#2a2e39" },
      timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const line = (color: string) => chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const candle = chart.addSeries(CandlestickSeries, { upColor: "#26a69a", downColor: "#ef5350", wickUpColor: "#26a69a", wickDownColor: "#ef5350", borderVisible: false });
    const tenkan = line("#3b82f6"); // blue
    const kijun = line("#ef4444"); // red
    const senkouA = line("#22c55e"); // green (leading span A)
    const senkouB = line("#ef4444"); // red (leading span B)
    const chikou = line("#eab308"); // yellow (lagging span)
    const cloud = new IchimokuCloud();
    candle.attachPrimitive(cloud);
    const zones = new TradeZones();
    candle.attachPrimitive(zones);
    const draw = new Drawings();
    candle.attachPrimitive(draw);
    const pos = new PositionControls();
    candle.attachPrimitive(pos);
    const markers = createSeriesMarkers(candle, []);
    chartRef.current = chart;
    refs.current = { candle, tenkan, kijun, senkouA, senkouB, chikou, cloud, zones, markers, draw, pos };
    onReadyRef.current?.({ chart, candle });
    return () => {
      onReadyRef.current?.(null);
      chart.remove();
      chartRef.current = null;
      refs.current = null;
    };
  }, []);

  // update data
  useEffect(() => {
    const r = refs.current;
    if (!r || !candles.length) return;
    r.candle.setData(candles.map((c) => ({ time: c.t as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c })));

    const lineData = (arr?: (number | null)[]) =>
      (arr ?? [])
        .map((v, i) => (v == null ? null : { time: candles[i].t as UTCTimestamp, value: v }))
        .filter((p): p is { time: UTCTimestamp; value: number } => p !== null);
    r.tenkan.setData(lineData(ichimoku?.tenkan));
    r.kijun.setData(lineData(ichimoku?.kijun));
    r.senkouA.setData(lineData(ichimoku?.senkou_a));
    r.senkouB.setData(lineData(ichimoku?.senkou_b));
    r.chikou.setData(lineData(ichimoku?.chikou));

    const a = ichimoku?.senkou_a;
    const b = ichimoku?.senkou_b;
    const cloudPts: CloudPoint[] = [];
    if (a && b) {
      for (let i = 0; i < candles.length; i++) {
        if (a[i] != null && b[i] != null) cloudPts.push({ t: candles[i].t as UTCTimestamp, a: a[i]!, b: b[i]! });
      }
    }
    r.cloud.setData(cloudPts);

    const first = candles[0].t;
    const last = candles[candles.length - 1].t;
    const ms: SeriesMarker<Time>[] = [];
    // entry/exit markers (all trades) + interactive entry/SL/TP lines & risk/reward zones (open trades only)
    const zoneData: TradeZone[] = [];
    const positions: ManagedPosition[] = [];
    for (const t of trades) {
      const entryT = t.fill_ts ? Math.floor(Date.parse(t.fill_ts) / 1000) : undefined;
      if (entryT != null && entryT >= first && entryT <= last) {
        const long = t.direction !== "SHORT";
        ms.push({ time: entryT as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: long ? "#26a69a" : "#ef5350", shape: long ? "arrowUp" : "arrowDown", text: long ? "B" : "S" });
      }
      const exitT = t.exit_ts ? Math.floor(Date.parse(t.exit_ts) / 1000) : undefined;
      if (exitT != null && exitT >= first && exitT <= last) {
        const pnl = t.pnl;
        const color = pnl == null ? "#9ca3af" : pnl >= 0 ? "#26a69a" : "#ef5350";
        const text = pnl == null ? "×" : `× ${pnl >= 0 ? "+" : "−"}${Math.abs(pnl).toFixed(1)}`;
        ms.push({ time: exitT as UTCTimestamp, position: "inBar", color, shape: "circle", text });
      }
      // active trade: interactive entry / stop / target lines (drag SL/TP, × to close) + R/R zone
      if (t.status === "open") {
        const entry = t.fill_price ?? t.entry_ref;
        if (t.trade_id) positions.push({ tradeId: t.trade_id, instrument: t.instrument, direction: t.direction, units: t.units, entry, stop: t.stop, tp: t.tp });
        if (entry != null && t.stop != null && t.tp != null && entryT != null) {
          zoneData.push({ tFrom: entryT as UTCTimestamp, tTo: last as UTCTimestamp, entry, stop: t.stop, tp: t.tp });
        }
      }
    }
    r.pos.setData(positions);
    r.zones.setData(zoneData);
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    r.markers.setMarkers(ms);
  }, [candles, ichimoku, trades]);

  // push committed drawings + pip size into the primitive
  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    r.draw.setPipSize(pipSize ?? DEFAULT_PIP);
    r.draw.setData(drawings ?? []);
  }, [drawings, pipSize]);

  // toggle the Ichimoku overlay (5 lines + cloud); candles/markers/drawings unaffected
  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    for (const s of [r.tenkan, r.kijun, r.senkouA, r.senkouB, r.chikou]) s.applyOptions({ visible: showIchimoku });
    r.cloud.setVisible(showIchimoku);
  }, [showIchimoku]);

  // reset any in-progress draft when the active tool changes
  useEffect(() => {
    draftStart.current = null;
    refs.current?.draw.setDraft(null);
  }, [tool]);

  // input: place/erase drawings. Subscribed once; reads current tool/drawings from refs.
  useEffect(() => {
    const chart = chartRef.current;
    const r = refs.current;
    if (!chart || !r) return;

    const anchor = (param: MouseEventParams): Pt | null => {
      if (!param.point) return null;
      const time = param.time ?? chart.timeScale().coordinateToTime(param.point.x);
      const price = r.candle.coordinateToPrice(param.point.y);
      if (time == null || price == null) return null; // off the loaded range
      return { time, price };
    };

    const commit = (d: Drawing) => {
      draftStart.current = null;
      r.draw.setDraft(null);
      onChangeRef.current?.([...drawingsRef.current, d]);
    };

    const onClick = (param: MouseEventParams) => {
      const t = toolRef.current;
      if (t === "cross") return;
      if (t === "eraser") {
        if (!param.point) return;
        const id = hitTest(drawingsRef.current, param.point, (p) => r.draw.xy(p), (price) => r.draw.priceToY(price));
        if (id) onChangeRef.current?.(drawingsRef.current.filter((d) => d.id !== id));
        return;
      }
      const pt = anchor(param);
      if (!pt) return;
      if (t === "hline") return commit({ id: newId(), type: "hline", price: pt.price });
      if (t === "text") {
        const text = window.prompt("Text");
        if (text) commit({ id: newId(), type: "text", at: pt, text });
        return;
      }
      // two-click tools: trendline / measure
      if (!draftStart.current) {
        draftStart.current = pt;
      } else {
        const a = draftStart.current;
        commit(t === "trendline" ? { id: newId(), type: "trendline", a, b: pt } : { id: newId(), type: "measure", a, b: pt });
      }
    };

    const onMove = (param: MouseEventParams) => {
      const a = draftStart.current;
      const t = toolRef.current;
      if (!a || (t !== "trendline" && t !== "measure")) return;
      const pt = anchor(param);
      if (!pt) return;
      r.draw.setDraft(t === "trendline" ? { id: "draft", type: "trendline", a, b: pt } : { id: "draft", type: "measure", a, b: pt });
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        draftStart.current = null;
        r.draw.setDraft(null);
      }
    };

    chart.subscribeClick(onClick);
    chart.subscribeCrosshairMove(onMove);
    window.addEventListener("keydown", onKey);
    return () => {
      chart.unsubscribeClick(onClick);
      chart.unsubscribeCrosshairMove(onMove);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // drag SL/TP handles + × to close (open positions). Raw DOM events (lightweight-charts has no drag API);
  // chart pan is frozen while hovering/dragging a handle so a drag doesn't scroll the chart. Only in the
  // default pointer ("cross") tool — a drawing tool takes the pointer instead.
  useEffect(() => {
    const container = el.current;
    const chart = chartRef.current;
    const r = refs.current;
    if (!container || !chart || !r) return;
    let drag: { tradeId: string; kind: "sl" | "tp" } | null = null;
    let frozen = false;
    const freeze = (on: boolean) => {
      if (on === frozen) return;
      frozen = on;
      chart.applyOptions({ handleScroll: !on, handleScale: !on });
    };
    const local = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const onDown = (e: MouseEvent) => {
      if (toolRef.current !== "cross") return;
      const { x, y } = local(e);
      const hit = r.pos.pick(x, y);
      if (!hit) return;
      if (hit.kind === "close") {
        onCloseRef.current?.(hit.tradeId);
        return;
      }
      drag = { tradeId: hit.tradeId, kind: hit.kind };
      freeze(true);
      e.preventDefault();
    };
    const onMove = (e: MouseEvent) => {
      if (drag) {
        const price = r.candle.coordinateToPrice(local(e).y);
        if (price != null) r.pos.setPreview({ tradeId: drag.tradeId, kind: drag.kind, price });
        return;
      }
      if (e.buttons !== 0 || toolRef.current !== "cross") return; // don't interfere with a chart pan / drawing
      const { x, y } = local(e);
      const hit = r.pos.pick(x, y);
      container.style.cursor = hit ? (hit.kind === "close" ? "pointer" : "ns-resize") : "";
      freeze(!!hit); // suspend pan while hovering a handle so the ensuing mousedown-drag won't scroll
    };
    const onUp = (e: MouseEvent) => {
      if (!drag) return;
      const price = r.candle.coordinateToPrice(local(e).y);
      const d = drag;
      drag = null;
      r.pos.setPreview(null);
      freeze(false);
      container.style.cursor = "";
      if (price != null) onModifyRef.current?.(d.tradeId, d.kind, price);
    };
    container.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      container.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  return <div ref={el} className="h-full w-full" style={{ cursor: tool !== "cross" ? "crosshair" : undefined }} />;
}
