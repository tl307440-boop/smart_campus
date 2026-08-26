import { hash, verify } from "@node-rs/argon2";

// OWASP 推荐的 Argon2id 参数档位之一（19 MiB 内存 / 2 次迭代 / 1 并行度）。
// 调低会削弱抗暴力破解能力，调高会拖慢登录响应。
const ARGON2_OPTIONS = {
  memoryCost: 19456,
  timeCost: 2,
  parallelism: 1,
} as const;

/** 登录失败时也要跑一次校验，避免用「响应快慢」探测出账号是否存在。 */
const DUMMY_HASH =
  "$argon2id$v=19$m=19456,t=2,p=1$c21hcnRjYW1wdXNkdW1teQ$Rd1Y3jVBiPPYVsBpNRRYQXQrOZTMPFqmjLBLQZWZKvo";

export function hashPassword(plain: string): Promise<string> {
  return hash(plain, ARGON2_OPTIONS);
}

export async function verifyPassword(plain: string, hashed: string): Promise<boolean> {
  try {
    return await verify(hashed, plain);
  } catch {
    // 散列串损坏或格式不认识时，一律当作验证失败，不要把异常抛给调用方
    return false;
  }
}

/** 账号不存在时调用，制造与真实校验相当的耗时。 */
export async function burnTiming(plain: string): Promise<void> {
  await verifyPassword(plain, DUMMY_HASH);
}
