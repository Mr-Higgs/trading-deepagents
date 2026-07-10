"use client";

// One shared OANDA account poller for the whole app, plus the manual trade actions (close / modify SL-TP).
// Previously the header, Account tab, and Chart tab each called useAccount() → 3 concurrent pollers hammering
// the single-worker LangGraph server; this polls once. closeTrade/modifyTrade call the `account_action` write
// graph (tripwire-guarded, practice-only) and refresh() so every surface updates.
import { createContext, useCallback, useContext, useMemo, ReactNode } from "react";
import { toast } from "sonner";
import { useClient } from "@/providers/ClientProvider";
import { useAccount } from "@/app/hooks/useAccount";

type ActionResult = { ok?: boolean; error?: string };
type AccountActions = {
  closeTrade: (tradeId: string, units?: number | "ALL") => Promise<ActionResult>;
  modifyTrade: (tradeId: string, opts: { stop?: number; tp?: number; instrument?: string }) => Promise<ActionResult>;
};
type AccountContextValue = ReturnType<typeof useAccount> & AccountActions;

const AccountContext = createContext<AccountContextValue | null>(null);

export function AccountProvider({ children }: { children: ReactNode }) {
  const client = useClient();
  const account = useAccount();
  const { refresh } = account;

  const run = useCallback(
    async (input: Record<string, unknown>, okMsg: string, failLabel: string): Promise<ActionResult> => {
      try {
        const r = (await client.runs.wait(null, "account_action", { input, signal: AbortSignal.timeout(30_000) })) as ActionResult;
        if (r?.ok) {
          toast.success(okMsg);
          refresh();
        } else {
          toast.error(`${failLabel}: ${r?.error ?? "unknown error"}`);
        }
        return r;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast.error(`${failLabel}: ${msg}`);
        return { ok: false, error: msg };
      }
    },
    [client, refresh]
  );

  const closeTrade = useCallback<AccountActions["closeTrade"]>(
    (tradeId, units) =>
      run(
        { action: "close", trade_id: tradeId, ...(units && units !== "ALL" ? { units: String(units) } : {}) },
        `Closed ${tradeId}${units && units !== "ALL" ? ` (${units}u)` : ""}`,
        "Close failed"
      ),
    [run]
  );

  const modifyTrade = useCallback<AccountActions["modifyTrade"]>(
    (tradeId, { stop, tp, instrument }) =>
      run(
        { action: "modify", trade_id: tradeId, ...(stop != null ? { stop } : {}), ...(tp != null ? { tp } : {}), ...(instrument ? { instrument } : {}) },
        `Updated ${tradeId}`,
        "Update failed"
      ),
    [run]
  );

  const value = useMemo(() => ({ ...account, closeTrade, modifyTrade }), [account, closeTrade, modifyTrade]);
  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

export function useAccountContext(): AccountContextValue {
  const ctx = useContext(AccountContext);
  if (!ctx) throw new Error("useAccountContext must be used within an AccountProvider");
  return ctx;
}
