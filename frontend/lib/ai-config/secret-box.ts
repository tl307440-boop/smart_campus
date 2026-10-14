/**
 * 学校模型密钥落库前加密。FastAPI 侧 backend/secret_box.py 解密，两端的 salt / info / 格式必须逐字一致：
 *   "v1." + base64url(12 字节 nonce + 密文 + 16 字节 tag)，AES-256-GCM，密钥由 AUTH_JWT_SECRET 经 HKDF-SHA256 派生。
 * 只在服务端（Route Handler）用；换了 AUTH_JWT_SECRET 后旧密文解不开，学校要重填。
 */
import { createCipheriv, hkdfSync, randomBytes } from "node:crypto";

const PREFIX = "v1.";
const SALT = "zhiyue-campus";
const INFO = "tenant-ai-config:v1";
const NONCE_BYTES = 12;

function derivedKey(): Buffer {
  const secret = process.env.AUTH_JWT_SECRET?.trim();
  if (!secret) throw new Error("缺少 AUTH_JWT_SECRET，无法加密模型密钥");
  return Buffer.from(hkdfSync("sha256", secret, SALT, INFO, 32));
}

export function encryptSecret(plain: string): string {
  const nonce = randomBytes(NONCE_BYTES);
  const cipher = createCipheriv("aes-256-gcm", derivedKey(), nonce);
  const body = Buffer.concat([cipher.update(plain, "utf8"), cipher.final(), cipher.getAuthTag()]);
  return PREFIX + Buffer.concat([nonce, body]).toString("base64url");
}

/** 末四位，给管理员认出是哪一把；太短的密钥不露任何字符 */
export function keyHint(plain: string): string {
  return plain.length >= 12 ? plain.slice(-4) : "";
}
