import { useCallback, useEffect, useRef, useState } from 'react';

/*
 * Data loading with explicit loading / empty / error states.
 *
 * Every screen in the workspace uses this so the four states the UI has to
 * handle are handled the same way everywhere, rather than each screen inventing
 * its own flags.
 */
export function useAsync(fn, deps = [], { immediate = true, pollMs = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(immediate);
  const [refreshing, setRefreshing] = useState(false);
  const mounted = useRef(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(
    async ({ silent = false } = {}) => {
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        const result = await fnRef.current();
        if (!mounted.current) return null;
        setData(result);
        setError(null);
        return result;
      } catch (err) {
        if (!mounted.current) return null;
        // A background refresh that fails should not blank a screen that is
        // already showing good data.
        if (!silent) setError(err);
        return null;
      } finally {
        if (mounted.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    []
  );

  useEffect(() => {
    if (immediate) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!pollMs) return undefined;
    const id = setInterval(() => run({ silent: true }), pollMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMs, ...deps]);

  return { data, error, loading, refreshing, reload: run, setData };
}

/**
 * One-shot actions (start, pause, upload) with their own pending/error state,
 * kept separate from the screen's data state so a failed action does not
 * replace the content with an error page.
 */
export function useAction(fn) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const execute = useCallback(
    async (...args) => {
      setPending(true);
      setError(null);
      try {
        return await fn(...args);
      } catch (err) {
        setError(err);
        return null;
      } finally {
        setPending(false);
      }
    },
    [fn]
  );

  return { execute, pending, error, clearError: () => setError(null) };
}
