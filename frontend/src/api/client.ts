// Typed API client: all backend calls go through /api (proxied by Vite in dev, nginx in Docker).

const BASE = "/api";

export interface ServiceHealth {
  status: string;
  service: string;
  env: string;
}

export interface DbHealth {
  db: "ok" | "unavailable";
}

// GET a JSON resource; non-2xx responses with a JSON body are still returned (e.g. 503 db status).
async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  return (await response.json()) as T;
}

// Liveness of the API process.
export const fetchHealth = (): Promise<ServiceHealth> => getJson<ServiceHealth>("/health");

// Readiness of the database connection.
export const fetchDbHealth = (): Promise<DbHealth> => getJson<DbHealth>("/health/db");