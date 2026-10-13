"use client";

import { useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { errorText } from "@/components/WorkspaceAdmin";
import { type EmbedConfig, createEmbed, deleteEmbed, embedSnippet, fetchEmbeds, updateEmbed } from "@/lib/workspace/client";

/** 抽屉页「挂件」（仅管理员）：学校官网上的聊天挂件，新建 / 停用 / 删除 / 复制嵌入代码。 */
export function EmbedPane() {
  const [embeds, setEmbeds] = useState<EmbedConfig[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftOrigins, setDraftOrigins] = useState("");
  const [draftLimit, setDraftLimit] = useState(200);

  useEffect(() => {
    let cancelled = false;
    fetchEmbeds()
      .then((list) => {
        if (!cancelled) setEmbeds(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setNotice(errorText(err, "读取失败"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function run(action: () => Promise<unknown>, fallback: string, done?: string) {
    setNotice(null);
    try {
      await action();
      setEmbeds(await fetchEmbeds());
      if (done) setNotice(done);
    } catch (err) {
      setNotice(errorText(err, fallback));
    }
  }

  function handleCreate() {
    void run(
      async () => {
        await createEmbed({
          name: draftName,
          allowedOrigins: draftOrigins.split(/[\s,，]+/).filter(Boolean),
          dailyLimit: draftLimit,
          enabled: true,
        });
        setDraftName("");
        setDraftOrigins("");
      },
      "创建失败",
      "已新建，复制代码贴到网页里就能用",
    );
  }

  function handleToggle(embed: EmbedConfig) {
    const { name, allowedOrigins, dailyLimit } = embed;
    void run(() => updateEmbed(embed.id, { name, allowedOrigins, dailyLimit, enabled: !embed.enabled }), "更新失败");
  }

  function handleDelete(embed: EmbedConfig) {
    if (!window.confirm(`删除挂件「${embed.name}」？已嵌入的网页会立即失效。`)) return;
    void run(() => deleteEmbed(embed.id), "删除失败");
  }

  async function handleCopy(embed: EmbedConfig) {
    try {
      await navigator.clipboard.writeText(embedSnippet(embed.id));
      setNotice(`已复制「${embed.name}」的嵌入代码`);
    } catch {
      setNotice(embedSnippet(embed.id));
    }
  }

  return (
    <section>
      <PaneTitle title="挂件">
        在学校官网放一个聊天入口。访客不用登录，只能提问，不能上传资料，也没有长期记忆。
      </PaneTitle>

      <form
        className="sketch space-y-3 p-4"
        onSubmit={(event) => {
          event.preventDefault();
          handleCreate();
        }}
      >
        <h3 className="font-display text-lg">新建挂件</h3>
        <label className="block">
          <span className="field-label">名称</span>
          <input value={draftName} onChange={(event) => setDraftName(event.target.value)} placeholder="如：官网首页" className="field" />
        </label>
        <label className="block">
          <span className="field-label">允许的网站（空格或逗号分隔）</span>
          <input
            value={draftOrigins}
            onChange={(event) => setDraftOrigins(event.target.value)}
            placeholder="https://www.example.edu.cn"
            className="field"
          />
        </label>
        <div className="flex items-end gap-3">
          <label className="block w-32">
            <span className="field-label">每日回答上限</span>
            <input
              type="number"
              min={1}
              value={draftLimit}
              onChange={(event) => setDraftLimit(Number(event.target.value))}
              className="field tabular-nums"
            />
          </label>
          <button type="submit" disabled={!draftName.trim() || !draftOrigins.trim()} className="btn btn-primary ml-auto">
            新建
          </button>
        </div>
      </form>
      {notice && <p className="mt-3 text-[0.9375rem] break-all text-muted" role="status">{notice}</p>}

      <ul className="mt-5 divide-y divide-dashed divide-rule">
        {embeds.map((embed) => (
          <li key={embed.id} className="py-3">
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 truncate font-display text-lg">{embed.name}</p>
              <span className={`shrink-0 text-sm ${embed.enabled ? "" : "text-muted"}`}>
                {embed.enabled ? "启用中" : "已停用"}
              </span>
            </div>
            <p className="mt-0.5 text-sm break-all text-muted">
              {embed.allowedOrigins.join("、")} · 每日上限 <span className="tabular-nums">{embed.dailyLimit}</span> 次
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" onClick={() => void handleCopy(embed)} className="btn btn-sm">
                复制代码
              </button>
              <button type="button" onClick={() => handleToggle(embed)} className="btn btn-sm">
                {embed.enabled ? "停用" : "启用"}
              </button>
              <button type="button" onClick={() => handleDelete(embed)} className="btn btn-sm btn-danger">
                删除
              </button>
            </div>
          </li>
        ))}
        {embeds.length === 0 && <li className="py-3 text-muted">还没有挂件。</li>}
      </ul>
    </section>
  );
}
