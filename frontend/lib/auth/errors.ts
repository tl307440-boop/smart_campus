import { NextResponse } from "next/server";

/**
 * 认证相关的错误码。前端按 code 分支处理，message 直接展示给用户。
 * 刻意不区分「学号不存在」和「密码错误」，避免account enumeration。
 */
export const AUTH_ERROR = {
  INVALID_INPUT: "INVALID_INPUT",
  INVALID_CREDENTIALS: "INVALID_CREDENTIALS",
  TENANT_NOT_FOUND: "TENANT_NOT_FOUND",
  ACCOUNT_DISABLED: "ACCOUNT_DISABLED",
  ALREADY_EXISTS: "ALREADY_EXISTS",
  UNAUTHORIZED: "UNAUTHORIZED",
  FORBIDDEN: "FORBIDDEN",
  NOT_FOUND: "NOT_FOUND",
  RATE_LIMITED: "RATE_LIMITED",
  SERVER_ERROR: "SERVER_ERROR",
} as const;

export type AuthErrorCode = (typeof AUTH_ERROR)[keyof typeof AUTH_ERROR];

const STATUS_BY_CODE: Record<AuthErrorCode, number> = {
  [AUTH_ERROR.INVALID_INPUT]: 400,
  [AUTH_ERROR.INVALID_CREDENTIALS]: 401,
  [AUTH_ERROR.TENANT_NOT_FOUND]: 404,
  [AUTH_ERROR.ACCOUNT_DISABLED]: 403,
  [AUTH_ERROR.ALREADY_EXISTS]: 409,
  [AUTH_ERROR.UNAUTHORIZED]: 401,
  [AUTH_ERROR.FORBIDDEN]: 403,
  [AUTH_ERROR.NOT_FOUND]: 404,
  [AUTH_ERROR.RATE_LIMITED]: 429,
  [AUTH_ERROR.SERVER_ERROR]: 500,
};

export interface AuthErrorBody {
  code: AuthErrorCode;
  message: string;
}

export function authError(code: AuthErrorCode, message: string): NextResponse<AuthErrorBody> {
  return NextResponse.json({ code, message }, { status: STATUS_BY_CODE[code] });
}
