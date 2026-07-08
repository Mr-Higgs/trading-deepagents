"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type UTCTimestamp,
  type Time,
  type SeriesMarker,
  type ISeriesMarkersPluginApi,
} from "lightweight-charts";
import type { Candle, Ichimoku } from "@/app/hooks/useCandles";
import type { Trade } from "@/app/utils/reduceEvents";
import { IchimokuCloud, type CloudPoint } from "./ichimokuCloud";
import { TradeZones, type TradeZone } from "./tradeZones";

export type ChartHandle = { chart: IChartApi; candle: ISeriesApi<"Candlestick"> };

type Props = { candles: Candle[]; ichimoku?: Ichimoku; trades: Trade[]; onReady?: (handle: ChartHandle | null) => void };

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
};

export function CandlestickChart({ candles, ichimoku, trades, onReady }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const refs = useRef<Refs | null>(null);
  const priceLines = useRef<IPriceLine[]>([]); // recreated each data update; removed before re-adding
  const onReadyRef = useRef(onReady); // ref so the create-once effect can call the latest without re-running
  onReadyRef.current = onReady;

  // create once
  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9ca3af", attributionLogo: false },
      grid: { vertLines: { color: "rgba(148,163,184,0.12)" }, horzLines: { color: "rgba(148,163,184,0.12)" } },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.25)" },
      timeScale: { borderColor: "rgba(148,163,184,0.25)", timeVisible: true, secondsVisible: false },
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
    const markers = createSeriesMarkers(candle, []);
    chartRef.current = chart;
    refs.current = { candle, tenkan, kijun, senkouA, senkouB, chikou, cloud, zones, markers };
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
    // entry/exit markers (all trades) + entry/SL/TP price lines & risk/reward zones (open trades only)
    for (const pl of priceLines.current) r.candle.removePriceLine(pl);
    priceLines.current = [];
    const zoneData: TradeZone[] = [];
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
      // active trade: annotate live entry / stop / target
      if (t.status === "open") {
        const entry = t.fill_price ?? t.entry_ref;
        const line = (price: number | undefined, color: string, title: string) => {
          if (price == null) return;
          priceLines.current.push(r.candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title }));
        };
        line(entry, "#3b82f6", "Entry");
        line(t.stop, "#ef5350", "SL");
        line(t.tp, "#26a69a", "TP");
        if (entry != null && t.stop != null && t.tp != null && entryT != null) {
          zoneData.push({ tFrom: entryT as UTCTimestamp, tTo: last as UTCTimestamp, entry, stop: t.stop, tp: t.tp });
        }
      }
    }
    r.zones.setData(zoneData);
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    r.markers.setMarkers(ms);
  }, [candles, ichimoku, trades]);

  return <div ref={el} className="h-full w-full" />;
}
