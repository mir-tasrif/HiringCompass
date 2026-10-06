// Typed API client: all backend calls go through /api (proxied by Vite in dev, nginx in Docker).

const BASE = "/api";
const TOKEN_KEY = "hc_token";

// Browser storage for the access token.
export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string): void => localStorage.setItem(TOKEN_KEY, token),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
};

// Error carrying the HTTP status and the server's stable error code.
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

export interface User {
  id: string;
  email: string;
  role: "interviewer" | "candidate";
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface ServiceHealth {
  status: string;
  service: string;
  env: string;
}

export interface DbHealth {
  db: "ok" | "unavailable";
}

// Call the API with the stored token; non-2xx responses throw ApiError, and 401 signals the app to sign out.
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const token = tokenStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && token) window.dispatchEvent(new Event("hc:unauthorized"));
    throw new ApiError(response.status, data.detail ?? "Something went wrong. Please try again.", data.code);
  }
  return data as T;
}

// Exchange credentials for a token.
export const login = (email: string, password: string): Promise<TokenResponse> =>
  apiFetch<TokenResponse>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });

// Fetch the signed-in user (restores a session after a page reload).
export const fetchMe = (): Promise<User> => apiFetch<User>("/auth/me");

// Liveness of the API process.
export const fetchHealth = (): Promise<ServiceHealth> => apiFetch<ServiceHealth>("/health");

// Readiness of the database connection.
export const fetchDbHealth = (): Promise<DbHealth> => apiFetch<DbHealth>("/health/db");