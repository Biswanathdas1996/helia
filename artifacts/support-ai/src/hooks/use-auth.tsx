import { createContext, useContext, useEffect, useState, useCallback } from "react";
import type { ReactNode } from "react";
import type { Me } from "@workspace/api-client-react";

interface AuthState {
  user: Me | null;
  loading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<Me>;
  register: (email: string, password: string, firstName?: string, lastName?: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<Me | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error ?? `Request failed (${res.status})`);
  }
  return res;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async (): Promise<Me | null> => {
    try {
      const res = await apiFetch("/api/me");
      const me = await res.json() as Me;
      setUser(me);
      return me;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (email: string, password: string): Promise<Me> => {
    await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    const me = await refresh();
    if (!me) {
      throw new Error("Login succeeded but profile could not be loaded");
    }
    return me;
  }, [refresh]);

  const register = useCallback(async (
    email: string,
    password: string,
    firstName?: string,
    lastName?: string,
  ): Promise<Me> => {
    await apiFetch("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, firstName, lastName }),
    });
    const me = await refresh();
    if (!me) {
      throw new Error("Registration succeeded but profile could not be loaded");
    }
    return me;
  }, [refresh]);

  const logout = useCallback(async () => {
    await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
