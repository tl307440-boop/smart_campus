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
import { PaneTitle } from "@/components/ToolDrawer";

/** 抽屉页「AI 服务」：自带密钥只存在这台浏览器的 localStorage 里。 */
export function ServicePane() {
  const [settings, setSettings] = useState<LlmSettings>(readSettings);
  const [notice, setNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  function update<K extends keyof LlmSettings>(key: K, value: LlmSettings[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }

  function chooseProvider(provider: LlmProvider) {
    if (provider === "custom") {
      setSettings((prev) => ({ ...prev, provider }));
      return;
    }
    const preset = PROVIDERS[provider];
    setSettings((prev) => ({ ...prev, provider, baseUrl: preset.baseUrl, model: preset.model }));
  }

  function handleSave() {
    saveSettings(settings);
    setNotice(settings.apiKey ? "已保存在这台浏览器里，之后的提问用你的密钥" : "已保存：没填密钥，继续用学校的服务");
  }

  async function handlePing() {
    setChecking(true);
    setNotice(null);
    try {
      const result = await pingLlm(settings);
      setNotice(result.models.length ? `连通，可用模型 ${result.models.slice(0, 3).join("、")}` : "连通");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "";
      setNotice(`连不上，检查网址和密钥是否填对。${detail ? `（${detail.slice(0, 120)}）` : ""}`);
    } finally {
      setChecking(false);
    }
  }

  return (
    <section>
      <PaneTitle title="AI 服务">
        不填也能用：默认走学校配置的服务。想用自己的模型，就在这里填密钥，只存在这台浏览器里。
      </PaneTitle>

      <div className="space-y-4">
        <label className="block">
          <span className="field-label">服务商</span>
          <select
            value={settings.provider}
            onChange={(event) => chooseProvider(event.target.value as LlmProvider)}
            className="field"
          >
            <option value="deepseek">Deepseek</option>
            <option value="kimi">Kimi</option>
            <option value="custom">自定义</option>
          </select>
        </label>
        <label className="block">
          <span className="field-label">接口网址（Base URL）</span>
          <input value={settings.baseUrl} onChange={(event) => update("baseUrl", event.target.value)} className="field" />
        </label>
        <label className="block">
          <span className="field-label">API 密钥</span>
          <input
            type="password"
            value={settings.apiKey}
            onChange={(event) => update("apiKey", event.target.value)}
            autoComplete="off"
            placeholder="sk-…"
            className="field"
          />
        </label>
        <label className="block">
          <span className="field-label">模型 ID</span>
          <input value={settings.model} onChange={(event) => update("model", event.target.value)} className="field" />
        </label>
        <label className="flex items-center gap-2.5 text-[0.9375rem]">
          <input
            type="checkbox"
            checked={settings.stream}
            onChange={(event) => update("stream", event.target.checked)}
            className="size-4 accent-pink"
          />
          边想边写（流式输出）
        </label>
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        <button type="button" onClick={handleSave} className="btn btn-primary">
          保存
        </button>
        <button type="button" disabled={checking || !settings.apiKey} onClick={() => void handlePing()} className="btn">
          {checking ? "检测中…" : "试着连一下"}
        </button>
      </div>
      {notice && <p className="mt-3 text-[0.9375rem] break-all text-muted" role="status">{notice}</p>}
    </section>
  );
}

/** 抽屉页「记忆」：长期记忆在服务端，换设备登录也在。 */
export function MemoryPane() {
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setNotes(await fetchMemories());
    } catch {
      setNotice("记忆没读出来，稍后再打开看看");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchMemories()
      .then((list) => {
        if (!cancelled) setNotes(list);
      })
      .catch(() => {
        if (!cancelled) setNotice("记忆没读出来，稍后再打开看看");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRemember() {
    const content = draft.trim();
    if (!content) return;
    setNotice(null);
    try {
      await addMemories([content]);
      setDraft("");
      await reload();
    } catch {
      setNotice("没记住，稍后再试");
    }
  }

  async function handleForget(id: string) {
    setNotice(null);
    try {
      await deleteMemory(id);
      await reload();
    } catch {
      setNotice("没删掉，稍后再试");
    }
  }

  return (
    <section>
      <PaneTitle title="记忆">
        告诉我的事（比如学院、专业）会记在这里，换设备登录也在。聊天时说「记住：……」也能记下。
      </PaneTitle>

      <form
        className="flex items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void handleRemember();
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="要记住的事"
          placeholder="例如：我是计算机学院大二的"
          className="field flex-1"
        />
        <button type="submit" disabled={!draft.trim()} className="btn">
          记住
        </button>
      </form>

      <ul className="mt-5 divide-y divide-dashed divide-rule">
        {notes.map((note) => (
          <li key={note.id} className="flex items-start justify-between gap-3 py-2.5">
            <span className="min-w-0 break-words">
              {note.content}
              {note.origin === "auto" && <span className="ml-2 text-sm text-muted">自动记下</span>}
            </span>
            <button type="button" onClick={() => void handleForget(note.id)} className="link-ink shrink-0 text-sm">
              忘掉
            </button>
          </li>
        ))}
        {notes.length === 0 && <li className="py-2.5 text-muted">还没有记住什么。</li>}
      </ul>
      {notice && <p className="mt-3 text-[0.9375rem] text-danger" role="status">{notice}</p>}
    </section>
  );
}
