"use client";

import { ArrowUp, ArrowDown } from "lucide-react";
import type { Signals, SignalState, FiredSignal } from "@/app/hooks/useCandles";

const SIGNAL_LABELS: Record<string, string> = {
  cloud_breakout: "Cloud breakout",
  tk_cross: "Tenkan/Kijun cross",
  price_cross_kijun: "Price crossed Base",
  price_cross_tenkan: "Price crossed Conversion",
  cloud_twist: "Cloud twist",
  chikou_cross: "Chikou cross",
};

function dotClass(v: boolean | null, s?: SignalState["price_vs_cloud"]) {
  const tone = s ? (s === "bullish" ? "bull" : s === "bearish" ? "bear" : "neutral") : v === null ? "neutral" : v ? "bull" : "bear";
  return tone === "bull" ? "bg-success" : tone === "bear" ? "bg-destructive" : "bg-muted-foreground/50";
}

function StateRow({ label, value, cloud }: { label: string; value: boolean | null; cloud?: SignalState["price_vs_cloud"] }) {
  const text = cloud ? cloud : value === null ? "n/a" : value ? "yes" : "no";
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${dotClass(value, cloud)}`} />
        <span className="font-medium capitalize text-foreground">{text}</span>
      </span>
    </div>
  );
}

function SignalRow({ s }: { s: FiredSignal }) {
  const up = s.dir === "up";
  return (
    <div className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-1.5 text-sm">
      <span className="flex items-center gap-2 font-medium text-foreground">
        {up ? <ArrowUp className="h-4 w-4 text-success" /> : <ArrowDown className="h-4 w-4 text-destructive" />}
        {SIGNAL_LABELS[s.name] ?? s.name}
      </span>
      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${s.strength === "strong" ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"}`}>
        {s.strength}
      </span>
    </div>
  );
}

export function SignalsPanel({ signals, instrument, timeframe }: { signals?: Signals; instrument?: string; timeframe?: string }) {
  const st = signals?.state;
  return (
    <div className="flex h-full flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Alerts &amp; Signals {instrument && <span className="font-mono normal-case text-foreground">· {instrument} {timeframe}</span>}
      </div>

      {st ? (
        <>
          <div className="divide-y divide-border">
            <StateRow label="Price vs cloud" value={null} cloud={st.price_vs_cloud} />
            <StateRow label="Tenkan > Kijun" value={st.tenkan_gt_kijun} />
            <StateRow label="Chikou above price (26)" value={st.chikou_above_price} />
            <StateRow label="Price above Base" value={st.price_above_kijun} />
            <StateRow label="Price above Conversion" value={st.price_above_tenkan} />
          </div>

          <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Fired this bar</div>
          {signals && signals.signals.length ? (
            <div className="flex flex-col gap-1.5">
              {signals.signals.map((s, i) => (
                <SignalRow key={`${s.name}-${i}`} s={s} />
              ))}
            </div>
          ) : (
            <div className="rounded-md border border-dashed border-border px-3 py-2 text-sm text-muted-foreground">No crosses on the latest bar.</div>
          )}
        </>
      ) : (
        <div className="p-4 text-center text-sm text-muted-foreground">Loading signals…</div>
      )}
    </div>
  );
}
