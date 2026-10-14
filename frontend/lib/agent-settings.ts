/** 浏览器里的模型配置（自带密钥）。不上传到仓库，也不写进服务器上的 .env。 */

export const SETTINGS_KEY = "zhiyue.llmSettings";
export const MEMORY_KEY = "zhiyue.memories";

export type LlmProvider = "deepseek" | "kimi" | "qwen" | "glm" | "custom";

export interface LlmSettings {
  provider: LlmProvider;
  baseUrl: string;
  apiKey: string;
  model: string;
  stream: boolean;
}

interface MemoryNote {
  id: string;
  content: string;
  createdAt: number;
}

export const PROVIDERS: Record<
  Exclude<LlmProvider, "custom">,
  { label: string; baseUrl: string; model: string }
> = {
  deepseek: {
    label: "Deepseek",
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  },
  kimi: {
    label: "Kimi",
    baseUrl: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k",
  },
  qwen: {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
  },
  glm: {
    label: "智谱 GLM",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    model: "glm-4-flash",
  },
};

const DEFAULT_SETTINGS: LlmSettings = {
  provider: "deepseek",
  baseUrl: PROVIDERS.deepseek.baseUrl,
  apiKey: "",
  model: PROVIDERS.deepseek.model,
  stream: true,
};

function canUseStorage(): boolean {
  return typeof window !== "undefined";
}

export function readSettings(): LlmSettings {
  if (!canUseStorage()) return DEFAULT_SETTINGS;
  try {
    const raw = window.localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<LlmSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: LlmSettings): void {
  window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

/**
 * 旧版把长期记忆存在 localStorage。现在记忆在服务端（backend/memory_store.py），
 * 这里只负责把老数据读出来交给迁移流程，上传成功后再清掉。
 */
function readLegacyMemories(): string[] {
  if (!canUseStorage()) return [];
  try {
    const raw = window.localStorage.getItem(MEMORY_KEY);
    const parsed = raw ? (JSON.parse(raw) as MemoryNote[]) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((note) => (typeof note?.content === "string" ? note.content.trim() : ""))
      .filter(Boolean)
      .slice(0, 80);
  } catch {
    return [];
  }
}

/** 把浏览器里的旧记忆整批迁到服务端；失败就留着，下次再试。 */
export async function migrateLegacyMemories(
  upload: (contents: string[]) => Promise<unknown>,
): Promise<void> {
  const contents = readLegacyMemories();
  if (contents.length === 0) {
    if (canUseStorage()) window.localStorage.removeItem(MEMORY_KEY);
    return;
  }
  await upload(contents);
  window.localStorage.removeItem(MEMORY_KEY);
}
