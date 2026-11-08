"use client";

// Everything that configures the bot, in one place: strategy, risk, market hours, server connection.
// The section lives in the URL (?section=) so the header's connection dot can deep-link to it.
import { useQueryState } from "nuqs";
import { StrategyTab } from "@/app/components/StrategyTab";
import { RiskSettings } from "@/app/components/RiskSettings";
import { MarketHoursSettings } from "@/app/components/MarketHoursSettings";
import { ConnectionSettings } from "@/app/components/ConnectionSettings";
import type { StandaloneConfig } from "@/lib/config";

const SECTIONS = [
  { id: "strategy", label: "Strategy", hint: "What the bot trades and how" },
  { id: "risk", label: "Risk", hint: "Position size, daily limits, kill switch" },
  { id: "hours", label: "Market hours", hint: "When the bot may open trades" },
  { id: "connection", label: "Connection", hint: "Trading server address" },
] as const;

export function SettingsTab({ config, onSaveConfig }: { config: StandaloneConfig; onSaveConfig: (c: StandaloneConfig) => void }) {
  const [section, setSection] = useQueryState("section", { defaultValue: "strategy" });

  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto border-b border-border p-2 md:w-56 md:flex-col md:border-b-0 md:border-r">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            onClick={() => void setSection(s.id)}
            aria-current={section === s.id ? "page" : undefined}
            className={`shrink-0 rounded-md px-3 py-2 text-left text-sm ${section === s.id ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"}`}
          >
            {s.label}
            <span className="hidden text-xs font-normal text-muted-foreground md:block">{s.hint}</span>
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {section === "risk" ? (
          <RiskSettings />
        ) : section === "hours" ? (
          <MarketHoursSettings />
        ) : section === "connection" ? (
          <ConnectionSettings initialConfig={config} onSave={onSaveConfig} />
        ) : (
          <StrategyTab />
        )}
      </div>
    </div>
  );
}
