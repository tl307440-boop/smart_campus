"use client";

import { useEffect, useRef, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { errorDetail, fetchTermReview, type TermReviewData } from "@/lib/api";

function StatBox({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm transition hover:shadow">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold tracking-tight text-foreground">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function TermReviewPane() {
  const [data, setData] = useState<TermReviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchTermReview();
        if (!cancelled) setData(res);
      } catch (err) {
        if (!cancelled) setError(errorDetail(err) || "加载学期回顾失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  function exportImage() {
    if (!cardRef.current) return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // 绘制简易分享长图
    const width = 600;
    const height = 760;
    canvas.width = width;
    canvas.height = height;

    // 背景渐变
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, "#fffaf4");
    grad.addColorStop(1, "#fde3e9");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, width, height);

    // 边框
    ctx.strokeStyle = "#1b1214";
    ctx.lineWidth = 4;
    ctx.strokeRect(16, 16, width - 32, height - 32);

    // 标题
    ctx.fillStyle = "#1b1214";
    ctx.font = "bold 28px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("🎓 我的学期学业小结", width / 2, 70);

    ctx.font = "16px sans-serif";
    ctx.fillStyle = "#666";
    ctx.fillText(`${data?.term || "本学期"} · 第 ${data?.current_week || 1} 周`, width / 2, 105);

    // 分割线
    ctx.strokeStyle = "#eee";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(40, 130);
    ctx.lineTo(width - 40, 130);
    ctx.stroke();

    // 核心指标
    const lessons = data?.total_lessons_estimated ?? 0;
    const courses = data?.total_courses ?? 0;
    const gpa = data?.curr_gpa ? data.curr_gpa.toFixed(2) : (data?.overall_gpa?.toFixed(2) ?? "暂无");

    ctx.fillStyle = "#1b1214";
    ctx.font = "bold 42px sans-serif";
    ctx.fillText(String(lessons), width / 4, 195);
    ctx.font = "bold 42px sans-serif";
    ctx.fillText(String(courses), width / 2, 195);
    ctx.font = "bold 42px sans-serif";
    ctx.fillText(String(gpa), (width * 3) / 4, 195);

    ctx.fillStyle = "#888";
    ctx.font = "14px sans-serif";
    ctx.fillText("累计已上节数", width / 4, 230);
    ctx.fillText("本学期修读门数", width / 2, 230);
    ctx.fillText("当前绩点 GPA", (width * 3) / 4, 230);

    // 趣味画像卡片
    ctx.fillStyle = "rgba(255, 255, 255, 0.75)";
    ctx.fillRect(40, 270, width - 80, 210);
    ctx.strokeRect(40, 270, width - 80, 210);

    ctx.fillStyle = "#1b1214";
    ctx.textAlign = "left";
    ctx.font = "bold 16px sans-serif";
    ctx.fillText("🏃 最忙碌的一天：", 60, 310);
    ctx.font = "15px sans-serif";
    ctx.fillText(data?.busiest_day || "暂无", 195, 310);

    ctx.font = "bold 16px sans-serif";
    ctx.fillText("🌅 最早一节课：", 60, 355);
    ctx.font = "15px sans-serif";
    ctx.fillText(data?.earliest_class || "无", 195, 355);

    ctx.font = "bold 16px sans-serif";
    ctx.fillText("🌙 最晚一节课：", 60, 400);
    ctx.font = "15px sans-serif";
    ctx.fillText(data?.latest_class || "无", 195, 400);

    ctx.font = "bold 16px sans-serif";
    ctx.fillText("🏷️ 学业特色标签：", 60, 445);
    ctx.font = "15px sans-serif";
    ctx.fillText((data?.tags || []).join("  "), 195, 445);

    // 寄语
    ctx.fillStyle = "#ff4d7d";
    ctx.font = "bold 16px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("💬 伴读寄语", width / 2, 530);

    ctx.fillStyle = "#333";
    ctx.font = "italic 15px sans-serif";
    ctx.fillText(data?.cheer_message || "每一份坚持，都在悄悄发芽！", width / 2, 565);

    // 底部水印
    ctx.fillStyle = "#aaa";
    ctx.font = "12px sans-serif";
    ctx.fillText("智悦智慧校园 · 校园桌宠伴读小结", width / 2, 700);

    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = `学期总结-${data?.term || "卡片"}.png`;
    a.click();
  }

  if (loading) {
    return (
      <div>
        <PaneTitle title="学期回顾">期末学业画像与课时总结</PaneTitle>
        <div className="py-12 text-center text-sm text-muted">正在整理你的学期数据... 📊</div>
      </div>
    );
  }

  if (error || !data?.bound) {
    return (
      <div>
        <PaneTitle title="学期回顾">期末学业画像与课时总结</PaneTitle>
        <div className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted">
          {error || "请先在「教务账号」完成绑定，才能为你生成本学期的学业回顾哦～"}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start justify-between">
        <PaneTitle title="学期回顾">期末学业画像与课时总结</PaneTitle>
        <button
          onClick={exportImage}
          className="inline-flex items-center gap-1.5 rounded-lg border border-primary bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/20"
        >
          📸 导出分享图片
        </button>
      </div>

      <div ref={cardRef} className="mt-2 space-y-4">
        {/* 顶部标签 */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-muted/40 p-3 text-xs">
          <span className="font-semibold text-foreground">
            {data.term} · 第 {data.current_week} 周
          </span>
          <div className="flex flex-wrap gap-1.5">
            {data.tags?.map((tag) => (
              <span key={tag} className="rounded-full bg-background px-2.5 py-0.5 font-medium shadow-sm">
                {tag}
              </span>
            ))}
          </div>
        </div>

        {/* 核心统计卡片 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatBox label="累计已上节数" value={data.total_lessons_estimated ?? 0} sub="课表理论排课推算" />
          <StatBox label="修读课程门数" value={data.total_courses ?? 0} sub="本学期在读" />
          <StatBox
            label="当前绩点 GPA"
            value={data.curr_gpa ? data.curr_gpa.toFixed(2) : (data.overall_gpa?.toFixed(2) ?? "暂无")}
            sub={data.gpa_change ? `较上期 ${data.gpa_change > 0 ? "+" : ""}${data.gpa_change}` : "平稳前行"}
          />
          <StatBox label="学业状态" value="冲刺中 🏃" sub="愿你逢考必过" />
        </div>

        {/* 趣味学业画像 */}
        <div className="rounded-xl border border-border bg-card p-4">
          <h3 className="text-sm font-semibold text-foreground">学业作息画像</h3>
          <div className="mt-3 grid gap-2.5 text-xs sm:grid-cols-3">
            <div className="rounded-lg bg-muted/30 p-2.5">
              <span className="text-muted">最忙碌的一天</span>
              <p className="mt-1 font-semibold text-foreground">{data.busiest_day}</p>
            </div>
            <div className="rounded-lg bg-muted/30 p-2.5">
              <span className="text-muted">最早一节课</span>
              <p className="mt-1 font-semibold text-foreground">{data.earliest_class}</p>
            </div>
            <div className="rounded-lg bg-muted/30 p-2.5">
              <span className="text-muted">最晚一节课</span>
              <p className="mt-1 font-semibold text-foreground">{data.latest_class}</p>
            </div>
          </div>
        </div>

        {/* 温馨寄语 */}
        <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 text-center">
          <div className="text-xs font-semibold text-primary">💬 桌宠伴读寄语</div>
          <p className="mt-1.5 text-sm italic text-foreground/90">"{data.cheer_message}"</p>
        </div>
      </div>
    </div>
  );
}
