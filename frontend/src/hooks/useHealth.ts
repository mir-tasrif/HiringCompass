import { useCallback, useEffect, useState } from "react";
import { fetchDbHealth, fetchHealth } from "../api/client";

export type Status = "checking" | "up" | "down";

export interface HealthState {
  api: Status;
  db: Status;
  env: string | null;
  checkedAt: Date | null;
  refresh: () => void;
}

// Poll API and database health every `intervalMs`; network failures map to "down".
export function useHealth(intervalMs = 10000): HealthState {
  const [api, setApi] = useState<Status>("checking");
  const [db, setDb] = useState<Status>("checking");
  const [env, setEnv] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  // Run both checks independently so one failure does not hide the other.
  const check = useCallback(async () => {
    try {
      const health = await fetchHealth();
      setApi(health.status === "ok" ? "up" : "down");
      setEnv(health.env);
    } catch {
      setApi("down");
    }
    try {
      setDb((await fetchDbHealth()).db === "ok" ? "up" : "down");
    } catch {
      setDb("down");
    }
    setCheckedAt(new Date());
  }, []);

  useEffect(() => {
    void check();
    const timer = setInterval(() => void check(), intervalMs);
    return () => clearInterval(timer);
  }, [check, intervalMs]);

  return { api, db, env, checkedAt, refresh: () => void check() };
}