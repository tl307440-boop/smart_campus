"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type MemoryNote,
  addMemories,
  clearMemories,
  deleteMemory,
  fetchMemories,
  updateMemory,
} from "@/lib/api";
import { PaneTitle } from "@/components/ToolDrawer";

/** 抽屉页「记忆」：长期记忆在服务端，换设备登录也在。 */
const SLOT_LABELS: Record<MemoryNote["slot"], string> = {
  grade: "年级",
  college: "学院",
  major: "专业",
  "": "",
};

/** 一条记忆：平时是一行字，点「改」变成输入框。置顶的排在最前并标出来。 */
function MemoryRow({
  note,
  onSave,
  onPin,
  onForget,
}: {
  note: MemoryNote;
  onSave: (id: string, content: string) => Promise<void>;
  onPin: (id: string, pinned: boolean) => Promise<void>;
  onForget: (id: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.content);
  const [busy, setBusy] = useState(false);

  async function save() {
    const content = draft.trim();
    if (!content || content === note.content) {
      setEditing(false);
      return;
    }
    setBusy(true);
    try {
      await onSave(note.id, content);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex items-start justify-between gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            value={draft}
            autoFocus
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void save();
              if (event.key === "Escape") setEditing(false);
            }}
            aria-label="修改这条记忆"
            className="field"
          />
        ) : (
          <span className="break-words">
            {note.content}
            {SLOT_LABELS[note.slot] && (
              <span className="ml-2 text-sm text-muted">{SLOT_LABELS[note.slot]}</span>
            )}
            {note.pinned && <span className="ml-2 text-sm text-pink">置顶</span>}
            {note.origin === "auto" && <span className="ml-2 text-sm text-muted">自动记下</span>}
          </span>
        )}
        {note.sensitive && (
          <p className="mt-1 text-sm text-muted">
            这条涉及隐私，只有你能看到，不会自动带进回答。
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {editing ? (
          <>
            <button type="button" disabled={busy} onClick={() => void save()} className="link-ink text-sm">
              保存
            </button>
            <button type="button" onClick={() => setEditing(false)} className="link-ink text-sm">
              取消
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(note.content);
                setEditing(true);
              }}
              className="link-ink text-sm"
            >
              改
            </button>
            <button type="button" onClick={() => void onPin(note.id, !note.pinned)} className="link-ink text-sm">
              {note.pinned ? "取消置顶" : "置顶"}
            </button>
            <button type="button" onClick={() => void onForget(note.id)} className="link-ink text-sm">
              忘掉
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export function MemoryPane() {
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);

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

  async function handleSave(id: string, content: string) {
    setNotice(null);
    try {
      const updated = await updateMemory(id, { content });
      setNotes((list) => list.map((note) => (note.id === id ? updated : note)));
      if (updated.sensitive) setNotice("这条涉及隐私，只留给你自己看，不会自动带进回答。");
    } catch {
      setNotice("没改成，稍后再试");
    }
  }

  async function handlePin(id: string, pinned: boolean) {
    setNotice(null);
    try {
      const updated = await updateMemory(id, { pinned });
      setNotes((list) => list.map((note) => (note.id === id ? updated : note)));
    } catch {
      setNotice("置顶没生效，稍后再试");
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

  async function handleClear() {
    setNotice(null);
    try {
      const result = await clearMemories();
      setNotes([]);
      setConfirmingClear(false);
      setNotice(`已忘掉 ${result.removed} 条。`);
    } catch {
      setNotice("没清干净，稍后再试");
    }
  }

  // 置顶的排前面，其余按时间倒序（后端已按时间排好）
  const ordered = [...notes].sort((a, b) => Number(b.pinned) - Number(a.pinned));

  return (
    <section>
      <PaneTitle title="记忆">
        告诉我的事（比如学院、专业）会记在这里，换设备登录也在。聊天时说「记住：……」也能记下。
        年级、学院、专业只保留最新的说法。
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
        {ordered.map((note) => (
          <MemoryRow
            key={note.id}
            note={note}
            onSave={handleSave}
            onPin={handlePin}
            onForget={handleForget}
          />
        ))}
        {notes.length === 0 && <li className="py-2.5 text-muted">还没有记住什么。</li>}
      </ul>

      {notes.length > 0 && (
        <div className="mt-4 flex items-center gap-3">
          {confirmingClear ? (
            <>
              <span className="text-sm text-muted">全部忘掉？这个不能撤回。</span>
              <button type="button" onClick={() => void handleClear()} className="btn btn-sm btn-danger">
                确定忘掉
              </button>
              <button type="button" onClick={() => setConfirmingClear(false)} className="link-ink text-sm">
                再想想
              </button>
            </>
          ) : (
            <button type="button" onClick={() => setConfirmingClear(true)} className="link-ink text-sm text-danger">
              全部忘掉
            </button>
          )}
        </div>
      )}

      {notice && <p className="mt-3 text-[0.9375rem] text-danger" role="status">{notice}</p>}
    </section>
  );
}
