"use client";

import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { useAccountContext } from "@/providers/AccountProvider";
import type { OpenTrade } from "@/app/hooks/useAccount";

const num = (s?: string) => {
  if (s == null || s === "") return undefined;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : undefined;
};

/** Edit SL/TP and close (full or partial) a single open OANDA trade. `trade == null` closes the dialog. */
export function ManageTradeDialog({ trade, onOpenChange }: { trade: OpenTrade | null; onOpenChange: (open: boolean) => void }) {
  const { closeTrade, modifyTrade } = useAccountContext();
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [units, setUnits] = useState("");
  const [busy, setBusy] = useState(false);

  const full = Math.abs(num(trade?.currentUnits) ?? 0);
  const dp = trade?.instrument?.includes("JPY") ? 3 : 5;
  const step = String(1 / 10 ** dp);

  // reset the form whenever a different trade is opened
  useEffect(() => {
    if (!trade) return;
    setSl(trade.stopLossOrder?.price ?? "");
    setTp(trade.takeProfitOrder?.price ?? "");
    setUnits(String(Math.abs(num(trade.currentUnits) ?? 0)));
  }, [trade]);

  if (!trade) return <Dialog open={false} onOpenChange={onOpenChange} />;
  const id = trade.id ?? "";

  const save = async () => {
    setBusy(true);
    await modifyTrade(id, { stop: num(sl), tp: num(tp), instrument: trade.instrument });
    setBusy(false);
    onOpenChange(false);
  };

  const doClose = async () => {
    const n = num(units) ?? full;
    const partial = n > 0 && n < full;
    if (!window.confirm(`Close ${partial ? `${n} of ${full}` : "ALL"} units of ${trade.instrument} (#${id})?`)) return;
    setBusy(true);
    await closeTrade(id, partial ? n : "ALL");
    setBusy(false);
    onOpenChange(false);
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>
            Manage {trade.instrument} <span className="font-mono text-sm text-muted-foreground">#{id}</span>
          </DialogTitle>
          <DialogDescription>
            {(num(trade.currentUnits) ?? 0) < 0 ? "SHORT" : "LONG"} {full.toLocaleString("en-US")} units @ {trade.price} · uPL {trade.unrealizedPL}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="sl">Stop-loss</Label>
              <Input id="sl" type="number" step={step} value={sl} onChange={(e) => setSl(e.target.value)} placeholder="none" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="tp">Take-profit</Label>
              <Input id="tp" type="number" step={step} value={tp} onChange={(e) => setTp(e.target.value)} placeholder="none" />
            </div>
          </div>
          <Button onClick={save} disabled={busy || (num(sl) == null && num(tp) == null)}>
            {busy ? "Saving…" : "Save SL / TP"}
          </Button>

          <div className="border-t border-border pt-3">
            <div className="grid gap-1.5">
              <Label htmlFor="units">Close units (max {full.toLocaleString("en-US")})</Label>
              <div className="flex gap-2">
                <Input id="units" type="number" step="1" min="1" max={full} value={units} onChange={(e) => setUnits(e.target.value)} />
                <Button variant="outline" onClick={() => setUnits(String(full))} disabled={busy}>
                  All
                </Button>
              </div>
            </div>
            <Button variant="destructive" className="mt-3 w-full" onClick={doClose} disabled={busy}>
              {busy ? "Working…" : (num(units) ?? full) >= full ? "Close position" : `Close ${num(units)} units`}
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
