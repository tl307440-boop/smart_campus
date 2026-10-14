"use client";

import { useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { type WorkspaceSettings, fetchWorkspaceSettings, saveWorkspaceSettings } from "@/lib/workspace/client";

interface Props {
  /** 保存成功后通知页面重新读取助手称呼 */
  onSaved?: () => void;
}

export function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** 抽屉页「工作区」（仅管理员）：本校助手称呼、人设和检索参数。写入走 Next 的 /api/admin/workspace。 */
export function WorkspacePane({ onSaved }: Props) {
  const [settings, setSettings] = useState<WorkspaceSettings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchWorkspaceSettings()
      .then((loaded) => {
        if (!cancelled) setSettings(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setNotice(errorText(err, "读取失败"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function update<K extends keyof WorkspaceSettings>(key: K, value: WorkspaceSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function handleSave() {
    if (!settings) return;
    setNotice(null);
    setSaving(true);
    try {
      setSettings(await saveWorkspaceSettings(settings));
      setNotice("已保存，约一分钟内对新提问生效");
      onSaved?.();
    } catch (err) {
      setNotice(errorText(err, "保存失败"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <PaneTitle title="工作区">本校所有学生看到的助手。改完点保存。</PaneTitle>
      {!settings ? (
        <p className="text-muted">{notice ?? "正在读取…"}</p>
      ) : (
        <>
          <div className="space-y-5">
            <label className="block">
              <span className="field-label">助手称呼</span>
              <input value={settings.assistantName} onChange={(event) => update("assistantName", event.target.value)} className="field" />
            </label>
            <label className="block">
              <span className="field-label">人设说明</span>
              <textarea
                value={settings.persona}
                onChange={(event) => update("persona", event.target.value)}
                rows={3}
                placeholder="例如：你服务于武汉文理学院，回答时附上负责部门的联系方式。"
                className="field resize-y"
              />
            </label>
            <label className="block">
              <span className="field-label">学校心理咨询联系方式</span>
              <textarea
                value={settings.counselingContact}
                onChange={(event) => update("counselingContact", event.target.value)}
                rows={2}
                maxLength={200}
                placeholder="例如：心理健康教育中心 027-1234 5678，工作日 8:30–17:30，图书馆三楼"
                className="field resize-y"
              />
              <span className="mt-1 block text-sm text-muted">
                同学流露出伤害自己的念头时，会和全国心理援助热线 12356 一起出现在求助卡片里。不填就只显示全国热线。
              </span>
            </label>
            <label className="flex items-start gap-2.5">
              <input
                type="checkbox"
                checked={settings.queryOnly}
                onChange={(event) => update("queryOnly", event.target.checked)}
                className="mt-1.5 size-4 accent-pink"
              />
              <span>
                只按资料回答
                <span className="block text-sm text-muted">开启后不闲聊，资料里查不到就直接说没找到</span>
              </span>
            </label>
            <label className="block">
              <span className="field-label">
                相似度阈值 <span className="text-ink tabular-nums">{settings.similarityThreshold.toFixed(2)}</span>
              </span>
              <input
                type="range"
                min={0}
                max={0.9}
                step={0.05}
                value={settings.similarityThreshold}
                onChange={(event) => update("similarityThreshold", Number(event.target.value))}
                className="mt-2 w-full accent-pink"
              />
            </label>
            <label className="block w-40">
              <span className="field-label">引用片段数</span>
              <input
                type="number"
                min={1}
                max={10}
                value={settings.topK}
                onChange={(event) => update("topK", Number(event.target.value))}
                className="field tabular-nums"
              />
            </label>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button type="button" disabled={saving} onClick={() => void handleSave()} className="btn btn-primary">
              {saving ? "保存中…" : "保存"}
            </button>
            {notice && <p className="text-[0.9375rem] text-muted" role="status">{notice}</p>}
          </div>
        </>
      )}
    </section>
  );
}
