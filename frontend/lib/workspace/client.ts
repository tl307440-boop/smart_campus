/** 管理员面板调用 Next 侧的管理接口（同源 /api/admin/*），带上登录令牌。 */
import type { AiConfigInput, AiConfigView } from "@/lib/ai-config/schema";
import { readStoredToken } from "@/lib/auth/session";
import type { EmbedInput, WorkspaceSettingsInput } from "@/lib/workspace/schema";

export type WorkspaceSettings = WorkspaceSettingsInput;

export interface EmbedConfig extends EmbedInput {
  id: string;
  createdAt: string;
}

async function adminRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readStoredToken();
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  const body = (await response.json().catch(() => ({}))) as { message?: string };
  if (!response.ok) {
    throw new Error(body.message || `请求失败（${response.status}）`);
  }
  return body as T;
}

export function fetchWorkspaceSettings() {
  return adminRequest<WorkspaceSettings>("/api/admin/workspace");
}

export function saveWorkspaceSettings(settings: WorkspaceSettings) {
  return adminRequest<WorkspaceSettings>("/api/admin/workspace", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export function fetchAiConfig() {
  return adminRequest<AiConfigView>("/api/admin/ai");
}

export function saveAiConfig(input: AiConfigInput) {
  return adminRequest<AiConfigView>("/api/admin/ai", { method: "PUT", body: JSON.stringify(input) });
}

export async function fetchEmbeds(): Promise<EmbedConfig[]> {
  return (await adminRequest<{ embeds: EmbedConfig[] }>("/api/admin/embeds")).embeds;
}

export async function createEmbed(input: EmbedInput): Promise<EmbedConfig> {
  return (
    await adminRequest<{ embed: EmbedConfig }>("/api/admin/embeds", {
      method: "POST",
      body: JSON.stringify(input),
    })
  ).embed;
}

export async function updateEmbed(id: string, input: EmbedInput): Promise<EmbedConfig> {
  return (
    await adminRequest<{ embed: EmbedConfig }>(`/api/admin/embeds/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  ).embed;
}

export function deleteEmbed(id: string) {
  return adminRequest<{ status: string }>(`/api/admin/embeds/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** 学校官网要粘贴的那一行代码 */
export function embedSnippet(embedId: string): string {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `<script src="${origin}/embed.js" data-embed-id="${embedId}" defer></script>`;
}
