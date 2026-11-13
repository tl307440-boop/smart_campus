"use client";

import { useCallback, useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { useToast } from "@/components/Toast";
import { useCopy } from "@/hooks/useCopy";
import {
  type ImBinding,
  type ImBindingsState,
  createImBindCode,
  deleteImBinding,
  errorDetail,
  fetchDigestPreview,
  fetchImBindings,
  updateImBinding,
} from "@/lib/api";

function BindingRow({
  binding,
  onSaved,
  onRemoved,
}: {
  binding: ImBinding;
  onSaved: (next: ImBinding) => void;
  onRemoved: (id: string) => void;
}) {
  const toast = useToast();
  const [morningDigest, setMorningDigest] = useState(binding.morning_digest);
  const [digestTime, setDigestTime] = useState(binding.digest_time || "07:30");
  const [gradeAlert, setGradeAlert] = useState(binding.grade_alert);
  const [ticketAlert, setTicketAlert] = useState(binding.ticket_alert);
  const [newsKeywords, setNewsKeywords] = useState(binding.news_keywords || "");
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await updateImBinding(binding.id, {
        morning_digest: morningDigest,
        digest_time: digestTime,
        grade_alert: gradeAlert,
        ticket_alert: ticketAlert,
        news_keywords: newsKeywords.trim(),
      });
      onSaved(updated);
      toast.success("推送偏好已保存");
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await deleteImBinding(binding.id);
      onRemoved(binding.id);
      toast.success("已解除绑定");
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="sketch bg-paper p-3.5 space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-skin px-2 py-0.5 text-xs">
            {binding.platform === "wecom" ? "企业微信" : "QQ"}
          </span>
          <span className="font-medium text-sm">
            {binding.display_name || binding.external_id}
          </span>
          {binding.display_name && (
            <span className="text-xs text-muted tabular-nums">({binding.external_id})</span>
          )}
        </div>
        <button type="button" disabled={saving} onClick={() => void handleDelete()} className="btn btn-sm">
          解绑
        </button>
      </div>

      <div className="grid gap-2 text-sm">
        <label className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={morningDigest}
              onChange={(e) => setMorningDigest(e.target.checked)}
              className="size-4 accent-pink"
            />
            每日早报（课表 / 考试倒计时 / 匹配通知）
          </span>
          <input
            type="time"
            value={digestTime}
            onChange={(e) => setDigestTime(e.target.value)}
            aria-label="早报推送时间"
            className="field w-28 py-1 text-sm tabular-nums"
          />
        </label>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={gradeAlert}
            onChange={(e) => setGradeAlert(e.target.checked)}
            className="size-4 accent-pink"
          />
          教务新出成绩立刻推送分数与最新 GPA
        </label>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={ticketAlert}
            onChange={(e) => setTicketAlert(e.target.checked)}
            className="size-4 accent-pink"
          />
          人工求助工单被老师回复时提醒我
        </label>

        <label className="block pt-1">
          <span className="field-label">重点订阅的通知关键词（空格分隔，如 计算机 四六级 奖学金）</span>
          <input
            value={newsKeywords}
            onChange={(e) => setNewsKeywords(e.target.value)}
            placeholder="留空则按你记忆里的学院/专业自动筛"
            className="field text-sm"
          />
        </label>
      </div>

      <button type="button" disabled={saving} onClick={() => void handleSave()} className="btn btn-sm btn-primary">
        {saving ? "保存中…" : "保存推送设置"}
      </button>
    </li>
  );
}

/** 抽屉页「消息推送」：企微/QQ 6 位绑定码、早报与出分推送偏好、今日早报即时预览。 */
export function ImPushPane() {
  const toast = useToast();
  const { copied, copy } = useCopy();
  const [state, setState] = useState<ImBindingsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bindCode, setBindCode] = useState<{ code: string; expires_in: number } | null>(null);
  const [generating, setGenerating] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await fetchImBindings());
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchImBindings()
      .then((res) => {
        if (!cancelled) {
          setState(res);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(errorDetail(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleGenerateCode() {
    setGenerating(true);
    try {
      setBindCode(await createImBindCode());
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setGenerating(false);
    }
  }

  async function handlePreview() {
    setPreviewing(true);
    try {
      const kw = state?.bindings[0]?.news_keywords || "";
      const today = new Date();
      const day = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
      const res = await fetchDigestPreview(kw, day);
      setPreview(res.content);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <section className="space-y-4">
      <PaneTitle title="企微 / QQ 机器人与推送">
        把校园助手接到日常聊天工具里，不用每次打开网页：支持群内/私聊直接问课表通知，以及每日早报、新成绩发布、人工工单回复主动触达。
      </PaneTitle>

      {state && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className={`rounded-full px-2.5 py-1 ${state.wecom_enabled ? "bg-flower/25 text-ink" : "bg-skin text-muted"}`}>
            企业微信机器人：{state.wecom_enabled ? "学校已配置" : "学校未开启"}
          </span>
          <span className={`rounded-full px-2.5 py-1 ${state.qq_enabled ? "bg-flower/25 text-ink" : "bg-skin text-muted"}`}>
            QQ OneBot 机器人：{state.qq_enabled ? "学校已配置" : "学校未开启"}
          </span>
        </div>
      )}

      <div className="sketch bg-paper p-3.5 space-y-2.5">
        <h3 className="font-display text-base">第一步：生成 6 位绑定码</h3>
        <p className="text-sm text-muted">
          绑定码 10 分钟内有效。生成后在企微或 QQ 机器人对话框发送 <code className="rounded bg-skin px-1.5 py-0.5">绑定 数字码</code> 即可关联本人账号；也支持随时发 <code className="rounded bg-skin px-1.5 py-0.5">早报</code>、<code className="rounded bg-skin px-1.5 py-0.5">帮助</code>、<code className="rounded bg-skin px-1.5 py-0.5">解绑</code>。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={generating} onClick={() => void handleGenerateCode()} className="btn btn-sm btn-primary">
            {generating ? "生成中…" : bindCode ? "重新生成绑定码" : "生成 6 位绑定码"}
          </button>
          <button type="button" onClick={() => void load()} className="btn btn-sm">
            刷新已绑定账号
          </button>
        </div>
        {bindCode && (
          <div className="mt-2 flex flex-wrap items-center justify-between gap-3 rounded border border-dashed border-rule bg-skin/70 px-3 py-2">
            <div>
              <p className="text-xs text-muted">请在企微 / QQ 对机器人发送：</p>
              <p className="font-display text-xl tracking-wider tabular-nums">绑定 {bindCode.code}</p>
            </div>
            <button
              type="button"
              onClick={() => void copy(`绑定 ${bindCode.code}`)}
              className="btn btn-sm"
            >
              {copied ? "已复制" : "复制口令"}
            </button>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="font-display text-base">已绑定的推送终端</h3>
        {loading ? (
          <p className="text-muted">正在读取…</p>
        ) : error ? (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        ) : !state || state.bindings.length === 0 ? (
          <p className="sketch bg-skin px-3 py-2 text-sm text-muted">还没有绑定过企业微信或 QQ。</p>
        ) : (
          <ul className="space-y-3">
            {state.bindings.map((b) => (
              <BindingRow
                key={b.id}
                binding={b}
                onSaved={(next) =>
                  setState((prev) =>
                    prev ? { ...prev, bindings: prev.bindings.map((row) => (row.id === next.id ? next : row)) } : prev,
                  )
                }
                onRemoved={(id) =>
                  setState((prev) =>
                    prev ? { ...prev, bindings: prev.bindings.filter((row) => row.id !== id) } : prev,
                  )
                }
              />
            ))}
          </ul>
        )}
      </div>

      <div className="sketch bg-paper p-3.5 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-display text-base">今日早报效果预览</h3>
          <button type="button" disabled={previewing} onClick={() => void handlePreview()} className="btn btn-sm">
            {previewing ? "生成中…" : "预览今日早报"}
          </button>
        </div>
        {preview && (
          <pre className="max-h-72 overflow-auto rounded border border-dashed border-rule bg-desk/50 p-3 font-body text-sm leading-relaxed whitespace-pre-wrap">
            {preview}
          </pre>
        )}
      </div>
    </section>
  );
}
