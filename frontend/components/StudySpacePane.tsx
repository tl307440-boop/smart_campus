"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { useToast } from "@/components/Toast";
import {
  type StudyChunkHit,
  type StudyCourse,
  type StudyFlashcard,
  type StudyMaterial,
  type StudyQuiz,
  type StudyQuizGrade,
  createStudyCourse,
  createStudyNote,
  deleteFlashcard,
  deleteStudyCourse,
  deleteStudyMaterial,
  errorDetail,
  fetchFlashcards,
  fetchStudyCourses,
  fetchStudyMaterials,
  generateFlashcards,
  generateStudyQuiz,
  gradeStudyQuiz,
  reviewFlashcard,
  searchStudyCourse,
  syncStudyCoursesFromJw,
  uploadStudyFile,
} from "@/lib/api";

type ModeTab = "materials" | "cards" | "quiz";

export function StudySpacePane({
  jwOn,
  onAsk,
}: {
  jwOn: boolean;
  onAsk: (question: string) => void;
}) {
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [courses, setCourses] = useState<StudyCourse[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loadingCourses, setLoadingCourses] = useState(true);
  const [courseError, setCourseError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const [addingCourse, setAddingCourse] = useState(false);
  const [newCourseName, setNewCourseName] = useState("");
  const [newCourseTeacher, setNewCourseTeacher] = useState("");

  const [mode, setMode] = useState<ModeTab>("materials");
  const [materials, setMaterials] = useState<StudyMaterial[]>([]);
  const [loadingMaterials, setLoadingMaterials] = useState(false);
  const [uploading, setUploading] = useState(false);

  const [addingNote, setAddingNote] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<StudyChunkHit[] | null>(null);
  const [searching, setSearching] = useState(false);

  const [cards, setCards] = useState<StudyFlashcard[]>([]);
  const [dueOnly, setDueOnly] = useState(false);
  const [cardTopic, setCardTopic] = useState("");
  const [cardCount, setCardCount] = useState(6);
  const [generatingCards, setGeneratingCards] = useState(false);
  const [cardIndex, setCardIndex] = useState(0);
  const [cardFlipped, setCardFlipped] = useState(false);
  const [reviewingCard, setReviewingCard] = useState(false);

  const [quizTopic, setQuizTopic] = useState("");
  const [quizCount, setQuizCount] = useState(4);
  const [generatingQuiz, setGeneratingQuiz] = useState(false);
  const [quiz, setQuiz] = useState<StudyQuiz | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [gradingQuiz, setGradingQuiz] = useState(false);
  const [gradeResult, setGradeResult] = useState<StudyQuizGrade | null>(null);

  const resetTransient = useCallback(() => {
    setSearchHits(null);
    setQuiz(null);
    setGradeResult(null);
    setAnswers({});
  }, []);

  const selectCourse = useCallback(
    (courseId: string) => {
      resetTransient();
      setSelectedId(courseId);
    },
    [resetTransient],
  );

  const loadCourses = useCallback(async (preferredId?: string) => {
    setLoadingCourses(true);
    setCourseError(null);
    try {
      const list = await fetchStudyCourses();
      setCourses(list);
      setSelectedId((prev) => {
        if (preferredId && list.some((c) => c.id === preferredId)) return preferredId;
        if (prev && list.some((c) => c.id === prev)) return prev;
        return list[0]?.id ?? "";
      });
    } catch (err) {
      setCourseError(errorDetail(err));
    } finally {
      setLoadingCourses(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchStudyCourses()
      .then((list) => {
        if (cancelled) return;
        setCourses(list);
        setCourseError(null);
        setSelectedId((prev) => (prev && list.some((c) => c.id === prev) ? prev : (list[0]?.id ?? "")));
      })
      .catch((err) => {
        if (!cancelled) setCourseError(errorDetail(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingCourses(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadCourseDetails = useCallback(
    async (courseId: string, onlyDue: boolean) => {
      if (!courseId) {
        setMaterials([]);
        setCards([]);
        return;
      }
      setLoadingMaterials(true);
      try {
        const [mats, fcards] = await Promise.all([
          fetchStudyMaterials(courseId),
          fetchFlashcards(courseId, onlyDue),
        ]);
        setMaterials(mats);
        setCards(fcards);
        setCardIndex(0);
        setCardFlipped(false);
      } catch (err) {
        toast.error(errorDetail(err));
      } finally {
        setLoadingMaterials(false);
      }
    },
    [toast],
  );

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    Promise.all([fetchStudyMaterials(selectedId), fetchFlashcards(selectedId, dueOnly)])
      .then(([mats, fcards]) => {
        if (cancelled) return;
        setMaterials(mats);
        setCards(fcards);
        setCardIndex(0);
        setCardFlipped(false);
      })
      .catch((err) => {
        if (!cancelled) toast.error(errorDetail(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingMaterials(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, dueOnly, toast]);

  const selectedCourse = courses.find((c) => c.id === selectedId) ?? null;

  async function handleSyncJw() {
    setSyncing(true);
    try {
      const today = new Date();
      const day = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
      const list = await syncStudyCoursesFromJw(day);
      setCourses(list);
      if (!selectedId && list[0]) setSelectedId(list[0].id);
      toast.success(`已同步 ${list.length} 门课程到个人伴学空间`);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setSyncing(false);
    }
  }

  async function handleCreateCourse(e: React.FormEvent) {
    e.preventDefault();
    if (!newCourseName.trim()) return;
    try {
      const created = await createStudyCourse({
        name: newCourseName.trim(),
        teacher: newCourseTeacher.trim(),
      });
      setNewCourseName("");
      setNewCourseTeacher("");
      setAddingCourse(false);
      await loadCourses(created.id);
      toast.success(`已创建课程《${created.name}》`);
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleDeleteCourse(course: StudyCourse) {
    try {
      await deleteStudyCourse(course.id);
      toast.success(`已删除《${course.name}》及其个人课件`);
      await loadCourses();
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !selectedId) return;
    setUploading(true);
    try {
      const created = await uploadStudyFile(selectedId, file);
      setMaterials((prev) => [created, ...prev]);
      toast.success(`已导入《${created.title}》（${created.char_count} 字）`);
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleAddNote(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedId || !noteTitle.trim() || !noteContent.trim()) return;
    setUploading(true);
    try {
      const created = await createStudyNote(selectedId, noteTitle.trim(), noteContent.trim());
      setMaterials((prev) => [created, ...prev]);
      setNoteTitle("");
      setNoteContent("");
      setAddingNote(false);
      toast.success(`已保存笔记《${created.title}》`);
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteMaterial(mat: StudyMaterial) {
    try {
      await deleteStudyMaterial(mat.id);
      setMaterials((prev) => prev.filter((item) => item.id !== mat.id));
      toast.success(`已移除《${mat.title}》`);
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedId || !searchQuery.trim()) return;
    setSearching(true);
    try {
      setSearchHits(await searchStudyCourse(selectedId, searchQuery.trim()));
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setSearching(false);
    }
  }

  async function handleGenerateCards() {
    if (!selectedId) return;
    setGeneratingCards(true);
    try {
      const added = await generateFlashcards(selectedId, {
        topic: cardTopic.trim(),
        count: cardCount,
      });
      toast.success(`已提炼生成 ${added.length} 张考点闪卡`);
      setDueOnly(false);
      await loadCourseDetails(selectedId, false);
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setGeneratingCards(false);
    }
  }

  async function handleReviewCard(card: StudyFlashcard, quality: 1 | 2 | 3 | 4) {
    setReviewingCard(true);
    try {
      const updated = await reviewFlashcard(card.id, quality);
      setCardFlipped(false);
      setCards((prev) => {
        if (dueOnly && quality > 1) {
          return prev.filter((c) => c.id !== card.id);
        }
        return prev.map((c) => (c.id === updated.id ? updated : c));
      });
      setCardIndex((idx) => (cards.length <= 1 ? 0 : idx % Math.max(1, dueOnly && quality > 1 ? cards.length - 1 : cards.length)));
      if (!dueOnly || quality === 1) {
        setCardIndex((idx) => (idx + 1) % Math.max(1, cards.length));
      }
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setReviewingCard(false);
    }
  }

  async function handleDeleteCard(cardId: string) {
    try {
      await deleteFlashcard(cardId);
      setCards((prev) => prev.filter((c) => c.id !== cardId));
      setCardFlipped(false);
      setCardIndex(0);
      await loadCourses(selectedId);
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleGenerateQuiz() {
    if (!selectedId) return;
    setGeneratingQuiz(true);
    setGradeResult(null);
    setAnswers({});
    try {
      const q = await generateStudyQuiz(selectedId, { topic: quizTopic.trim(), count: quizCount });
      setQuiz(q);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setGeneratingQuiz(false);
    }
  }

  async function handleGradeQuiz(e: React.FormEvent) {
    e.preventDefault();
    if (!quiz) return;
    setGradingQuiz(true);
    try {
      setGradeResult(await gradeStudyQuiz(quiz.quiz_id, answers));
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setGradingQuiz(false);
    }
  }

  const activeCard = cards[cardIndex] ?? cards[0] ?? null;

  return (
    <section className="space-y-4">
      <PaneTitle title="个人课程伴学空间">
        每门课独立存放在你本人账号下，上传 PPT / PDF / Word / 笔记不会进入全校公共库；可直接在主对话里基于课件提问、刷考点闪卡与考前自测。
      </PaneTitle>

      {/* 课程目录切换与同步 */}
      <div className="flex flex-wrap items-center gap-2">
        {jwOn && (
          <button type="button" disabled={syncing} onClick={() => void handleSyncJw()} className="btn btn-sm btn-primary">
            {syncing ? "同步中…" : "从教务课表同步本学期课程"}
          </button>
        )}
        <button type="button" onClick={() => setAddingCourse((v) => !v)} className="btn btn-sm">
          {addingCourse ? "收起" : "+ 新建课程文件夹"}
        </button>
      </div>

      {addingCourse && (
        <form onSubmit={handleCreateCourse} className="sketch bg-paper p-3 space-y-2">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              value={newCourseName}
              onChange={(e) => setNewCourseName(e.target.value)}
              placeholder="课程名称，如 操作系统 / 高等数学"
              required
              className="field text-sm"
            />
            <input
              value={newCourseTeacher}
              onChange={(e) => setNewCourseTeacher(e.target.value)}
              placeholder="任课老师（可选）"
              className="field text-sm"
            />
          </div>
          <button type="submit" className="btn btn-sm btn-primary">
            保存课程
          </button>
        </form>
      )}

      {loadingCourses ? (
        <p className="text-muted">正在加载课程空间…</p>
      ) : courseError ? (
        <p className="text-sm text-danger" role="alert">
          {courseError}
        </p>
      ) : courses.length === 0 ? (
        <p className="sketch bg-skin px-3 py-2 text-sm text-muted">
          还没有课程文件夹。可以点上面的「从教务课表同步」或「新建课程文件夹」开始整理你的课件与重点笔记。
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="我的课程">
            {courses.map((c) => (
              <button
                key={c.id}
                type="button"
                role="tab"
                aria-selected={selectedId === c.id}
                onClick={() => selectCourse(c.id)}
                className={`btn btn-sm flex items-center gap-1.5 ${selectedId === c.id ? "btn-primary" : ""}`}
              >
                <span>{c.name}</span>
                {c.due_card_count > 0 && (
                  <span className="rounded-full bg-pollen px-1.5 text-[11px] text-ink tabular-nums">
                    {c.due_card_count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {selectedCourse && (
            <div className="sketch bg-paper p-3.5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-dashed border-rule pb-2">
                <div>
                  <h3 className="font-display text-lg leading-tight">{selectedCourse.name}</h3>
                  <p className="text-xs text-muted">
                    {selectedCourse.teacher ? `${selectedCourse.teacher} · ` : ""}
                    {selectedCourse.term ? `${selectedCourse.term} · ` : ""}
                    资料 {selectedCourse.material_count} 份（{selectedCourse.char_count} 字）· 闪卡 {selectedCourse.card_count} 张
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => onAsk(`请根据我《${selectedCourse.name}》的伴学课件梳理本课程最核心的期末复习脉络与必考难点`)}
                    className="btn btn-sm"
                  >
                    在对话里问课件
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDeleteCourse(selectedCourse)}
                    className="btn btn-sm text-danger"
                  >
                    删课程
                  </button>
                </div>
              </div>

              <div role="tablist" className="flex flex-wrap gap-2">
                {[
                  { id: "materials" as const, label: `课件与笔记 (${materials.length})` },
                  { id: "cards" as const, label: `考点闪卡 (${cards.length})` },
                  { id: "quiz" as const, label: "考前自测" },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === tab.id}
                    onClick={() => setMode(tab.id)}
                    className={`btn btn-sm ${mode === tab.id ? "btn-primary" : ""}`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {mode === "materials" && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".pptx,.pdf,.docx,.md,.markdown,.txt,.csv,.xlsx"
                      onChange={handleFileChange}
                      className="hidden"
                    />
                    <button
                      type="button"
                      disabled={uploading}
                      onClick={() => fileInputRef.current?.click()}
                      className="btn btn-sm btn-primary"
                    >
                      {uploading ? "解析保存中…" : "上传课件 (.pptx/.pdf/.docx/.md)"}
                    </button>
                    <button type="button" onClick={() => setAddingNote((v) => !v)} className="btn btn-sm">
                      {addingNote ? "收起笔记框" : "+ 粘贴笔记 / 黑板识图内容"}
                    </button>
                  </div>

                  {addingNote && (
                    <form onSubmit={handleAddNote} className="space-y-2 rounded border border-dashed border-rule bg-desk/40 p-3">
                      <input
                        value={noteTitle}
                        onChange={(e) => setNoteTitle(e.target.value)}
                        placeholder="笔记标题，如 第三章：进程同步与死锁"
                        required
                        className="field text-sm"
                      />
                      <textarea
                        value={noteContent}
                        onChange={(e) => setNoteContent(e.target.value)}
                        rows={4}
                        required
                        placeholder="粘贴老师重点、口述补充或对话识图提取出的板书文本"
                        className="field text-sm"
                      />
                      <button type="submit" disabled={uploading} className="btn btn-sm btn-primary">
                        保存到本课知识空间
                      </button>
                    </form>
                  )}

                  <form onSubmit={handleSearch} className="flex gap-2">
                    <input
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="在本课课件/笔记里搜关键词或知识点…"
                      className="field flex-1 text-sm"
                    />
                    <button type="submit" disabled={searching || !searchQuery.trim()} className="btn btn-sm">
                      {searching ? "检索中…" : "搜课件"}
                    </button>
                  </form>

                  {searchHits && (
                    <div className="space-y-2 rounded border border-dashed border-rule bg-skin/50 p-2.5">
                      <div className="flex items-center justify-between text-xs text-muted">
                        <span>检索到 {searchHits.length} 个相关片段</span>
                        <button type="button" onClick={() => setSearchHits(null)} className="underline">
                          收起结果
                        </button>
                      </div>
                      {searchHits.length === 0 ? (
                        <p className="text-sm text-muted">没有匹配片段，试试换个关键词。</p>
                      ) : (
                        searchHits.map((hit) => (
                          <div key={hit.chunk_id} className="rounded bg-paper p-2 text-sm space-y-1">
                            <p className="text-xs text-muted">
                              来源：{hit.material_title}（相关度 {(hit.score * 100).toFixed(0)}%）
                            </p>
                            <p className="whitespace-pre-wrap leading-relaxed text-ink">{hit.content}</p>
                          </div>
                        ))
                      )}
                    </div>
                  )}

                  {loadingMaterials ? (
                    <p className="text-sm text-muted">正在读取资料…</p>
                  ) : materials.length === 0 ? (
                    <p className="text-sm text-muted">这门课还没传资料，上传一个 .pptx 或 .pdf 课件试试吧～</p>
                  ) : (
                    <ul className="divide-y divide-dashed divide-rule">
                      {materials.map((m) => (
                        <li key={m.id} className="flex items-start justify-between gap-2 py-2.5">
                          <div className="min-w-0 flex-1 space-y-0.5">
                            <p className="font-medium text-sm truncate">
                              <span className="mr-1.5 rounded bg-skin px-1.5 py-0.5 text-[11px]">
                                {m.kind === "file" ? "文件" : "笔记"}
                              </span>
                              {m.title}
                            </p>
                            <p className="text-xs text-muted">
                              {m.filename ? `${m.filename} · ` : ""}
                              {m.char_count} 字
                            </p>
                            {m.summary && <p className="text-xs text-muted line-clamp-2">{m.summary}</p>}
                          </div>
                          <button
                            type="button"
                            onClick={() => void handleDeleteMaterial(m)}
                            className="btn btn-sm shrink-0 text-danger"
                          >
                            删除
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {mode === "cards" && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      value={cardTopic}
                      onChange={(e) => setCardTopic(e.target.value)}
                      placeholder="限定章节或主题（可选）"
                      className="field flex-1 text-sm"
                    />
                    <select
                      value={cardCount}
                      onChange={(e) => setCardCount(Number(e.target.value))}
                      aria-label="生成闪卡张数"
                      className="field w-24 text-sm"
                    >
                      {[4, 6, 8, 10].map((n) => (
                        <option key={n} value={n}>
                          {n} 张
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={generatingCards}
                      onClick={() => void handleGenerateCards()}
                      className="btn btn-sm btn-primary"
                    >
                      {generatingCards ? "AI 提炼中…" : "AI 提炼考点闪卡"}
                    </button>
                  </div>

                  <div className="flex items-center justify-between text-sm">
                    <label className="flex items-center gap-2 text-muted">
                      <input
                        type="checkbox"
                        checked={dueOnly}
                        onChange={(e) => setDueOnly(e.target.checked)}
                        className="size-4 accent-pink"
                      />
                      只看今天到期待复习（SM-2 调度）
                    </label>
                    {cards.length > 0 && (
                      <span className="text-xs text-muted tabular-nums">
                        第 {Math.min(cardIndex + 1, cards.length)} / {cards.length} 张
                      </span>
                    )}
                  </div>

                  {activeCard ? (
                    <div className="rounded border-2 border-ink bg-desk/50 p-4 space-y-3">
                      <div className="flex items-center justify-between gap-2 text-xs text-muted">
                        <span className="rounded bg-pollen px-2 py-0.5 text-ink">
                          {activeCard.tags || "核心考点"}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="tabular-nums">
                            已复习 {activeCard.reps} 次 · 当前间隔 {activeCard.interval_days} 天
                          </span>
                          <button
                            type="button"
                            onClick={() => void handleDeleteCard(activeCard.id)}
                            className="underline text-danger"
                          >
                            删卡
                          </button>
                        </div>
                      </div>

                      <p className="font-display text-lg leading-snug">{activeCard.front}</p>

                      {cardFlipped ? (
                        <div className="space-y-3 border-t border-dashed border-rule pt-3">
                          <p className="text-sm leading-relaxed whitespace-pre-wrap">{activeCard.back}</p>
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            <span className="text-xs text-muted">掌握度：</span>
                            {[
                              { q: 1 as const, label: "没记住（10 分钟后重来）" },
                              { q: 2 as const, label: "有点模糊" },
                              { q: 3 as const, label: "记得住" },
                              { q: 4 as const, label: "秒懂轻松" },
                            ].map((opt) => (
                              <button
                                key={opt.q}
                                type="button"
                                disabled={reviewingCard}
                                onClick={() => void handleReviewCard(activeCard, opt.q)}
                                className={`btn btn-sm ${opt.q === 3 ? "btn-primary" : ""}`}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-2">
                          <button type="button" onClick={() => setCardFlipped(true)} className="btn btn-sm btn-primary">
                            翻看解答与易错点
                          </button>
                          {cards.length > 1 && (
                            <button
                              type="button"
                              onClick={() => {
                                setCardFlipped(false);
                                setCardIndex((i) => (i + 1) % cards.length);
                              }}
                              className="btn btn-sm"
                            >
                              下一张
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted">
                      {dueOnly
                        ? "今天的待复习闪卡全部过完啦！取消勾选「只看今天到期待复习」可浏览全部卡片或重新出卡。"
                        : "还没有闪卡。先在「课件与笔记」上传课件，然后点「AI 提炼考点闪卡」。"}
                    </p>
                  )}
                </div>
              )}

              {mode === "quiz" && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      value={quizTopic}
                      onChange={(e) => setQuizTopic(e.target.value)}
                      placeholder="指定出题章节点（可选）"
                      className="field flex-1 text-sm"
                    />
                    <select
                      value={quizCount}
                      onChange={(e) => setQuizCount(Number(e.target.value))}
                      aria-label="出题数量"
                      className="field w-24 text-sm"
                    >
                      {[3, 4, 6, 8].map((n) => (
                        <option key={n} value={n}>
                          {n} 题
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={generatingQuiz}
                      onClick={() => void handleGenerateQuiz()}
                      className="btn btn-sm btn-primary"
                    >
                      {generatingQuiz ? "出题中…" : "生成考前自测"}
                    </button>
                  </div>

                  {quiz && !gradeResult && (
                    <form onSubmit={handleGradeQuiz} className="space-y-3">
                      {quiz.questions.map((q, idx) => (
                        <div key={q.id} className="rounded border border-dashed border-rule bg-desk/40 p-3 space-y-2">
                          <p className="font-medium text-sm">
                            {idx + 1}. {q.question}
                          </p>
                          {q.type === "choice" && q.options.length > 0 ? (
                            <div className="space-y-1">
                              {q.options.map((opt) => (
                                <label key={opt} className="flex items-start gap-2 text-sm cursor-pointer">
                                  <input
                                    type="radio"
                                    name={`quiz-${q.id}`}
                                    value={opt}
                                    checked={answers[q.id] === opt}
                                    onChange={() => setAnswers((prev) => ({ ...prev, [q.id]: opt }))}
                                    className="mt-1 accent-pink"
                                  />
                                  <span>{opt}</span>
                                </label>
                              ))}
                            </div>
                          ) : (
                            <textarea
                              value={answers[q.id] || ""}
                              onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
                              rows={3}
                              placeholder="写下你的解题思路或核心要点…"
                              className="field text-sm"
                            />
                          )}
                        </div>
                      ))}
                      <button type="submit" disabled={gradingQuiz} className="btn btn-sm btn-primary">
                        {gradingQuiz ? "自动批改中…" : "交卷并 AI 批改"}
                      </button>
                    </form>
                  )}

                  {gradeResult && (
                    <div className="space-y-3">
                      <div className="flex flex-wrap items-center justify-between gap-2 rounded bg-pollen/50 px-3 py-2">
                        <p className="font-display text-lg">
                          得分：{gradeResult.score} / {gradeResult.max_score}（{gradeResult.percent}%）
                        </p>
                        <button type="button" onClick={() => void handleGenerateQuiz()} className="btn btn-sm">
                          再来一套新题
                        </button>
                      </div>
                      <ul className="space-y-2.5">
                        {gradeResult.results.map((r, idx) => (
                          <li key={r.id} className="rounded border border-dashed border-rule bg-paper p-3 text-sm space-y-1">
                            <p className="font-medium">
                              <span className={`mr-1.5 font-bold ${r.correct ? "text-ink" : "text-danger"}`}>
                                {r.correct ? "✓" : "✗"} ({r.score}/{r.max_score})
                              </span>
                              {idx + 1}. {r.question}
                            </p>
                            <p className="text-xs text-muted">你的回答：{r.your_answer || "（未作答）"}</p>
                            <p className="text-xs text-muted">参考答案：{r.expected_answer}</p>
                            <p className="text-xs text-ink">评语：{r.feedback}</p>
                            {r.explanation && <p className="text-xs text-muted">解析：{r.explanation}</p>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
