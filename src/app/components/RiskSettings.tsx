"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useServerConfig } from "@/app/hooks/useServerConfig";
import { LoadFailed } from "@/app/components/LoadFailed";

type Form = {
  risk_pct: string;
  max_trades_per_day: string;
  max_concurrent_positions: string;
  min_stop_pips: string;
  min_rr: string;
  max_daily_loss_pct: string;
  kill_switch: boolean;
  breakeven_enabled: boolean;
  breakeven_trigger_r: string;
  breakeven_buffer_pips: string;
  trail_mode: string;
  trail_buffer_pips: string;
  trail_distance_pips: string;
};

function NumField({
  id,
  label,
  hint,
  value,
  onChange,
  step,
  min,
  max,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  step?: string;
  min?: string;
  max?: string;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>
        {label}{" "}
        {hint && <span className="text-muted-foreground">({hint})</span>}
      </Label>
      <Input
        id={id}
        type="number"
        step={step}
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function RiskSettings() {
  const { load, save } = useServerConfig();
  const [form, setForm] = useState<Form | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchForm = useCallback(() => {
    setLoadErr(null);
    load().then(
      (res) => {
        const r = res.risk;
        if (r)
          setForm({
            risk_pct: String(r.risk_pct * 100),
            max_trades_per_day: String(r.max_trades_per_day),
            max_concurrent_positions: String(r.max_concurrent_positions),
            min_stop_pips: String(r.min_stop_pips),
            min_rr: String(r.min_rr),
            max_daily_loss_pct: String(r.max_daily_loss_pct * 100),
            kill_switch: r.kill_switch,
            breakeven_enabled: r.breakeven_enabled,
            breakeven_trigger_r: String(r.breakeven_trigger_r),
            breakeven_buffer_pips: String(r.breakeven_buffer_pips),
            trail_mode: r.trail_mode,
            trail_buffer_pips: String(r.trail_buffer_pips),
            trail_distance_pips: String(r.trail_distance_pips),
          });
      },
      (e) => setLoadErr(e instanceof Error ? e.message : String(e))
    );
  }, [load]);
  useEffect(fetchForm, [fetchForm]);

  const set = (k: keyof Form, v: string | boolean) =>
    setForm((f) => (f ? { ...f, [k]: v } : f));

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const res = await save({
        risk: {
          risk_pct: Number(form.risk_pct) / 100,
          max_trades_per_day: Number(form.max_trades_per_day),
          max_concurrent_positions: Number(form.max_concurrent_positions),
          min_stop_pips: Number(form.min_stop_pips),
          min_rr: Number(form.min_rr),
          max_daily_loss_pct: Number(form.max_daily_loss_pct) / 100,
          kill_switch: form.kill_switch,
          breakeven_enabled: form.breakeven_enabled,
          breakeven_trigger_r: Number(form.breakeven_trigger_r),
          breakeven_buffer_pips: Number(form.breakeven_buffer_pips),
          trail_mode: form.trail_mode,
          trail_buffer_pips: Number(form.trail_buffer_pips),
          trail_distance_pips: Number(form.trail_distance_pips),
        },
      });
      if (res.ok) {
        toast.success("Risk settings saved. They apply from the next run.");
      } else {
        toast.error((res.errors ?? ["Save failed"]).join("; "));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <h2 className="text-base font-semibold">Risk</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Hard money rules the code always enforces, even when an LLM is in the
        loop. Changes apply from the{" "}
        <span className="font-medium text-foreground">next</span> run.
      </p>
      {loadErr ? (
        <LoadFailed
          error={loadErr}
          onRetry={fetchForm}
        />
      ) : form ? (
        <div className="grid grid-cols-2 gap-4 py-4">
          <NumField
            id="risk_pct"
            label="Risk per trade"
            hint="% equity"
            value={form.risk_pct}
            onChange={(v) => set("risk_pct", v)}
            step="0.1"
            min="0"
            max="5"
          />
          <NumField
            id="max_daily_loss_pct"
            label="Max daily loss"
            hint="% equity"
            value={form.max_daily_loss_pct}
            onChange={(v) => set("max_daily_loss_pct", v)}
            step="0.5"
            min="0"
            max="50"
          />
          <NumField
            id="max_trades_per_day"
            label="Max trades / day"
            value={form.max_trades_per_day}
            onChange={(v) => set("max_trades_per_day", v)}
            step="1"
            min="0"
            max="50"
          />
          <NumField
            id="max_concurrent_positions"
            label="Max concurrent positions"
            value={form.max_concurrent_positions}
            onChange={(v) => set("max_concurrent_positions", v)}
            step="1"
            min="1"
            max="50"
          />
          <NumField
            id="min_stop_pips"
            label="Min stop distance"
            hint="pips"
            value={form.min_stop_pips}
            onChange={(v) => set("min_stop_pips", v)}
            step="1"
            min="0"
            max="200"
          />
          <NumField
            id="min_rr"
            label="Min reward:risk"
            hint="× risk"
            value={form.min_rr}
            onChange={(v) => set("min_rr", v)}
            step="0.5"
            min="0"
            max="10"
          />
          <div className="col-span-2 flex items-center justify-between rounded-md border border-border px-3 py-2">
            <div>
              <Label htmlFor="kill_switch">Kill switch</Label>
              <p className="text-xs text-muted-foreground">
                Block all new entries. Applies to the next run; to stop the
                current run now, press Stop in the header.
              </p>
            </div>
            <Switch
              id="kill_switch"
              checked={form.kill_switch}
              onCheckedChange={(v) => set("kill_switch", v)}
            />
          </div>

          {/* trade management */}
          <div className="col-span-2 border-t border-border pt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Trade management
          </div>
          <div className="col-span-2 flex items-center justify-between rounded-md border border-border px-3 py-2">
            <div>
              <Label htmlFor="breakeven_enabled">Move stop to breakeven</Label>
              <p className="text-xs text-muted-foreground">
                At the trigger, then trail — for risk-free trades
              </p>
            </div>
            <Switch
              id="breakeven_enabled"
              checked={form.breakeven_enabled}
              onCheckedChange={(v) => set("breakeven_enabled", v)}
            />
          </div>
          {form.breakeven_enabled && (
            <>
              <NumField
                id="breakeven_trigger_r"
                label="Breakeven trigger"
                hint="× risk (R)"
                value={form.breakeven_trigger_r}
                onChange={(v) => set("breakeven_trigger_r", v)}
                step="0.1"
                min="0"
                max="10"
              />
              <NumField
                id="breakeven_buffer_pips"
                label="Breakeven buffer"
                hint="pips beyond entry"
                value={form.breakeven_buffer_pips}
                onChange={(v) => set("breakeven_buffer_pips", v)}
                step="0.5"
                min="0"
                max="100"
              />
              <div className="grid gap-1.5">
                <Label htmlFor="trail_mode">Trail mode</Label>
                <Select
                  value={form.trail_mode}
                  onValueChange={(v) => set("trail_mode", v)}
                >
                  <SelectTrigger id="trail_mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="off">Off (breakeven only)</SelectItem>
                    <SelectItem value="kijun">Kijun line</SelectItem>
                    <SelectItem value="pips">Fixed pips</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.trail_mode === "kijun" && (
                <NumField
                  id="trail_buffer_pips"
                  label="Trail buffer"
                  hint="pips off Kijun"
                  value={form.trail_buffer_pips}
                  onChange={(v) => set("trail_buffer_pips", v)}
                  step="1"
                  min="0"
                  max="100"
                />
              )}
              {form.trail_mode === "pips" && (
                <NumField
                  id="trail_distance_pips"
                  label="Trail distance"
                  hint="pips"
                  value={form.trail_distance_pips}
                  onChange={(v) => set("trail_distance_pips", v)}
                  step="1"
                  min="0"
                  max="500"
                />
              )}
            </>
          )}
        </div>
      ) : (
        <div className="py-8 text-center text-sm text-muted-foreground">
          Loading risk settings…
        </div>
      )}
      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={!form || saving}
        >
          {saving ? "Saving…" : "Save risk settings"}
        </Button>
      </div>
    </div>
  );
}
