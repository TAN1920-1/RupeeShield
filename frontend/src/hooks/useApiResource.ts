/**
 * Minimal read-only data hook.
 *
 * Every page follows the same pattern: call `useApiResource` with a fetcher and a
 * dependency list, then render `loading` / `error` / `data`. Keeping this in one
 * place means no page has to hand-roll try/catch/setState, and the "unknown is not
 * zero" rule survives because a failed request yields `data === null` rather than
 * a fabricated empty object.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface ApiResource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useApiResource<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // The fetcher is a fresh closure on every render; keep the latest one in a ref
  // so re-fetching is driven by `deps` and not by render churn.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce, ...deps]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, loading, error, reload };
}

/**
 * Re-run `callback` on an interval. Used by the live views that poll rather than
 * stream, so a page left open does not hammer the API at an unbounded rate.
 */
export function useInterval(callback: () => void, delayMs: number | null): void {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (delayMs === null) return;
    const handle = window.setInterval(() => callbackRef.current(), delayMs);
    return () => window.clearInterval(handle);
  }, [delayMs]);
}
