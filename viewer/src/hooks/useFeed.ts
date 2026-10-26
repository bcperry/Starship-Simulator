import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { clientFeed, type FleetPoint } from "../feed";

export interface FeedData {
  snapshot: ReturnType<typeof clientFeed.getSnapshot>;
  /** fleet series sorted oldest→newest, for charts */
  fleet: FleetPoint[];
  /** live flag: have we seen data recently (< ~2 s)? */
  live: boolean;
  paused: boolean;
  togglePause: () => void;
}

/**
 * Subscribes the component tree to the shared client feed. One EventSource
 * per app; the store coalesces to 10 Hz so this stays cheap at 330 msg/s.
 */
export function useFeed(): FeedData {
  const snapshot = useSyncExternalStore(
    clientFeed.subscribe,
    clientFeed.getSnapshot,
    clientFeed.getSnapshot,
  );

  useEffect(() => {
    clientFeed.connect();
    return () => clientFeed.disconnect();
  }, []);

  // Periodic re-render so time-based indicators (live dot, staleness) stay
  // honest even when the wire is quiet.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(t);
  }, []);

  const live = snapshot.lastEventMs > 0 && Date.now() - snapshot.lastEventMs < 2000;

  const fleet = useMemo(
    () => [...snapshot.fleet].sort((a, b) => a.tsMs - b.tsMs),
    [snapshot.fleet],
  );

  return {
    snapshot,
    fleet,
    live,
    paused: snapshot.paused,
    togglePause: () =>
      snapshot.paused ? clientFeed.resume() : clientFeed.pause(),
  };
}

export function useClock(): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return Date.now();
}
