"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  fetchCurrentUser,
  loginRequest,
  registerRequest,
  type LoginInput,
  type RegisterInput,
} from "@/lib/auth/client";
import {
  type AuthUser,
  clearAuth,
  persistAuth,
  readStoredToken,
  readStoredUser,
} from "@/lib/auth/session";
import { forgetThreadId } from "@/lib/thread-storage";

interface AuthContextValue {
  user: AuthUser | null;
  ready: boolean;
  login: (input: LoginInput) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    // 整段放进异步函数：直接在 effect 体里同步 setState 会触发级联渲染
    async function restore(): Promise<void> {
      const token = readStoredToken();
      const cached = readStoredUser();
      if (!token) {
        if (!cancelled) setReady(true);
        return;
      }

      // 先画出缓存用户，再回查 /me；回查失败则清掉过期令牌
      if (cached && !cancelled) setUser(cached);
      try {
        const next = await fetchCurrentUser();
        if (cancelled) return;
        setUser(next);
        persistAuth({ accessToken: token, expiresIn: 0, user: next });
      } catch {
        if (cancelled) return;
        clearAuth();
        setUser(null);
      } finally {
        if (!cancelled) setReady(true);
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (input: LoginInput) => {
    const payload = await loginRequest(input);
    setUser(payload.user);
  }, []);

  const register = useCallback(async (input: RegisterInput) => {
    const payload = await registerRequest(input);
    setUser(payload.user);
  }, []);

  const logout = useCallback(() => {
    // 先忘会话编号：它按账号存，清掉登录态之后就找不到是哪个账号的了
    forgetThreadId();
    clearAuth();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, ready, login, register, logout }),
    [user, ready, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须包在 AuthProvider 内");
  return ctx;
}
