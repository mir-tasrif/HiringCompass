import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchMe, login as apiLogin, tokenStore, User } from "../api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

// Holds the signed-in user, restores the session on load, and signs out when the API reports 401.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(tokenStore.get() !== null);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  useEffect(() => {
    if (tokenStore.get()) {
      fetchMe()
        .then(setUser)
        .catch(() => tokenStore.clear())
        .finally(() => setLoading(false));
    }
    window.addEventListener("hc:unauthorized", logout);
    return () => window.removeEventListener("hc:unauthorized", logout);
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await apiLogin(email, password);
    tokenStore.set(result.access_token);
    setUser(result.user);
  }, []);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Access the auth state; must be used inside AuthProvider.
export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}