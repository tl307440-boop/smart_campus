"use client";

import { useEffect, useState } from "react";
import { PROVIDERS } from "@/lib/agent-settings";
import type { AiConfigView, AiEndpointInput, AiService } from "@/lib/ai-config/schema";
import { type AiServiceName, type AiStatus, errorDetail, fetchAiStatus, testAiService } from "@/lib/api";
import { fetchAiConfig, saveAiConfig } from "@/lib/workspace/client";

/** 与 backend/ai_config.py 的默认值一致：留空时后端用的就是这些 */
const DEFAULTS: Record<AiService, { baseUrl: string; model: string }> = {
  chat: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  reader: { baseUrl: "https://api.moonshot.cn/v1", model: "kimi-k2-0905-preview" },
};

const ROWS: { service: AiServiceName; name: string; use: string }[] = [
  { service: "chat", name: "对话模型", use: "判断问题、回答、情绪分析" },
  { service: "reader", name: "长文档精读", use: "资料不多时整篇读，摘出相关段落" },
  { service: "embedding", name: "知识库向量化", use: "资料多时按片段检索（平台统一提供）" },
];

const SOURCE_TEXT = { school: "学校配置", platform: "平台默认" } as const;

type Drafts = Record<AiService, AiEndpointInput>;
type TestState = { pending: boolean; ok?: boolean; text?: string };

function toDrafts(view: AiConfigView): Drafts {
  const draft = (service: AiService) => ({
    baseUrl: view[service].baseUrl,
    model: view[service].model,
    apiKey: "",
    clearKey: false,
  });
  return { chat: draft("chat"), reader: draft("reader") };
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`sketch grid size-6 shrink-0 place-items-center rounded-full font-display text-sm leading-none ${
        ok ? "bg-paper text-ink" : "bg-skin text-pink"
      }`}
    >
      {ok ? "✓" : "!"}
    </span>
  );
}

function EndpointForm({
  service,
  view,
  draft,
  onChange,
}: {
  service: AiService;
  view: AiConfigView[AiService];
  draft: AiEndpointInput;
  onChange: (patch: Partial<AiEndpointInput>) => void;
}) {
  const fallback = DEFAULTS[service];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="常用服务商，点一下填好网址和模型">
        {Object.values(PROVIDERS).map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() =>
              onChange({
                baseUrl: preset.baseUrl,
                model: service === "reader" && preset.label === "Kimi" ? fallback.model : preset.model,
              })
            }
            className="btn btn-sm"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <label className="block">
        <span className="field-label">接口网址</span>
        <input
          value={draft.baseUrl}
          onChange={(event) => onChange({ baseUrl: event.target.value })}
          placeholder={`留空用 ${fallback.baseUrl}`}
          className="field"
        />
      </label>
      <label className="block">
        <span className="field-label">模型 ID</span>
        <input
          value={draft.model}
          onChange={(event) => onChange({ model: event.target.value })}
          placeholder={`留空用 ${fallback.model}`}
          className="field"
        />
      </label>
      <label className="block">
        <span className="field-label">API 密钥</span>
        <input
          type="password"
          value={draft.apiKey}
          onChange={(event) => onChange({ apiKey: event.target.value, clearKey: false })}
          autoComplete="off"
          placeholder={view.hasKey ? `已保存 ···${view.keyHint || "****"}，留空不改` : "sk-…"}
          className="field"
        />
      </label>
      {view.hasKey && (
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={draft.clearKey}
            onChange={(event) => onChange({ clearKey: event.target.checked, apiKey: "" })}
            className="size-4 accent-pink"
          />
          清除已保存的密钥（改用平台默认）
        </label>
      )}
    </div>
  );
}

/**
 * 「AI 服务」页里学校那一块：所有人都能看到三项服务配没配；管理员还能改学校自己的模型并测连通。
 * 密钥只进不出，读回时只有末四位。
 */
export function SchoolAiSettings({ isAdmin }: { isAdmin: boolean }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [view, setView] = useState<AiConfigView | null>(null);
  const [drafts, setDrafts] = useState<Drafts | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [tests, setTests] = useState<Partial<Record<AiServiceName, TestState>>>({});

  useEffect(() => {
    let cancelled = false;
    fetchAiStatus()
      .then((next) => !cancelled && setStatus(next))
      .catch(() => !cancelled && setNotice("没读到学校的 AI 服务状态，稍后再打开看看"));
    if (isAdmin) {
      fetchAiConfig()
        .then((next) => {
          if (cancelled) return;
          setView(next);
          setDrafts(toDrafts(next));
        })
        .catch((err) => !cancelled && setNotice(errorDetail(err)));
    }
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  async function runTest(service: AiServiceName) {
    setTests((prev) => ({ ...prev, [service]: { pending: true } }));
    try {
      const result = await testAiService(service);
      setTests((prev) => ({ ...prev, [service]: { pending: false, ok: true, text: `连通 · ${result.model}` } }));
    } catch (err) {
      setTests((prev) => ({ ...prev, [service]: { pending: false, ok: false, text: errorDetail(err) } }));
    }
    // 测试接口刚丢掉了后端缓存，这时读到的状态才是最新的
    await fetchAiStatus().then(setStatus, () => undefined);
  }

  async function handleSave() {
    if (!drafts) return;
    setSaving(true);
    setNotice(null);
    try {
      const saved = await saveAiConfig(drafts);
      setView(saved);
      setDrafts(toDrafts(saved));
      // 后端把学校配置缓存 60 秒，测试接口会先丢掉缓存，所以先测再读状态
      for (const service of ["chat", "reader"] as const) {
        if (saved[service].hasKey || status?.[service].configured) await runTest(service);
      }
      setStatus(await fetchAiStatus());
      setNotice("已保存。");
    } catch (err) {
      setNotice(errorDetail(err));
    } finally {
      setSaving(false);
    }
  }

  const chatMissing = status && !status.chat.configured;

  return (
    <section className="space-y-4">
      <h3 className="font-display text-xl leading-tight">学校的 AI 服务</h3>
      {chatMissing && (
        <p className="sketch bg-skin px-3 py-2 text-[0.9375rem]">
          {isAdmin
            ? "还没有可用的对话模型，问答暂时用不了。在下面填好学校的模型密钥并保存。"
            : "学校还没配置对话模型，问答暂时用不了，请联系老师或管理员。"}
        </p>
      )}

      <ul className="divide-y divide-dashed divide-rule">
        {ROWS.map(({ service, name, use }) => {
          const item = status?.[service];
          const test = tests[service];
          return (
            <li key={service} className="flex items-start gap-3 py-3">
              <StatusDot ok={Boolean(item?.configured)} />
              <div className="min-w-0 flex-1">
                <p className="leading-snug">
                  {name}
                  <span className="ml-2 text-sm text-muted">
                    {!item
                      ? "读取中…"
                      : item.configured && item.source
                        ? `${SOURCE_TEXT[item.source]} · ${item.model}`
                        : service === "embedding"
                          ? "未配置：需要在服务器上配置"
                          : "未配置"}
                  </span>
                </p>
                <p className="text-sm text-muted">{use}</p>
                {test?.text && (
                  <p className={`mt-1 text-sm break-all ${test.ok ? "text-ink" : "text-danger"}`} role="status">
                    {test.text}
                  </p>
                )}
              </div>
              {isAdmin && item?.configured && (
                <button type="button" disabled={test?.pending} onClick={() => void runTest(service)} className="btn btn-sm shrink-0">
                  {test?.pending ? "测试中…" : "测一下"}
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {isAdmin && view && drafts && (
        <details open={editing} onToggle={(event) => setEditing(event.currentTarget.open)} className="sketch px-4 py-3">
          <summary className="cursor-pointer font-display text-lg">改学校的配置</summary>
          <p className="mt-1 text-sm text-muted">
            填了就用学校自己的模型，不填用平台默认。密钥加密保存，保存后只显示末四位。
          </p>
          {(["chat", "reader"] as const).map((service) => (
            <fieldset key={service} className="mt-4 border-t border-dashed border-rule pt-3">
              <legend className="pr-2 font-display text-base">{service === "chat" ? "对话模型" : "长文档精读"}</legend>
              <EndpointForm
                service={service}
                view={view[service]}
                draft={drafts[service]}
                onChange={(patch) => setDrafts((prev) => prev && { ...prev, [service]: { ...prev[service], ...patch } })}
              />
            </fieldset>
          ))}
          <button type="button" disabled={saving} onClick={() => void handleSave()} className="btn btn-primary mt-5">
            {saving ? "保存并测试中…" : "保存并测试"}
          </button>
        </details>
      )}
      {notice && (
        <p className="text-[0.9375rem] break-all text-muted" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
