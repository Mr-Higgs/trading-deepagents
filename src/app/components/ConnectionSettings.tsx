"use client";

import { useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { DEFAULT_CONFIG, testConnection, type StandaloneConfig } from "@/lib/config";

/** Where the trading-agent server lives. Used inline in Settings and on the first-run screen. */
export function ConnectionSettings({ initialConfig, onSave }: { initialConfig?: StandaloneConfig | null; onSave: (config: StandaloneConfig) => void }) {
  const [deploymentUrl, setDeploymentUrl] = useState(initialConfig?.deploymentUrl || DEFAULT_CONFIG.deploymentUrl);
  const [assistantId, setAssistantId] = useState(initialConfig?.assistantId || DEFAULT_CONFIG.assistantId);
  const [langsmithApiKey, setLangsmithApiKey] = useState(initialConfig?.langsmithApiKey || "");
  const [status, setStatus] = useState<{ state: "idle" | "testing" | "ok" | "error"; msg?: string }>({ state: "idle" });

  const cfg = (): StandaloneConfig => ({ deploymentUrl: deploymentUrl.trim(), assistantId: assistantId.trim(), langsmithApiKey: langsmithApiKey || undefined });
  const valid = !!deploymentUrl.trim() && !!assistantId.trim();

  const test = async () => {
    setStatus({ state: "testing" });
    const err = await testConnection(cfg());
    setStatus(err ? { state: "error", msg: err } : { state: "ok", msg: "Connected. The trading server is reachable." });
    return !err;
  };

  return (
    <div className="max-w-2xl">
      <h2 className="text-base font-semibold">Connection</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Where the trading server runs. Start it with <span className="font-mono">uv run langgraph dev</span> in <span className="font-mono">trading-agent/</span>. The defaults work for a local setup.
      </p>
      <div className="grid gap-4 py-4">
        <div className="grid gap-2">
          <Label htmlFor="deploymentUrl">Server URL</Label>
          <Input id="deploymentUrl" value={deploymentUrl} onChange={(e) => setDeploymentUrl(e.target.value)} />
        </div>
        <details className="rounded-md border border-border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-muted-foreground">Advanced</summary>
          <div className="mt-3 grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="assistantId">Graph ID</Label>
              <Input id="assistantId" value={assistantId} onChange={(e) => setAssistantId(e.target.value)} />
              <p className="text-xs text-muted-foreground">Leave as <span className="font-mono">trade_loop</span> unless you renamed the graph.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="langsmithApiKey">LangSmith API key (only for a hosted deployment)</Label>
              <Input id="langsmithApiKey" type="password" placeholder="lsv2_pt_..." value={langsmithApiKey} onChange={(e) => setLangsmithApiKey(e.target.value)} />
            </div>
          </div>
        </details>
        {status.state !== "idle" && (
          <div
            role="status"
            className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${status.state === "ok" ? "border-success/40 text-success" : status.state === "error" ? "border-destructive/40 text-destructive" : "border-border text-muted-foreground"}`}
          >
            {status.state === "testing" ? <Loader2 className="mt-0.5 h-4 w-4 animate-spin" /> : status.state === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4" /> : <AlertTriangle className="mt-0.5 h-4 w-4" />}
            <span className="min-w-0">{status.state === "testing" ? "Testing connection…" : status.msg}</span>
          </div>
        )}
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => void test()} disabled={!valid || status.state === "testing"}>
          Test connection
        </Button>
        <Button onClick={() => onSave(cfg())} disabled={!valid}>
          Save &amp; connect
        </Button>
      </div>
    </div>
  );
}
