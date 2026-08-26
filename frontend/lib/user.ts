/**
 * 已废弃：匿名 localStorage userId 不再作为 API 身份。
 * 会话归属改由 FastAPI 校验 JWT 的 sub（Prisma 用户 id）。
 * 保留此文件只为避免旧 import 在热更新期间炸掉，请不要再引用。
 */
export function getUserId(): string {
  return "";
}
