import {
  type AuthSuccess,
  type AuthUser,
  persistAuth,
  readStoredToken,
} from "@/lib/auth/session";

export interface AuthErrorBody {
  code?: string;
  message?: string;
}

export interface LoginInput {
  tenantSlug: string;
  studentNo: string;
  password: string;
}

export interface RegisterInput extends LoginInput {
  name: string;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as AuthErrorBody;
    if (body.message) return body.message;
  } catch {
    // 非 JSON 错误体时退回状态码
  }
  return `请求失败（${response.status}）`;
}

async function postAuth(path: string, body: unknown, expected: number): Promise<AuthSuccess> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status !== expected) {
    throw new Error(await parseError(response));
  }
  const payload = (await response.json()) as AuthSuccess;
  persistAuth(payload);
  return payload;
}

/** 登录：打 Next `/api/auth/login`，由 Prisma 验密、jose 签发 JWT。 */
export function loginRequest(input: LoginInput): Promise<AuthSuccess> {
  return postAuth("/api/auth/login", input, 200);
}

/** 注册：默认学生角色，成功后同样拿到令牌。 */
export function registerRequest(input: RegisterInput): Promise<AuthSuccess> {
  return postAuth("/api/auth/register", input, 201);
}

/** 用现有令牌回查账号是否仍可用（停用/删除时要清本地态）。 */
export async function fetchCurrentUser(): Promise<AuthUser> {
  const token = readStoredToken();
  if (!token) throw new Error("缺少访问令牌");

  const response = await fetch("/api/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const body = (await response.json()) as { user: AuthUser };
  return body.user;
}
