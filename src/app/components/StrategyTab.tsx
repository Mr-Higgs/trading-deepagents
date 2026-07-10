"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, Lock, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useServerConfig, type StrategyConfig } from "@/app/hooks/useServerConfig";
import { getAgentPrefs, setAgentPrefs, type AgentPrefs, type Authority, type GateMode } from "@/lib/runPrefs";

// selectable Ichimoku strategies (wired to the engine via strategy.name)
const ICHIMOKU = [
  { id: "ichimoku_pullback", name: "Ichimoku Trend-Pullback", desc: "H4 trend → H1 pullback → M15 candlestick trigger (v1.2: +Chikou +momentum)" },
  { id: "ichimoku_breakout", name: "Ichimoku Cloud Breakout", desc: "Setup #2: Tenkan>Kijun + above cloud + Chikou → close breaks the cloud" },
  { id: "ichimoku_confluence", name: "Confluence", desc: "Top-down weighted multi-timeframe vote → pullback/breakout trigger" },
];
// techniques from docs/trading-system/ with no engine strategy behind them yet
const PLANNED = [
  { id: "support_resistance", name: "Support & Resistance", desc: "Key levels, breaks & retests" },
  { id: "price_action", name: "Price Action", desc: "Bare-chart structure" },
];
const PATTERNS = ["engulfing", "pin_bar", "hammer", "shooting_star", "doji"];

type Form = Pick<
  StrategyConfig,
  "version" | "name" | "style" | "rr" | "stop_rule" | "stop_buffer_pips" | "h1_pullback_band" | "min_trend_distance_pips" | "tenkan" | "kijun" | "senkou_b" | "displacement" | "m15_trigger" | "require_tk_momentum" | "require_chikou" | "breakout_ref"
>;

// trading styles → triple-screen stack (mirrors config.py STYLES; shown as a hint next to the picker)
const STYLES = [
  { id: "scalping", name: "Scalping", stack: "M15/M5/M1" },
  { id: "intraday", name: "Intraday", stack: "H1/M15/M5" },
  { id: "swing", name: "Swing", stack: "H4/H1/M15" },
  { id: "position", name: "Position", stack: "D/H4/H1" },
];

function toForm(s: StrategyConfig): Form {
  return {
    version: s.version,
    name: s.name,
    style: s.style ?? "swing",
    rr: s.rr,
    stop_rule: s.stop_rule,
    stop_buffer_pips: s.stop_buffer_pips,
    h1_pullback_band: s.h1_pullback_band,
    min_trend_distance_pips: s.min_trend_distance_pips,
    tenkan: s.tenkan,
    kijun: s.kijun,
    senkou_b: s.senkou_b,
    displacement: s.displacement,
    m15_trigger: s.m15_trigger ?? [],
    require_tk_momentum: s.require_tk_momentum,
    require_chikou: s.require_chikou,
    breakout_ref: s.breakout_ref,
  };
}

function Num({ id, label, value, onChange, step }: { id: string; label: string; value: number | string; onChange: (v: string) => void; step?: string }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs">{label}</Label>
      <Input id={id} type="number" step={step} value={String(value)} onChange={(e) => onChange(e.target.value)} className="h-8" />
    </div>
  );
}

export function StrategyTab() {
  const { strategy, load, save } = useServerConfig();
  const [form, setForm] = useState<Form | null>(null);
  const [prefs, setPrefs] = useState<AgentPrefs>(getAgentPrefs);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void load().then((res) => res.strategy && setForm(toForm(res.strategy)));
    setPrefs(getAgentPrefs());
  }, [load]);

  const mode = prefs.gateMode;
  const patchPrefs = (p: Partial<AgentPrefs>) => {
    setAgentPrefs(p);
    setPrefs((cur) => ({ ...cur, ...p }));
  };

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const num = (k: keyof Form) => (v: string) => set(k, Number(v) as Form[typeof k]);

  const togglePattern = (p: string) =>
    setForm((f) => {
      if (!f) return f;
      const has = f.m15_trigger.includes(p);
      const next = has ? f.m15_trigger.filter((x) => x !== p) : [...f.m15_trigger, p];
      return { ...f, m15_trigger: next.length ? next : f.m15_trigger }; // keep at least one
    });

  const chooseMode = (m: GateMode) => patchPrefs({ gateMode: m });

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const res = await save({ strategy: form });
      if (res.ok) toast.success(`Strategy saved — ${res.strategy?.name} ${res.strategy?.version}`);
      else toast.error((res.errors ?? ["Save failed"]).join("; "));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const isBreakout = form?.name === "ichimoku_breakout";
  const paramLabel = form?.name === "ichimoku_confluence" ? "Confluence" : isBreakout ? "Cloud Breakout" : "Trend-Pullback";

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      {/* selectable Ichimoku strategies */}
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Ichimoku strategy</div>
        <div className="grid gap-2 sm:grid-cols-2">
          {ICHIMOKU.map((t) => {
            const active = form?.name === t.id;
            return (
              <button
                key={t.id}
                onClick={() => set("name", t.id)}
                className={`rounded-lg border p-3 text-left transition ${active ? "border-primary bg-primary/5 ring-1 ring-primary" : "border-border bg-card hover:border-primary/40"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-foreground">{t.name}</span>
                  {active && <Check className="h-4 w-4 text-primary" />}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{t.desc}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* trading style → triple-screen timeframe stack (Elder's three screens) */}
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Trading style — triple screen</div>
        <div className="grid gap-2 sm:grid-cols-4">
          {STYLES.map((t) => {
            const active = form?.style === t.id;
            return (
              <button
                key={t.id}
                onClick={() => set("style", t.id)}
                className={`rounded-lg border p-3 text-left transition ${active ? "border-primary bg-primary/5 ring-1 ring-primary" : "border-border bg-card hover:border-primary/40"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-foreground">{t.name}</span>
                  {active && <Check className="h-4 w-4 text-primary" />}
                </div>
                <p className="mt-1 font-mono text-xs text-muted-foreground">{t.stack}</p>
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">trend → bias → entry. The entry timeframe is the decision clock. Save, then start a run to apply.</p>
      </div>

      {/* config */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{paramLabel} — parameters</span>
          {strategy && <span className="font-mono text-xs text-muted-foreground">{strategy.instrument} · {strategy.trend_tf}/{strategy.bias_tf}/{strategy.entry_tf}</span>}
        </div>

        {form ? (
          <div className="space-y-4 p-4">
            {/* LLM decision layer (applies to backtest AND live/demo runs) */}
            <div className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <Label className="text-xs">LLM decision layer</Label>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">Applies to backtest and live/demo runs.</p>
                </div>
                <div className="inline-flex rounded-md border border-border p-0.5">
                  {(["deterministic", "agent"] as GateMode[]).map((m) => (
                    <button key={m} onClick={() => chooseMode(m)} className={`rounded px-3 py-1 text-xs font-medium ${mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                      {m === "agent" ? "LLM" : "deterministic"}
                    </button>
                  ))}
                </div>
              </div>

              {mode === "agent" ? (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  {/* authority: veto-only gate vs full signal authority */}
                  <div>
                    <Label className="text-xs">Authority</Label>
                    <div className="mt-1 inline-flex rounded-md border border-border p-0.5">
                      {(["gate", "signal"] as Authority[]).map((a) => (
                        <button key={a} onClick={() => patchPrefs({ authority: a })} className={`rounded px-3 py-1 text-xs font-medium ${prefs.authority === a ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                          {a === "signal" ? "Full signal" : "Gate (veto only)"}
                        </button>
                      ))}
                    </div>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {prefs.authority === "signal"
                        ? "Deterministic strategy is the baseline; the LLM may approve, veto, or emit refined direction/entry/stop/TP. Size & hard risk limits stay code-owned."
                        : "The LLM only approves or vetoes the code-computed signal — it never changes prices."}
                    </p>
                  </div>

                  {/* analyst sub-agents */}
                  <div>
                    <Label className="text-xs">Analyst sub-agents</Label>
                    <div className="mt-1 grid gap-2 sm:grid-cols-3">
                      {([["technical", "Technical", "Ichimoku context"], ["fundamental", "Fundamental", "Economic calendar"], ["sentiment", "Sentiment", "CFTC COT positioning"]] as const).map(([key, label, desc]) => (
                        <div key={key} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                          <div>
                            <Label htmlFor={`an-${key}`} className="text-xs">{label}</Label>
                            <p className="text-[11px] text-muted-foreground">{desc}</p>
                          </div>
                          <Switch id={`an-${key}`} checked={prefs[key]} onCheckedChange={(v) => patchPrefs({ [key]: v } as Partial<AgentPrefs>)} />
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* verdict cache (backtest determinism) */}
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                      <div>
                        <Label htmlFor="vc-cache" className="text-xs">Cache verdicts</Label>
                        <p className="text-[11px] text-muted-foreground">Backtest re-runs replay cached decisions (free & deterministic)</p>
                      </div>
                      <Switch id="vc-cache" checked={prefs.cache} onCheckedChange={(v) => patchPrefs({ cache: v })} />
                    </div>
                    <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                      <div>
                        <Label htmlFor="vc-refresh" className="text-xs">Refresh cache</Label>
                        <p className="text-[11px] text-muted-foreground">Ignore cached verdicts, call the LLM fresh (backtest)</p>
                      </div>
                      <Switch id="vc-refresh" checked={prefs.refresh} onCheckedChange={(v) => patchPrefs({ refresh: v })} disabled={!prefs.cache} />
                    </div>
                  </div>
                </div>
              ) : (
                <p className="mt-2 text-[11px] text-muted-foreground">Pure rule engine — no LLM in the loop.</p>
              )}
            </div>

            {/* candlestick trigger patterns */}
            <div>
              <Label className="text-xs">{isBreakout ? "Candlestick patterns (unused by breakout)" : "M15 trigger patterns"}</Label>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {PATTERNS.map((p) => {
                  const on = form.m15_trigger.includes(p);
                  return (
                    <button key={p} onClick={() => togglePattern(p)} className={`rounded-md border px-2.5 py-1 text-xs font-medium capitalize ${on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground"}`}>
                      {p.replace("_", " ")}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* confirmations */}
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                  <Label htmlFor="tk" className="text-xs">Tenkan/Kijun momentum</Label>
                  <p className="text-[11px] text-muted-foreground">Require T/K to agree with direction</p>
                </div>
                <Switch id="tk" checked={form.require_tk_momentum} onCheckedChange={(v) => set("require_tk_momentum", v)} />
              </div>
              <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                  <Label htmlFor="ch" className="text-xs">Chikou confirmation</Label>
                  <p className="text-[11px] text-muted-foreground">Close vs price 26 bars ago</p>
                </div>
                <Switch id="ch" checked={form.require_chikou} onCheckedChange={(v) => set("require_chikou", v)} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Num id="rr" label="Risk:reward" value={form.rr} onChange={num("rr")} step="0.1" />
              <div className="grid gap-1.5">
                <Label htmlFor="stop_rule" className="text-xs">Stop rule</Label>
                <Select value={form.stop_rule} onValueChange={(v) => set("stop_rule", v)}>
                  <SelectTrigger id="stop_rule" className="h-8"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="kijun">Kijun</SelectItem>
                    <SelectItem value="cloud">Cloud</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {isBreakout && (
                <div className="grid gap-1.5">
                  <Label htmlFor="breakout_ref" className="text-xs">Breakout ref</Label>
                  <Select value={form.breakout_ref} onValueChange={(v) => set("breakout_ref", v)}>
                    <SelectTrigger id="breakout_ref" className="h-8"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cloud">Cloud</SelectItem>
                      <SelectItem value="base">Base</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
              <Num id="stop_buffer_pips" label="Stop buffer (pips)" value={form.stop_buffer_pips} onChange={num("stop_buffer_pips")} step="0.5" />
              <Num id="min_trend_distance_pips" label="Min trend dist (pips)" value={form.min_trend_distance_pips} onChange={num("min_trend_distance_pips")} step="1" />
              <Num id="h1_pullback_band" label="H1 pullback band" value={form.h1_pullback_band} onChange={num("h1_pullback_band")} step="0.0001" />
              <Num id="tenkan" label="Tenkan" value={form.tenkan} onChange={num("tenkan")} step="1" />
              <Num id="kijun" label="Kijun" value={form.kijun} onChange={num("kijun")} step="1" />
              <Num id="senkou_b" label="Senkou B" value={form.senkou_b} onChange={num("senkou_b")} step="1" />
              <Num id="displacement" label="Displacement" value={form.displacement} onChange={num("displacement")} step="1" />
              <div className="grid gap-1.5">
                <Label htmlFor="version" className="text-xs">Version tag</Label>
                <Input id="version" value={form.version} onChange={(e) => set("version", e.target.value)} className="h-8 font-mono" />
              </div>
            </div>

            <div className="flex justify-end">
              <Button onClick={handleSave} disabled={saving}>{saving ? "Saving…" : "Save strategy"}</Button>
            </div>
          </div>
        ) : (
          <div className="p-8 text-center text-sm text-muted-foreground">Loading strategy config…</div>
        )}
      </div>

      {/* techniques not yet wired */}
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Layers className="h-3.5 w-3.5" /> Top-Down / Triple Screen (H4/H1/M15) &amp; Confluence are active · other techniques (planned)
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {PLANNED.map((t) => (
            <div key={t.id} className="rounded-lg border border-border bg-card p-3 opacity-70">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-foreground">{t.name}</span>
                <Lock className="h-3 w-3 text-muted-foreground" />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{t.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
