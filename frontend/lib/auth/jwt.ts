import { SignJWT, jwtVerify } from "jose";

/**
 * 令牌由 Next 侧签发，FastAPI 侧用同一个密钥验签（PyJWT / HS256）。
 * 两边必须共用 AUTH_JWT_SECRET，改动 claim 名字要同步改 backend/auth.py。
 */
export const JWT_ISSUER = "zhiyue-campus";
export const JWT_ALGORITHM = "HS256";
export const ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 12;

export const USER_ROLE = {
  STUDENT: "STUDENT",
  STAFF: "STAFF",
  ADMIN: "ADMIN",
} as const;

export type UserRole = (typeof USER_ROLE)[keyof typeof USER_ROLE];

export interface AccessTokenClaims {
  /** 用户 id，同时是长期记忆的归属键 */
  sub: string;
  /** 租户（学校）id */
  tid: string;
  /** 学号 */
  sno: string;
  name: string;
  role: UserRole;
}

function getSecret(): Uint8Array {
  const secret = process.env.AUTH_JWT_SECRET;
  if (!secret) {
    throw new Error("缺少 AUTH_JWT_SECRET，无法签发令牌");
  }
  return new TextEncoder().encode(secret);
}

export async function signAccessToken(claims: AccessTokenClaims): Promise<string> {
  return new SignJWT({ ...claims })
    .setProtectedHeader({ alg: JWT_ALGORITHM })
    .setIssuer(JWT_ISSUER)
    .setSubject(claims.sub)
    .setIssuedAt()
    .setExpirationTime(`${ACCESS_TOKEN_TTL_SECONDS}s`)
    .sign(getSecret());
}

/** 嵌入挂件访客令牌的有效期。短一些：令牌暴露在第三方网页里 */
export const GUEST_TOKEN_TTL_SECONDS = 60 * 60 * 2;

/** 访客角色只在 FastAPI 侧认，isAccessTokenClaims 会拒绝它，访客因此进不了 Next 的任何接口 */
export const GUEST_ROLE = "GUEST";

export interface GuestTokenClaims {
  sub: string;
  tid: string;
  /** 挂件 id，FastAPI 用它回查启用状态与每日上限（backend/auth.py） */
  eid: string;
}

export async function signGuestToken(claims: GuestTokenClaims): Promise<string> {
  return new SignJWT({ ...claims, sno: "guest", name: "访客", role: GUEST_ROLE })
    .setProtectedHeader({ alg: JWT_ALGORITHM })
    .setIssuer(JWT_ISSUER)
    .setSubject(claims.sub)
    .setIssuedAt()
    .setExpirationTime(`${GUEST_TOKEN_TTL_SECONDS}s`)
    .sign(getSecret());
}

export async function verifyAccessToken(token: string): Promise<AccessTokenClaims | null> {
  try {
    const { payload } = await jwtVerify(token, getSecret(), {
      issuer: JWT_ISSUER,
      algorithms: [JWT_ALGORITHM],
    });
    if (!isAccessTokenClaims(payload)) return null;
    return payload;
  } catch {
    // 过期、签名不符、格式错误都归为「没有有效身份」
    return null;
  }
}

function isAccessTokenClaims(value: unknown): value is AccessTokenClaims {
  if (typeof value !== "object" || value === null) return false;
  const claims = value as Record<string, unknown>;
  return (
    typeof claims.sub === "string" &&
    typeof claims.tid === "string" &&
    typeof claims.sno === "string" &&
    typeof claims.name === "string" &&
    typeof claims.role === "string" &&
    Object.values(USER_ROLE).includes(claims.role as UserRole)
  );
}
