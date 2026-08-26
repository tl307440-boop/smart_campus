/** 浏览器侧登录态：令牌与用户资料只放本机，不作为 FastAPI 的身份来源。 */

export const TOKEN_STORAGE_KEY = "zhiyue.accessToken";
export const USER_STORAGE_KEY = "zhiyue.authUser";

export type AuthRole = "STUDENT" | "STAFF" | "ADMIN";

export interface AuthUser {
  id: string;
  studentNo: string;
  name: string;
  role: AuthRole;
  tenantSlug: string;
  tenantName: string;
}

export interface AuthSuccess {
  accessToken: string;
  expiresIn: number;
  user: AuthUser;
}

export function readStoredToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
}

export function readStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as AuthUser;
    if (!parsed?.id || !parsed.role) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function persistAuth(payload: AuthSuccess): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, payload.accessToken);
  window.localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(payload.user));
}

export function clearAuth(): void {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(USER_STORAGE_KEY);
}

export function canManageKnowledge(role: AuthRole | undefined): boolean {
  return role === "STAFF" || role === "ADMIN";
}
