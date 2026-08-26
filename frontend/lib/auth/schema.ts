/**
 * 请求体校验。项目没引 zod，这里手写守卫，规则集中在一处便于前后端对齐。
 */

/** 学号只允许字母数字，长度 4-32 */
const STUDENT_NO_PATTERN = /^[A-Za-z0-9]{4,32}$/;
/** 租户 slug 与 Redis key、URL 都有关系，收紧到小写字母数字和连字符 */
const TENANT_SLUG_PATTERN = /^[a-z0-9-]{2,32}$/;

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
export const NAME_MAX_LENGTH = 32;

export interface LoginInput {
  tenantSlug: string;
  studentNo: string;
  password: string;
}

export interface RegisterInput extends LoginInput {
  name: string;
}

export interface ValidationFailure {
  ok: false;
  message: string;
}

export interface ValidationSuccess<T> {
  ok: true;
  value: T;
}

export type ValidationResult<T> = ValidationSuccess<T> | ValidationFailure;

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === "string" ? value.trim() : "";
}

export function parseLoginInput(body: unknown): ValidationResult<LoginInput> {
  if (typeof body !== "object" || body === null) {
    return { ok: false, message: "请求体格式不正确" };
  }
  const source = body as Record<string, unknown>;
  const tenantSlug = readString(source, "tenantSlug").toLowerCase();
  const studentNo = readString(source, "studentNo");
  const password = readString(source, "password");

  if (!TENANT_SLUG_PATTERN.test(tenantSlug)) {
    return { ok: false, message: "学校标识不合法" };
  }
  if (!STUDENT_NO_PATTERN.test(studentNo)) {
    return { ok: false, message: "学号应为 4-32 位字母或数字" };
  }
  if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    return { ok: false, message: `密码长度应在 ${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 之间` };
  }
  return { ok: true, value: { tenantSlug, studentNo, password } };
}

export function parseRegisterInput(body: unknown): ValidationResult<RegisterInput> {
  const base = parseLoginInput(body);
  if (!base.ok) return base;

  const source = body as Record<string, unknown>;
  const name = readString(source, "name");
  if (!name || name.length > NAME_MAX_LENGTH) {
    return { ok: false, message: `姓名不能为空，且不超过 ${NAME_MAX_LENGTH} 字` };
  }
  return { ok: true, value: { ...base.value, name } };
}
