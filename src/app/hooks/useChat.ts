"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type Message,
  type Assistant,
  type Checkpoint,
} from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import type { UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { TodoItem } from "@/app/types/types";
import { useClient } from "@/providers/ClientProvider";
import { useQueryState } from "nuqs";
import { reduceEvents, type RunMeta, type TradingEvent } from "@/app/utils/reduceEvents";
import { reduceResearch, type ResearchEvent } from "@/app/utils/reduceResearch";

export type StateType = {
  messages: Message[];
  todos: TodoItem[];
  files: Record<string, string>;
  email?: {
    id?: string;
    subject?: string;
    page_content?: string;
  };
  ui?: any;
};

export function useChat({
  activeAssistant,
  onHistoryRevalidate,
  thread,
}: {
  activeAssistant: Assistant | null;
  onHistoryRevalidate?: () => void;
  thread?: UseStreamThread<StateType>;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const client = useClient();

  // Trading events arrive on the LangGraph `custom` stream (trade_loop graph), not in graph state —
  // accumulate them client-side. SKIP is dropped: it's ~95% of the volume (insufficient_history /
  // session_closed) and noise for the dashboard. ponytail: no skip rows in the feed; add a toggle if needed.
  const [events, setEvents] = useState<TradingEvent[]>([]);
  const [runMeta, setRunMeta] = useState<RunMeta | null>(null);
  // Timestamp of the most recent event of ANY type (including dropped SKIP heartbeats), so a healthy live
  // run that's just between signals reads as "watching · last check HH:MM:SS" instead of a frozen feed.
  const [lastCheckAt, setLastCheckAt] = useState<string | null>(null);
  // startRun defers its submit here while a stale URL threadId is being cleared (see startRun + effect).
  const [pendingRun, setPendingRun] = useState<Record<string, unknown> | null>(null);
  // Mirrors stream.isLoading so onError (which can't read `stream` while it's being constructed) can tell a
  // mid-run drop from a load-time stale-thread 404 — we only reset the threadId in the latter case.
  const loadingRef = useRef(false);

  // Only hand useStream a threadId we've verified exists. The dev thread store is wiped on server restart,
  // so a URL threadId can be stale — and the SDK's history fetch for a missing thread rejects a promise it
  // fire-and-forgets, surfacing as an unhandled-rejection overlay in Next dev (which ignores preventDefault).
  // Verifying before streaming means that fetch never happens for a dead id.
  const [streamThreadId, setStreamThreadId] = useState<string | null>(null);
  // A thread useStream just auto-created is live — promote it to URL + stream without re-verifying.
  const handleThreadCreated = useCallback(
    (id: string) => {
      setStreamThreadId(id);
      setThreadId(id);
    },
    [setThreadId]
  );

  const stream = useStream<StateType>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    reconnectOnMount: true,
    threadId: streamThreadId,
    onThreadId: handleThreadCreated,
    defaultHeaders: { "x-auth-scheme": "langsmith" },
    // Enable fetching state history when switching to existing threads
    fetchStateHistory: true,
    // Revalidate thread list when stream finishes, errors, or creates new thread
    onFinish: onHistoryRevalidate,
    onError: (err: unknown) => {
      // The dev thread store is ephemeral (cleared on server restart / expiry). A URL threadId that
      // no longer exists 404s on load — drop it so the app resets to a fresh thread, not a crash. But
      // NOT mid-run: a drop during an active run must not abandon the (still-executing) background run's
      // thread — leave it so reconnectOnMount can rejoin, and let the UI surface stream.error instead.
      const msg = err instanceof Error ? err.message : String(err);
      if (/not found|404/i.test(msg) && !loadingRef.current) setThreadId(null);
      onHistoryRevalidate?.();
    },
    onCreated: onHistoryRevalidate,
    onCustomEvent: (event: unknown) => {
      const e = event as TradingEvent;
      if (!e || typeof e.type !== "string" || !("run_id" in (e as object))) return;
      if (e.type === "RUN_META") {
        setRunMeta(e as unknown as RunMeta); // run header (execution backend/account) — not a feed event
        return;
      }
      if (e.ts) setLastCheckAt(e.ts); // heartbeat: bump on every event, incl. dropped SKIPs
      if (e.type !== "SKIP") setEvents((prev) => [...prev, e]);
    },
    experimental_thread: thread,
  });

  useEffect(() => {
    loadingRef.current = stream.isLoading;
  }, [stream.isLoading]);

  // Gate the URL threadId into the stream: verify it exists first; drop a stale one (server restart) before
  // its history fetch can reject. A freshly created thread (threadId === streamThreadId) is already live.
  useEffect(() => {
    if (!threadId) {
      setStreamThreadId(null);
      return;
    }
    if (threadId === streamThreadId) return;
    let live = true;
    client.threads.get(threadId).then(
      () => {
        if (live) setStreamThreadId(threadId);
      },
      () => {
        if (live) {
          setStreamThreadId(null);
          setThreadId(null); // stale id gone → reset the URL to a fresh thread
        }
      }
    );
    return () => {
      live = false;
    };
  }, [threadId, streamThreadId, client, setThreadId]);

  const reduced = useMemo(() => reduceEvents(events), [events]);
  // The backtest iteration loop rides the SAME custom stream (trade_loop mode:"research") — fold its
  // RESEARCH_META/ITER_*/RESEARCH_END events into a live iteration list for the Live + Backtest views.
  const research = useMemo(() => reduceResearch(events as unknown as ResearchEvent[]), [events]);

  // Kick off a trading run on the trade_loop graph, e.g. startRun({ mode: "backtest" }) or { mode: "live" }.
  // A dashboard run is a one-shot execution, not a conversation continuation: never submit onto the
  // current URL threadId, because the dev server's thread store is wiped on restart/expiry and a stale
  // threadId 404s the run ("Thread or assistant not found"). Reset to a fresh thread first — if a
  // threadId is set, clear it and defer the submit to the effect below; useStream auto-creates a new
  // thread when threadId is null.
  const startRun = useCallback(
    (input: Record<string, unknown> = {}) => {
      setEvents([]);
      setRunMeta(null);
      setLastCheckAt(null);
      if (threadId) setThreadId(null); // clear the current thread; the verify effect nulls streamThreadId
      setPendingRun(input); // submit once we're on a fresh (null) thread — see effect below
    },
    [threadId, setThreadId]
  );

  // Deferred submit: fire only once BOTH the URL threadId and the verified streamThreadId are null, so the
  // run always lands on a fresh auto-created thread (never a stale dev-store id that 404s). useStream calls
  // onThreadId with the new id, repopulating both; pendingRun is cleared so that can't re-trigger a submit.
  useEffect(() => {
    if (!pendingRun || threadId || streamThreadId) return;
    stream.submit(pendingRun as Partial<StateType>, {
      config: { ...(activeAssistant?.config ?? {}), recursion_limit: 100 },
    });
    setPendingRun(null);
    onHistoryRevalidate?.();
  }, [pendingRun, threadId, streamThreadId, stream, activeAssistant?.config, onHistoryRevalidate]);

  const sendMessage = useCallback(
    (content: string) => {
      const newMessage: Message = { id: uuidv4(), type: "human", content };
      stream.submit(
        { messages: [newMessage] },
        {
          optimisticValues: (prev) => ({
            messages: [...(prev.messages ?? []), newMessage],
          }),
          config: { ...(activeAssistant?.config ?? {}), recursion_limit: 100 },
        }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
  );

  const runSingleStep = useCallback(
    (
      messages: Message[],
      checkpoint?: Checkpoint,
      isRerunningSubagent?: boolean,
      optimisticMessages?: Message[]
    ) => {
      if (checkpoint) {
        stream.submit(undefined, {
          ...(optimisticMessages
            ? { optimisticValues: { messages: optimisticMessages } }
            : {}),
          config: activeAssistant?.config,
          checkpoint: checkpoint,
          ...(isRerunningSubagent
            ? { interruptAfter: ["tools"] }
            : { interruptBefore: ["tools"] }),
        });
      } else {
        stream.submit(
          { messages },
          { config: activeAssistant?.config, interruptBefore: ["tools"] }
        );
      }
    },
    [stream, activeAssistant?.config]
  );

  const setFiles = useCallback(
    async (files: Record<string, string>) => {
      if (!threadId) return;
      // TODO: missing a way how to revalidate the internal state
      // I think we do want to have the ability to externally manage the state
      await client.threads.updateState(threadId, { values: { files } });
    },
    [client, threadId]
  );

  const continueStream = useCallback(
    (hasTaskToolCall?: boolean) => {
      stream.submit(undefined, {
        config: {
          ...(activeAssistant?.config || {}),
          recursion_limit: 100,
        },
        ...(hasTaskToolCall
          ? { interruptAfter: ["tools"] }
          : { interruptBefore: ["tools"] }),
      });
      // Update thread list when continuing stream
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
  );

  const markCurrentThreadAsResolved = useCallback(() => {
    stream.submit(null, { command: { goto: "__end__", update: null } });
    // Update thread list when marking thread as resolved
    onHistoryRevalidate?.();
  }, [stream, onHistoryRevalidate]);

  const resumeInterrupt = useCallback(
    (value: any) => {
      stream.submit(null, { command: { resume: value } });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, onHistoryRevalidate]
  );

  const stopStream = useCallback(() => {
    stream.stop();
  }, [stream]);

  return {
    stream,
    todos: stream.values.todos ?? [],
    files: stream.values.files ?? {},
    email: stream.values.email,
    ui: stream.values.ui,
    setFiles,
    messages: stream.messages,
    isLoading: stream.isLoading,
    isThreadLoading: stream.isThreadLoading,
    error: stream.error, // surfaced on the dashboard so a dropped/failed run is visible, not a silent freeze

    interrupt: stream.interrupt,
    getMessagesMetadata: stream.getMessagesMetadata,
    sendMessage,
    runSingleStep,
    continueStream,
    stopStream,
    markCurrentThreadAsResolved,
    resumeInterrupt,
    // trading dashboard
    events,
    runMeta,
    lastCheckAt,
    startRun,
    trades: reduced.trades,
    equityCurve: reduced.equityCurve,
    pnl: reduced.pnl,
    openPositions: reduced.openPositions,
    // backtest iteration loop (trade_loop mode:"research", shares this stream)
    research,
  };
}
