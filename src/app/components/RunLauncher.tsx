"use client";

// Three ways to run the strategy, spelled out in plain language so nobody has to guess what "sim" vs
// "paper" means. Paper is the only one that touches OANDA, so it asks first (a real dialog, not confirm()).
import { useState } from "react";
import { History, Radio, Banknote } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useChatContext } from "@/providers/ChatProvider";
import { getGateMode, getAgentOverrides } from "@/lib/runPrefs";

const MODES = [
  {
    id: "backtest",
    icon: History,
    title: "Backtest",
    desc: "Replay past prices to see how the strategy would have done. No money, no orders.",
    input: { mode: "backtest" },
  },
  {
    id: "sim",
    icon: Radio,
    title: "Simulated live",
    desc: "Watch real prices as they happen. Trades are simulated here; nothing is sent to OANDA.",
    input: { mode: "live", execution: "simulated", poll: 15 },
  },
  {
    id: "paper",
    icon: Banknote,
    title: "Paper trade on OANDA",
    desc: "Watch real prices and place real orders on your OANDA practice (demo) account.",
    input: { mode: "live", execution: "oanda", poll: 15 },
  },
] as const;

const isoDay = (d: Date) => d.toISOString().slice(0, 10);
const monthsAgo = (n: number) => {
  const d = new Date();
  d.setUTCMonth(d.getUTCMonth() - n);
  return d;
};

export function RunLauncher() {
  const { startRun, isLoading } = useChatContext();
  const [confirmPaper, setConfirmPaper] = useState(false);
  // Backtest window (UTC dates). 6 months by default: shorter windows often contain no setups for a strict strategy.
  const [from, setFrom] = useState(() => isoDay(monthsAgo(6)));
  const [to, setTo] = useState(() => isoDay(new Date()));
  const badRange = !from || !to || from >= to;

  const launch = (input: Record<string, unknown>) =>
    startRun({
      ...input,
      strategy: getGateMode(),
      agents: getAgentOverrides(),
    });

  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m.id}
            disabled={isLoading || (m.id === "backtest" && badRange)}
            onClick={() =>
              m.id === "paper"
                ? setConfirmPaper(true)
                : launch(
                    m.id === "backtest"
                      ? { ...m.input, start: from, end: to }
                      : m.input
                  )
            }
            className={`enabled:hover:border-primary/60 flex items-start gap-3 rounded-lg border bg-card p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
              m.id === "paper" ? "border-warning/40" : "border-border"
            }`}
          >
            <m.icon
              className={`mt-0.5 h-5 w-5 shrink-0 ${
                m.id === "paper" ? "text-warning" : "text-primary"
              }`}
            />
            <span>
              <span className="block text-sm font-medium text-foreground">
                {m.title}
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {m.desc}
              </span>
            </span>
          </button>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>Backtest dates (UTC):</span>
        <label className="flex items-center gap-1">
          From
          <input
            type="date"
            value={from}
            max={to}
            onChange={(e) => setFrom(e.target.value)}
            disabled={isLoading}
            className="rounded-md border border-border bg-background px-2 py-1 text-foreground"
          />
        </label>
        <label className="flex items-center gap-1">
          To
          <input
            type="date"
            value={to}
            min={from}
            max={isoDay(new Date())}
            onChange={(e) => setTo(e.target.value)}
            disabled={isLoading}
            className="rounded-md border border-border bg-background px-2 py-1 text-foreground"
          />
        </label>
        <span>
          {badRange
            ? "Pick a start date before the end date."
            : "Longer ranges take longer to run."}
        </span>
      </div>
      {isLoading && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          A run is in progress. Stop it from the header to start another.
        </p>
      )}

      <Dialog
        open={confirmPaper}
        onOpenChange={setConfirmPaper}
      >
        <DialogContent className="sm:max-w-[460px]">
          <DialogHeader>
            <DialogTitle>
              Place orders on your OANDA practice account?
            </DialogTitle>
            <DialogDescription>
              While this runs, every signal the strategy takes during an enabled
              market session becomes a real order on the practice (101-)
              account. Orders show up in your OANDA history. No real money is
              involved.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmPaper(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                setConfirmPaper(false);
                launch(MODES[2].input);
              }}
            >
              Start paper trading
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
