"use client";

import { AlertTriangle } from "lucide-react";
import { useChatContext } from "@/providers/ChatProvider";
import {
  ResearchControls,
  ResearchStream,
} from "@/app/components/ResearchViews";
import { Term } from "@/app/utils/glossary";

/** Research tab: the strategy-tuning loop (trade_loop mode:"research") on the shared stream. */
export function BacktestLoopTab() {
  const { research, startRun, stopStream, isLoading, error } = useChatContext();
  const active =
    isLoading && (research.meta !== null || research.iterations.length > 0);
  const errMsg = error
    ? error instanceof Error
      ? error.message
      : String(error)
    : null;
  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <p className="max-w-3xl text-sm text-muted-foreground">
        An AI research loop tries one strategy change per iteration on past
        prices. It keeps a change only if it also improves results on a later
        stretch of history it wasn&apos;t tuned on (
        <Term k="oos">out-of-sample</Term>). It runs on a copy of the config, so
        your live settings are never touched.
      </p>
      <ResearchControls
        isLoading={isLoading}
        active={active}
        onStart={(iterations, model) =>
          startRun({ mode: "research", iterations, model })
        }
        onStop={stopStream}
      />
      {errMsg && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">
            The connection to the loop dropped ({errMsg}). Click Run loop to
            start again.
          </span>
        </div>
      )}
      <ResearchStream research={research} />
    </div>
  );
}
