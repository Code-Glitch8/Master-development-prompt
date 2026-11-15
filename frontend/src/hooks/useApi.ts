import { useCallback, useEffect, useRef, useState } from "react";
import { live } from "../services/ws";

/** Fetch data and keep it fresh: re-fetches when a matching live event arrives,
 *  when the WebSocket reconnects, or when `reload` is called. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = [], liveTypes: string[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);

  const reload = useCallback(async (silent = false) => {
    const my = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const d = await fnRef.current();
      if (my === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (my === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (liveTypes.length === 0) return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const off = live.on((e) => {
      if (e.type === "reconnected" || liveTypes.includes(e.type)) {
        if (t) clearTimeout(t);
        t = setTimeout(() => void reload(true), 250);
      }
    });
    return () => { off(); if (t) clearTimeout(t); };
  }, [reload, liveTypes.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  return { data, error, loading, reload, setData };
}
