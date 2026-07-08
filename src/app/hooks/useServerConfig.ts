"use client";

import { useCallback, useState } from "react";
import { useClient } from "@/providers/ClientProvider";

export type RiskConfig = {
  risk_pct: number; // fraction, e.g. 0.01 = 1%
  max_trades_per_day: number;
  max_concurrent_positions: number;
  min_stop_pips: number;
  min_rr: number; // min reward:risk floor (reject below)
  max_daily_loss_pct: number; // fraction
  kill_switch: boolean;
  // trade management
  breakeven_enabled: boolean;
  breakeven_trigger_r: number;
  breakeven_buffer_pips: number;
  trail_mode: string; // "off" | "kijun" | "pips"
  trail_buffer_pips: number; // kijun trail gap (decoupled from breakeven buffer)
  trail_distance_pips: number;
  account_ccy?: string; // read-only
};

export type StrategyConfig = {
  version: string;
  instrument?: string; // read-only
  trend_tf?: string;
  bias_tf?: string;
  entry_tf?: string;
  name: string; // "ichimoku_pullback" | "ichimoku_breakout"
  m15_trigger: string[]; // candlestick trigger pattern(s)
  require_tk_momentum: boolean;
  require_chikou: boolean;
  breakout_ref: string; // "cloud" | "base"
  rr: number;
  stop_rule: string; // "kijun" | "cloud"
  stop_buffer_pips: number;
  h1_pullback_band: number;
  min_trend_distance_pips: number;
  tenkan: number;
  kijun: number;
  senkou_b: number;
  displacement: number;
};

export type SessionsConfig = {
  active: string[]; // which named sessions are enabled
  available: string[]; // all presets defined in sessions.yaml
};

export type ConfigSetResult = {
  ok: boolean;
  errors?: string[];
  risk?: RiskConfig;
  strategy?: StrategyConfig;
  sessions?: SessionsConfig;
};

/** Read/write the server-side risk + strategy + sessions YAML via the `config` LangGraph graph. */
export function useServerConfig() {
  const client = useClient();
  const [risk, setRisk] = useState<RiskConfig | null>(null);
  const [strategy, setStrategy] = useState<StrategyConfig | null>(null);
  const [sessions, setSessions] = useState<SessionsConfig | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = (await client.runs.wait(null, "config", { input: { action: "get" } })) as ConfigSetResult;
      if (res.risk) setRisk(res.risk);
      if (res.strategy) setStrategy(res.strategy);
      if (res.sessions) setSessions(res.sessions);
      return res;
    } finally {
      setLoading(false);
    }
  }, [client]);

  const save = useCallback(
    async (patch: { risk?: Partial<RiskConfig>; strategy?: Partial<StrategyConfig>; sessions?: { active: string[] } }) => {
      const res = (await client.runs.wait(null, "config", { input: { action: "set", ...patch } })) as ConfigSetResult;
      if (res.ok) {
        if (res.risk) setRisk(res.risk);
        if (res.strategy) setStrategy(res.strategy);
        if (res.sessions) setSessions(res.sessions);
      }
      return res;
    },
    [client]
  );

  return { risk, strategy, sessions, loading, load, save };
}
