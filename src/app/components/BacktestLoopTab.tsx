"use client";

import { AlertTriangle } from "lucide-react";
import { useChatContext } from "@/providers/ChatProvider";
import { ResearchControls, ResearchStream } from "@/app/components/ResearchViews";

/**
 * Full-height view of the backtest iteration loop. Reads the SHARED trade_loop stream (via
 * useChatContext) — the loop runs as `startRun({ mode: "research" })`, so launching it here or from
 * the Live tab drives the same run. The Live tab shows a compact inline version of the same stream.
 */
export function BacktestLoopTab() {
  const { research, startRun, stopStream, isLoading, error } = useChatContext();
  const active = isLoading && (research.meta !== null || research.iterations.length > 0);
  const errMsg = error ? (error instanceof Error ? error.message : String(error)) : null;
  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <ResearchControls
        isLoading={isLoading}
        active={active}
        onStart={(iterations, model) => startRun({ mode: "research", iterations, model })}
        onStop={stopStream}
      />
      <div className="ml-1 flex items-center gap-2 text-sm text-muted-foreground">
        <span className={`inline-block h-2 w-2 rounded-full ${errMsg ? "bg-destructive" : isLoading ? "animate-pulse bg-success" : "bg-muted-foreground/40"}`} />
        {errMsg ? "disconnected" : isLoading ? "streaming" : "idle"} · {research.iterations.length} iterations
      </div>
      {errMsg && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">Stream disconnected — {errMsg}. Click Run loop to reconnect.</span>
        </div>
      )}
      <ResearchStream research={research} />
    </div>
  );
}
