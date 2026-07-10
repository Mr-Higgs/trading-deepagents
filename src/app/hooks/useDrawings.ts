"use client";

import { useCallback, useEffect, useState } from "react";
import type { Drawing } from "@/app/components/drawings";

// ponytail: localStorage keyed per instrument+timeframe (bar times differ across TFs, so drawings must be
// separate). Upgrade path is a backend store if drawings ever need to sync across devices.
const storeKey = (instrument: string, tf: string) => `drawings:${instrument}:${tf}`;

export function useDrawings(instrument?: string, timeframe?: string): [Drawing[], (next: Drawing[]) => void] {
  const [drawings, set] = useState<Drawing[]>([]);

  useEffect(() => {
    if (!instrument || !timeframe) return;
    try {
      const raw = localStorage.getItem(storeKey(instrument, timeframe));
      set(raw ? (JSON.parse(raw) as Drawing[]) : []);
    } catch {
      set([]);
    }
  }, [instrument, timeframe]);

  const update = useCallback(
    (next: Drawing[]) => {
      set(next);
      if (!instrument || !timeframe) return;
      try {
        localStorage.setItem(storeKey(instrument, timeframe), JSON.stringify(next));
      } catch {
        // ignore quota / unavailable
      }
    },
    [instrument, timeframe],
  );

  return [drawings, update];
}
