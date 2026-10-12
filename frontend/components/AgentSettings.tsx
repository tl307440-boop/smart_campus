"use client";

import { useCallback, useEffect, useState } from "react";
import {
  PROVIDERS,
  type LlmProvider,
  type LlmSettings,
  readSettings,
  saveSettings,
} from "@/lib/agent-settings";
import { type MemoryNote, addMemories, deleteMemory, fetchMemories, pingLlm } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function AgentSettings({ open, onClose }: Props) {
  const [settings, setSettings] = useState<LlmSettings>(readSettings);
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const reloadMemories = useCallback(async () => {
    try {
      setNotes(await fetchMemories());
    } catch (err) {
      setMemoryNotice(err instanceof Error ? err.message : "读取记忆失败");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchMemories()
      .then((list) => {
        if (!cancelled) setNotes(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setMemoryNotice(err instanceof Error ? err.message : "读取记忆失败");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  function update<K extends keyof LlmSettings>(key: K, value: LlmSettings[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }

  function chooseProvider(provider: LlmProvider) {
    if (provider === "custom") {
      setSettings((prev) => ({ ...prev, provider }));
      return;
    }
    const preset = PROVIDERS[provider];
    setSettings((prev) => ({
      ...prev,
      provider,
      baseUrl: preset.baseUrl,
      model: preset.model,
    }));
  }

  function handleSave() {
    saveSettings(settings);
    setNotice("已保存在这台浏览器里");
  }

  async function handlePing() {
    setChecking(true);
    setNotice(null);
    try {
      const result = await pingLlm(settings);
      setNotice(result.models.length ? `连通，可用模型 ${result.models.slice(0, 3).join("、")}` : "连通");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "连通失败");
    } finally {
      setChecking(false);
    }
  }

  async function handleRemember() {
    const content = draft.trim();
    if (!content) return;
    setMemoryNotice(null);
    try {
      await addMemories([content]);
      setDraft("");
      await reloadMemories();
    } catch (err) {
      setMemoryNotice(err instanceof Error ? err.message : "保存记忆失败");
    }
  }

  async function handleForget(id: string) {
    setMemoryNotice(null);
    try {
      await deleteMemory(id);
      await reloadMemories();
    } catch (err) {
      setMemoryNotice(err instanceof Error ? err.message : "删除记忆失败");
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 px-4 py-8">
      <div className="w-full max-w-3xl rounded-2xl border border-[#f0c9cf] bg-[#fff7f8] p-6 text-[#5c3a42] shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[#c45b73]">AI 服务设置</h2>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleSave}
              className="rounded-full border border-[#e7b7c2] px-4 py-1 text-sm text-[#c45b73]"
            >
              保存
            </button>
            <button type="button" onClick={onClose} className="rounded-full px-3 py-1 text-sm">
              关闭
            </button>
          </div>
        </div>

        <section className="mt-5 rounded-xl border border-[#f3d5db] bg-white p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[#c45b73]">LLM API 配置</h3>
            <button
              type="button"
              disabled={checking || !settings.apiKey}
              onClick={handlePing}
              className="rounded-full border border-[#e7b7c2] px-3 py-1 text-xs disabled:opacity-40"
            >
              {checking ? "检测中…" : "连通检测"}
            </button>
          </div>

          <label className="mt-4 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>服务商</span>
            <select
              value={settings.provider}
              onChange={(event) => chooseProvider(event.target.value as LlmProvider)}
              className="rounded-lg border border-[#f0d0d6] bg-white px-3 py-2"
            >
              <option value="deepseek">Deepseek</option>
              <option value="kimi">Kimi</option>
              <option value="custom">自定义</option>
            </select>
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>LLM API 基础网址</span>
            <input
              value={settings.baseUrl}
              onChange={(event) => update("baseUrl", event.target.value)}
              className="rounded-lg border border-[#f0d0d6] px-3 py-2"
            />
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>LLM API Key</span>
            <input
              type="password"
              value={settings.apiKey}
              onChange={(event) => update("apiKey", event.target.value)}
              autoComplete="off"
              className="rounded-lg border border-[#f0d0d6] px-3 py-2"
            />
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>模型 ID</span>
            <input
              value={settings.model}
              onChange={(event) => update("model", event.target.value)}
              className="rounded-lg border border-[#f0d0d6] px-3 py-2"
            />
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>流式响应</span>
            <input
              type="checkbox"
              checked={settings.stream}
              onChange={(event) => update("stream", event.target.checked)}
              className="size-4 accent-[#c45b73]"
            />
          </label>
          {notice && <p className="mt-3 text-sm text-[#8a5a66]">{notice}</p>}
        </section>

        <section className="mt-4 rounded-xl border border-[#f3d5db] bg-white p-4">
          <h3 className="text-sm font-semibold text-[#c45b73]">长期记忆</h3>
          <p className="mt-1 text-xs text-[#8a6a72]">
            保存在服务器上，换设备登录也在。对话里说「记住：……」会记在这里，聊天中提到的个人信息（如学院、专业）也会被自动记下。回答前会把相关条目交给模型。
          </p>
          <div className="mt-3 flex gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="例如：我是计算机学院的"
              className="flex-1 rounded-lg border border-[#f0d0d6] px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={() => void handleRemember()}
              className="rounded-full border border-[#e7b7c2] px-4 text-sm text-[#c45b73]"
            >
              记住
            </button>
          </div>
          <ul className="mt-3 space-y-2">
            {notes.map((note) => (
              <li key={note.id} className="flex items-start justify-between gap-3 text-sm">
                <span>
                  {note.content}
                  {note.origin === "auto" && (
                    <span className="ml-2 text-xs text-[#b08a93]">自动记录</span>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => void handleForget(note.id)}
                  className="shrink-0 text-xs text-[#c45b73]"
                >
                  忘记
                </button>
              </li>
            ))}
            {notes.length === 0 && <li className="text-xs text-[#8a6a72]">还没有长期记忆。</li>}
          </ul>
          {memoryNotice && <p className="mt-3 text-sm text-[#8a5a66]">{memoryNotice}</p>}
        </section>
      </div>
    </div>
  );
}
