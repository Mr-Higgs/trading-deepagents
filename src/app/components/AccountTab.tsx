"use client";

import { useState } from "react";
import { RefreshCw, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type AccountSummary, type OpenTrade } from "@/app/hooks/useAccount";
import { useAccountContext } from "@/providers/AccountProvider";
import { ManageTradeDialog } from "@/app/components/ManageTradeDialog";

const num = (s?: string | number) => (s === undefined || s === null ? NaN : typeof s === "number" ? s : parseFloat(s));
const money = (s?: string | number, ccy = "USD") => {
  const n = num(s);
  return Number.isNaN(n) ? "—" : `${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy}`;
};
const signed = (s?: string | number) => {
  const n = num(s);
  return Number.isNaN(n) ? "—" : `${n >= 0 ? "+" : "−"}${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
const plColor = (s?: string | number) => {
  const n = num(s);
  return Number.isNaN(n) || n === 0 ? "text-muted-foreground" : n > 0 ? "text-success" : "text-destructive";
};

function Tile({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold tabular-nums ${valueClass ?? "text-foreground"}`}>{value}</div>
    </div>
  );
}

function SummaryTiles({ s }: { s: AccountSummary }) {
  const ccy = s.currency ?? "USD";
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <Tile label="NAV" value={money(s.NAV, ccy)} />
      <Tile label="Balance" value={money(s.balance, ccy)} />
      <Tile label="Unrealized P/L" value={signed(s.unrealizedPL)} valueClass={plColor(s.unrealizedPL)} />
      <Tile label="Realized P/L" value={signed(s.pl)} valueClass={plColor(s.pl)} />
      <Tile label="Margin used" value={money(s.marginUsed, ccy)} />
      <Tile label="Margin available" value={money(s.marginAvailable, ccy)} />
      <Tile label="Open trades" value={String(s.openTradeCount ?? 0)} />
      <Tile label="Open positions" value={String(s.openPositionCount ?? 0)} />
    </div>
  );
}

export function AccountTab() {
  const { data, loading, error, refresh } = useAccountContext();
  const s = data?.summary;
  const [manage, setManage] = useState<OpenTrade | null>(null);

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          Practice account <span className="font-mono text-foreground">{data?.account ?? "…"}</span>
        </div>
        <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {!s && !error && <div className="p-6 text-center text-sm text-muted-foreground">Loading account…</div>}

      {s && (
        <>
          <SummaryTiles s={s} />

          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Open positions ({data?.positions?.length ?? 0})
            </div>
            {data?.positions?.length ? (
              <div className="overflow-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
                      <th>Instrument</th><th>Long units</th><th>Short units</th><th className="text-right">Unrealized P/L</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono tabular-nums [&>tr]:border-t [&>tr]:border-border">
                    {data.positions.map((p, i) => (
                      <tr key={`${p.instrument}-${i}`} className="[&>td]:px-3 [&>td]:py-2">
                        <td>{p.instrument}</td>
                        <td>{p.long?.units ?? "0"}</td>
                        <td>{p.short?.units ?? "0"}</td>
                        <td className={`text-right ${plColor(p.unrealizedPL)}`}>{signed(p.unrealizedPL)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-4 text-sm text-muted-foreground">No open positions.</div>
            )}
          </div>

          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Open trades ({data?.trades?.length ?? 0})
            </div>
            {data?.trades?.length ? (
              <div className="overflow-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
                      <th>ID</th><th>Instrument</th><th>Units</th><th>Entry</th><th className="text-right">Unrealized P/L</th><th>Opened</th><th className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono tabular-nums [&>tr]:border-t [&>tr]:border-border">
                    {data.trades.map((t) => (
                      <tr key={t.id} className="[&>td]:px-3 [&>td]:py-2">
                        <td>{t.id}</td>
                        <td>{t.instrument}</td>
                        <td>{t.currentUnits}</td>
                        <td>{t.price}</td>
                        <td className={`text-right ${plColor(t.unrealizedPL)}`}>{signed(t.unrealizedPL)}</td>
                        <td className="text-muted-foreground">{t.openTime ? new Date(t.openTime).toISOString().slice(0, 16).replace("T", " ") : "—"}</td>
                        <td className="text-right">
                          <Button size="sm" variant="outline" onClick={() => setManage(t)}>Manage</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-4 text-sm text-muted-foreground">No open trades.</div>
            )}
          </div>

          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Closed positions ({data?.closed_trades?.length ?? 0})
            </div>
            {data?.closed_trades?.length ? (
              <div className="overflow-auto">
                <table className="w-full min-w-[680px] text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
                      <th>ID</th><th>Instrument</th><th>Units</th><th>Entry</th><th>Close</th><th className="text-right">Realized P/L</th><th>Closed</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono tabular-nums [&>tr]:border-t [&>tr]:border-border">
                    {data.closed_trades.map((t) => (
                      <tr key={t.id} className="[&>td]:px-3 [&>td]:py-2">
                        <td>{t.id}</td>
                        <td>{t.instrument}</td>
                        <td>{t.initialUnits}</td>
                        <td>{t.price}</td>
                        <td>{t.averageClosePrice}</td>
                        <td className={`text-right ${plColor(t.realizedPL)}`}>{signed(t.realizedPL)}</td>
                        <td className="text-muted-foreground">{t.closeTime ? new Date(t.closeTime).toISOString().slice(0, 16).replace("T", " ") : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-4 text-sm text-muted-foreground">No closed positions.</div>
            )}
          </div>
        </>
      )}

      <ManageTradeDialog trade={manage} onOpenChange={(o) => !o && setManage(null)} />
    </div>
  );
}
