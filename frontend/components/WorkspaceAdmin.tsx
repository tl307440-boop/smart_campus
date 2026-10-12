"use client";

import { useEffect, useState } from "react";
import {
  type EmbedConfig,
  type WorkspaceSettings,
  createEmbed,
  deleteEmbed,
  embedSnippet,
  fetchEmbeds,
  fetchWorkspaceSettings,
  saveWorkspaceSettings,
  updateEmbed,
} from "@/lib/workspace/client";

interface Props {
  open: boolean;
  onClose: () => void;
}

const FIELD = "rounded-lg border border-[#f0d0d6] bg-white px-3 py-2 text-sm";
const PILL = "rounded-full border border-[#e7b7c2] px-3 py-1 text-xs text-[#c45b73] disabled:opacity-40";
const SECTION = "mt-4 rounded-xl border border-[#f3d5db] bg-white p-4";

const EMPTY_SETTINGS: WorkspaceSettings = {
  assistantName: "小懒",
  persona: "",
  queryOnly: false,
  similarityThreshold: 0,
  topK: 3,
};

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** 管理员面板：本校工作区设置 + 嵌入挂件管理。写入都走 Next 的 /api/admin/*。 */
export function WorkspaceAdmin({ open, onClose }: Props) {
  const [settings, setSettings] = useState<WorkspaceSettings>(EMPTY_SETTINGS);
  const [embeds, setEmbeds] = useState<EmbedConfig[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [embedNotice, setEmbedNotice] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftOrigins, setDraftOrigins] = useState("");
  const [draftLimit, setDraftLimit] = useState(200);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    Promise.all([fetchWorkspaceSettings(), fetchEmbeds()])
      .then(([loaded, list]) => {
        if (cancelled) return;
        setSettings(loaded);
        setEmbeds(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setNotice(errorText(err, "读取失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  function update<K extends keyof WorkspaceSettings>(key: K, value: WorkspaceSettings[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setNotice(null);
    try {
      setSettings(await saveWorkspaceSettings(settings));
      setNotice("已保存，约一分钟内对新提问生效");
    } catch (err) {
      setNotice(errorText(err, "保存失败"));
    }
  }

  async function handleCreateEmbed() {
    setEmbedNotice(null);
    try {
      await createEmbed({
        name: draftName,
        allowedOrigins: draftOrigins.split(/[\s,，]+/).filter(Boolean),
        dailyLimit: draftLimit,
        enabled: true,
      });
      setDraftName("");
      setDraftOrigins("");
      setEmbeds(await fetchEmbeds());
    } catch (err) {
      setEmbedNotice(errorText(err, "创建失败"));
    }
  }

  async function handleToggle(embed: EmbedConfig) {
    setEmbedNotice(null);
    try {
      const { name, allowedOrigins, dailyLimit } = embed;
      await updateEmbed(embed.id, { name, allowedOrigins, dailyLimit, enabled: !embed.enabled });
      setEmbeds(await fetchEmbeds());
    } catch (err) {
      setEmbedNotice(errorText(err, "更新失败"));
    }
  }

  async function handleDelete(embed: EmbedConfig) {
    if (!window.confirm(`删除挂件「${embed.name}」？已嵌入的网页会立即失效。`)) return;
    setEmbedNotice(null);
    try {
      await deleteEmbed(embed.id);
      setEmbeds(await fetchEmbeds());
    } catch (err) {
      setEmbedNotice(errorText(err, "删除失败"));
    }
  }

  async function handleCopy(embed: EmbedConfig) {
    try {
      await navigator.clipboard.writeText(embedSnippet(embed.id));
      setEmbedNotice(`已复制「${embed.name}」的嵌入代码`);
    } catch {
      setEmbedNotice(embedSnippet(embed.id));
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 px-4 py-8">
      <div className="w-full max-w-3xl rounded-2xl border border-[#f0c9cf] bg-[#fff7f8] p-6 text-[#5c3a42] shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[#c45b73]">工作区与嵌入挂件</h2>
          <button type="button" onClick={onClose} className="rounded-full px-3 py-1 text-sm">
            关闭
          </button>
        </div>

        <section className={SECTION}>
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[#c45b73]">本校工作区设置</h3>
            <button type="button" onClick={() => void handleSave()} className={PILL}>
              保存
            </button>
          </div>
          <label className="mt-4 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>助手称呼</span>
            <input
              value={settings.assistantName}
              onChange={(event) => update("assistantName", event.target.value)}
              className={FIELD}
            />
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-start gap-3 text-sm">
            <span className="pt-2">人设说明</span>
            <textarea
              value={settings.persona}
              onChange={(event) => update("persona", event.target.value)}
              rows={3}
              placeholder="例如：你服务于武汉文理学院，回答时附上负责部门的联系方式。"
              className={FIELD}
            />
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>只按资料回答</span>
            <span className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.queryOnly}
                onChange={(event) => update("queryOnly", event.target.checked)}
                className="size-4 accent-[#c45b73]"
              />
              <span className="text-xs text-[#8a6a72]">开启后不闲聊，资料里查不到就直接说明没找到</span>
            </span>
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>相似度阈值</span>
            <span className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={0.9}
                step={0.05}
                value={settings.similarityThreshold}
                onChange={(event) => update("similarityThreshold", Number(event.target.value))}
                className="flex-1 accent-[#c45b73]"
              />
              <span className="w-12 text-right">{settings.similarityThreshold.toFixed(2)}</span>
            </span>
          </label>
          <label className="mt-3 grid grid-cols-[8rem_1fr] items-center gap-3 text-sm">
            <span>引用片段数</span>
            <input
              type="number"
              min={1}
              max={10}
              value={settings.topK}
              onChange={(event) => update("topK", Number(event.target.value))}
              className={`${FIELD} w-24`}
            />
          </label>
          {notice && <p className="mt-3 text-sm text-[#8a5a66]">{notice}</p>}
        </section>

        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-[#c45b73]">嵌入挂件</h3>
          <p className="mt-1 text-xs text-[#8a6a72]">
            在学校官网放一个聊天气泡。访客无需登录，只能提问，不能上传资料，也没有长期记忆。
          </p>
          <div className="mt-3 grid grid-cols-[1fr_1.6fr_6rem_auto] gap-2">
            <input
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              placeholder="名称，如：官网首页"
              className={FIELD}
            />
            <input
              value={draftOrigins}
              onChange={(event) => setDraftOrigins(event.target.value)}
              placeholder="允许的网站，如 https://www.example.edu.cn"
              className={FIELD}
            />
            <input
              type="number"
              min={1}
              value={draftLimit}
              onChange={(event) => setDraftLimit(Number(event.target.value))}
              title="每日回答上限"
              className={FIELD}
            />
            <button type="button" onClick={() => void handleCreateEmbed()} className={PILL}>
              新建
            </button>
          </div>

          <ul className="mt-4 space-y-2">
            {embeds.map((embed) => (
              <li key={embed.id} className="rounded-lg border border-[#f3d5db] px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">
                    {embed.name}
                    {!embed.enabled && <span className="ml-2 text-xs text-[#b08a93]">已停用</span>}
                  </span>
                  <span className="flex shrink-0 gap-2">
                    <button type="button" onClick={() => void handleCopy(embed)} className={PILL}>
                      复制代码
                    </button>
                    <button type="button" onClick={() => void handleToggle(embed)} className={PILL}>
                      {embed.enabled ? "停用" : "启用"}
                    </button>
                    <button type="button" onClick={() => void handleDelete(embed)} className={PILL}>
                      删除
                    </button>
                  </span>
                </div>
                <p className="mt-1 text-xs break-all text-[#8a6a72]">
                  {embed.allowedOrigins.join("、")} · 每日上限 {embed.dailyLimit} 次
                </p>
              </li>
            ))}
            {embeds.length === 0 && <li className="text-xs text-[#8a6a72]">还没有挂件。</li>}
          </ul>
          {embedNotice && <p className="mt-3 text-sm break-all text-[#8a5a66]">{embedNotice}</p>}
        </section>
      </div>
    </div>
  );
}
