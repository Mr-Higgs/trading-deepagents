"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useServerConfig } from "@/app/hooks/useServerConfig";
import { LoadFailed } from "@/app/components/LoadFailed";

const pretty = (name: string) =>
  name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function MarketHoursSettings() {
  const { load, save } = useServerConfig();
  const [available, setAvailable] = useState<string[] | null>(null);
  const [active, setActive] = useState<string[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchSessions = useCallback(() => {
    setLoadErr(null);
    load().then(
      (res) => {
        if (res.sessions) {
          setAvailable(res.sessions.available);
          setActive(res.sessions.active);
        }
      },
      (e) => setLoadErr(e instanceof Error ? e.message : String(e))
    );
  }, [load]);
  useEffect(fetchSessions, [fetchSessions]);

  const toggle = (name: string) =>
    setActive((a) =>
      a.includes(name) ? a.filter((n) => n !== name) : [...a, name]
    );

  const handleSave = async () => {
    if (!active.length) {
      toast.error("Select at least one market session");
      return;
    }
    setSaving(true);
    try {
      const res = await save({ sessions: { active } });
      if (res.ok) {
        toast.success("Market hours saved. They apply from the next run.");
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
      <h2 className="text-base font-semibold">Market hours</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The bot only opens trades while at least one of the selected market
        sessions is open. Changes apply from the next run. The exact hours of
        each session are set in{" "}
        <span className="font-mono">config/sessions.yaml</span>.
      </p>
      {loadErr ? (
        <LoadFailed
          error={loadErr}
          onRetry={fetchSessions}
        />
      ) : available ? (
        <div className="flex flex-wrap gap-2 py-4">
          {available.map((name) => {
            const on = active.includes(name);
            return (
              <Button
                key={name}
                type="button"
                variant={on ? "default" : "outline"}
                size="sm"
                aria-pressed={on}
                onClick={() => toggle(name)}
              >
                {pretty(name)}
              </Button>
            );
          })}
        </div>
      ) : (
        <div className="py-8 text-center text-sm text-muted-foreground">
          Loading market hours…
        </div>
      )}
      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={!available || !active.length || saving}
        >
          {saving ? "Saving…" : "Save market hours"}
        </Button>
      </div>
    </div>
  );
}
