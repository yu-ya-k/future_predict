/**
 * Independent 1s client-side clock (ui_plan.md A6, invariant I-2).
 *
 * Decoupled from polling so the elapsed/remaining time keeps ticking smoothly
 * even while a long `waiting_deep_research` poll is in flight — the job never
 * looks frozen.
 */

import { useEffect, useState } from "react";

/** Minutes elapsed since `since`, updated every second while `active`. */
export function useElapsed(since: string | Date | undefined, active = true): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  if (!since) return 0;
  const start = typeof since === "string" ? Date.parse(since) : since.getTime();
  if (Number.isNaN(start)) return 0;
  return Math.max(0, (now - start) / 60_000);
}

/** Format minutes as `MM:SS`. */
export function formatElapsed(minutes: number): string {
  const totalSeconds = Math.floor(minutes * 60);
  const mm = Math.floor(totalSeconds / 60);
  const ss = totalSeconds % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}
