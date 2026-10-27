"use client";

import { useEffect, useState } from "react";
import { PROVIDERS } from "@/lib/agent-settings";
import {
  LOCAL_EMBEDDING_MODELS,
  type AiConfigView,
  type AiEndpointInput,
  type AiService,
  type EmbeddingInput,
  type JwConfig,
  type JwMode,
  type KeyInput,
  type KeyService,
} from "@/lib/ai-config/schema";
import { type AiServiceName, type AiStatus, errorDetail, fetchAiStatus, testAiService } from "@/lib/api";
import { fetchAiConfig, saveAiConfig } from "@/lib/workspace/client";
import { useToast } from "@/components/Toast";

/** 与 backend/ai_config.py 的默认值一致：留空时后端用的就是这些 */
const DEFAULTS: Record<AiService, { baseUrl: string; model: string }> = {
  chat: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  reader: { baseUrl: "https://api.moonshot.cn/v1", model: "kimi-k2-0905-preview" },
  // 识图没有默认值：三项都要学校自己填，没填就不开放图片提问
  vision: { baseUrl: "", model: "" },
};

/** 都是 OpenAI 兼容接口、支持看图的模型；点一下只是帮着填网址和模型 ID，密钥还得自己填 */
const VISION_PRESETS = [
  { label: "智谱 GLM-4V-Flash", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4v-flash" },
  { label: "Kimi 视觉", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k-vision-preview" },
  { label: "通义千问 VL", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-vl-plus" },
] as const;

const ROWS: { service: AiServiceName; name: string; use: string }[] = [
  { service: "chat", name: "对话模型", use: "判断问题、回答、情绪分析" },
  { service: "reader", name: "长文档精读", use: "资料不多时整篇读，摘出相关段落" },
  { service: "vision", name: "图片提问", use: "学生拍课表、通知、表格来问；单独的识图模型把图读成文字，图片不保存" },
  { service: "embedding", name: "知识库向量化", use: "智谱 embedding-3，把资料切成片段再检索" },
  { service: "jw", name: "教务查询", use: "学生绑定账号后查课表、考试、成绩、空教室" },
  { service: "news", name: "学校通知", use: "从官网、教务处的列表页读最新通知" },
  { service: "map", name: "地图问路", use: "新生找教学楼、食堂、校门，规划步行路线" },
  { service: "search", name: "联网搜索", use: "学校资料里没有的最新信息" },
];

const ENDPOINT_TITLE: Record<AiService, string> = { chat: "对话模型", reader: "长文档精读", vision: "图片提问（识图模型）" };

const SOURCE_TEXT = { school: "学校配置", platform: "平台默认" } as const;
const JW_MODE_TEXT: Record<JwMode, string> = { off: "关闭", demo: "演示数据", zhengfang: "正方教务" };

const KEY_HELP: Record<KeyService, { title: string; placeholder: string; link: string; linkText: string; tips: string }> = {
  map: {
    title: "高德地图 Key",
    placeholder: "32 位字母数字",
    link: "https://console.amap.com/dev/key/app",
    linkText: "去高德开放平台申请",
    tips: "控制台 → 应用管理 → 创建应用 → 添加 Key，服务平台选「Web 服务」。个人实名认证后免费：路径规划每月 15 万次、地点搜索每月 5000 次。",
  },
  search: {
    title: "博查搜索 Key",
    placeholder: "sk-…",
    link: "https://open.bochaai.com/",
    linkText: "去博查开放平台申请",
    tips: "注册后在「API KEY 管理」里创建。新用户有免费额度，用完约 3.6 分一次，预付费。",
  },
};

type Drafts = Record<AiService, AiEndpointInput> &
  Record<KeyService, KeyInput> & { jw: JwConfig; newsUrls: string; embedding: EmbeddingInput };

type TestState = { pending: boolean; ok?: boolean; text?: string };

function toDrafts(view: AiConfigView): Drafts {
  const draft = (service: AiService) => ({
    baseUrl: view[service].baseUrl,
    model: view[service].model,
    apiKey: "",
    clearKey: false,
  });
  return {
    chat: draft("chat"),
    reader: draft("reader"),
    vision: draft("vision"),
    map: { apiKey: "", clearKey: false },
    search: { apiKey: "", clearKey: false },
    jw: { ...view.jw },
    newsUrls: view.newsUrls.join("\n"),
    embedding: {
      provider: view.embedding.provider,
      model: view.embedding.model,
      baseUrl: view.embedding.baseUrl,
      apiKey: "",
      clearKey: false,
    },
  };
}

function statusText(service: AiServiceName, status: AiStatus[AiServiceName] | undefined): string {
  if (!status) return "读取中…";
  if (!status.configured || !status.source) {
    if (service === "embedding") return "未配置：请管理员填写智谱密钥";
    if (service === "vision") return "未开通：学生暂时不能附图提问";
    if (service === "jw") return "关闭";
    return "未配置";
  }
  const detail = service === "jw" ? JW_MODE_TEXT[status.model as JwMode] ?? status.model : status.model;
  return `${SOURCE_TEXT[status.source]} · ${detail}`;
}

function KeyForm({
  service,
  view,
  draft,
  onChange,
}: {
  service: KeyService;
  view: AiConfigView[KeyService];
  draft: KeyInput;
  onChange: (patch: Partial<KeyInput>) => void;
}) {
  const help = KEY_HELP[service];
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">
        {help.tips}{" "}
        <a href={help.link} target="_blank" rel="noreferrer" className="underline decoration-dashed underline-offset-2">
          {help.linkText}
        </a>
      </p>
      <label className="block">
        <span className="field-label">{help.title}</span>
        <input
          type="password"
          value={draft.apiKey}
          onChange={(event) => onChange({ apiKey: event.target.value, clearKey: false })}
          autoComplete="off"
          placeholder={view.hasKey ? `已保存 ···${view.keyHint || "****"}，留空不改` : `${help.placeholder}，先空着也行`}
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
          清除已保存的 Key
        </label>
      )}
    </div>
  );
}

function JwForm({ draft, onChange }: { draft: JwConfig; onChange: (patch: Partial<JwConfig>) => void }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="教务查询方式">
        {(Object.keys(JW_MODE_TEXT) as JwMode[]).map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={draft.mode === mode}
            onClick={() => onChange({ mode })}
            className={`btn btn-sm ${draft.mode === mode ? "btn-primary" : ""}`}
          >
            {JW_MODE_TEXT[mode]}
          </button>
        ))}
      </div>
      <p className="text-sm text-muted">
        {draft.mode === "demo"
          ? "不连真实教务，所有学生看到同一份样例课表，用来演示。"
          : draft.mode === "zhengfang"
            ? "学生在「工具 → 教务账号」用统一身份认证账号绑定，只保存登录凭证、不保存密码，只做查询。正式开放前请取得教务处、信息中心同意。"
            : "关闭后助手不会查个人课表、考试和成绩。"}
      </p>
      {draft.mode === "zhengfang" && (
        <>
          <label className="block">
            <span className="field-label">统一身份认证（CAS）地址</span>
            <input
              value={draft.casUrl}
              onChange={(event) => onChange({ casUrl: event.target.value })}
              placeholder="https://cas.xxx.edu.cn/cas"
              className="field"
            />
          </label>
          <label className="block">
            <span className="field-label">正方教务地址</span>
            <input
              value={draft.baseUrl}
              onChange={(event) => onChange({ baseUrl: event.target.value })}
              placeholder="https://jwxt.xxx.edu.cn/jwglxt"
              className="field"
            />
          </label>
        </>
      )}
    </div>
  );
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

function EmbeddingForm({
  view,
  draft,
  onChange,
}: {
  view: AiConfigView["embedding"];
  draft: EmbeddingInput;
  onChange: (patch: Partial<EmbeddingInput>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        和对话模型一样，写在这个管理员账号里，加密保存。填了就用学校自己的智谱密钥，不填用服务器上的。向量计算在智谱那边，这台机器只存切好的片段。
      </p>
      <label className="block">
        <span className="field-label">智谱 API 密钥（embedding-3）</span>
        <input
          type="password"
          value={draft.apiKey}
          onChange={(event) => onChange({ apiKey: event.target.value, clearKey: false, provider: "zhipu" })}
          autoComplete="off"
          placeholder={view.hasKey ? `已保存 ···${view.keyHint || "****"}，留空不改` : "留空则用服务器上的智谱密钥"}
          className="field"
        />
      </label>
      {view.hasKey && (
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={draft.clearKey}
            onChange={(event) => onChange({ clearKey: event.target.checked, apiKey: "", provider: "" })}
            className="size-4 accent-pink"
          />
          清除已保存的密钥（改用服务器上的）
        </label>
      )}
      <p className="text-sm text-muted">
        开源模型先留着，暂不启用：{LOCAL_EMBEDDING_MODELS.map((item) => item.label).join("、")}。不打进镜像，也不会在服务器上加载。
      </p>
    </div>
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
  const isVision = service === "vision";
  const presets: readonly { label: string; baseUrl: string; model: string }[] = isVision ? VISION_PRESETS : Object.values(PROVIDERS);
  return (
    <div className="space-y-3">
      {isVision && (
        <p className="text-sm text-muted">
          网址、模型、密钥三项都填了，学生才会在输入框旁看到「附图」。学生拍的图会发给这里选的服务商来读，学校这边不保存图片；
          图里的个人证件号会被要求不转写。选之前请确认这家服务商的数据政策你们能接受。
        </p>
      )}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="常用服务商，点一下填好网址和模型">
        {presets.map((preset) => (
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
        <span className="field-label">
          服务地址<span className="ml-2 text-[0.8125rem]">Base URL</span>
        </span>
        <input
          value={draft.baseUrl}
          onChange={(event) => onChange({ baseUrl: event.target.value })}
          placeholder={isVision ? "必填，如 https://open.bigmodel.cn/api/paas/v4" : `留空用 ${fallback.baseUrl}`}
          className="field"
        />
      </label>
      <label className="block">
        <span className="field-label">模型 ID</span>
        <input
          value={draft.model}
          onChange={(event) => onChange({ model: event.target.value })}
          placeholder={isVision ? "必填，要选支持看图的模型，如 glm-4v-flash" : `留空用 ${fallback.model}`}
          className="field"
        />
      </label>
      <label className="block">
        <span className="field-label">
          密钥<span className="ml-2 text-[0.8125rem]">API Key，在服务商后台复制，这里只存加密后的内容</span>
        </span>
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
          {isVision ? "清除已保存的密钥（关闭图片提问）" : "清除已保存的密钥（改用平台默认）"}
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
  const toast = useToast();

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
      const newsUrls = drafts.newsUrls.split("\n").map((line) => line.trim()).filter(Boolean);
      const saved = await saveAiConfig({ ...drafts, newsUrls });
      setView(saved);
      setDrafts(toDrafts(saved));
      // 后端把学校配置缓存 60 秒，测试接口会先丢掉缓存，所以先测再读状态
      for (const service of ["chat", "reader", "vision", "map", "search"] as const) {
        if (saved[service].hasKey || status?.[service].configured) await runTest(service);
      }
      await runTest("embedding");
      if (saved.newsUrls.length) await runTest("news");
      if (saved.jw.mode !== "off") await runTest("jw");
      setStatus(await fetchAiStatus());
      toast.success("已保存，每项服务的测试结果在上面");
    } catch (err) {
      toast.error(`没保存上：${errorDetail(err)}`);
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
                  <span className="ml-2 text-sm text-muted">{statusText(service, item)}</span>
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
          <fieldset className="mt-4 border-t border-dashed border-rule pt-3">
            <legend className="pr-2 font-display text-base">知识库向量化</legend>
            <EmbeddingForm
              view={view.embedding}
              draft={drafts.embedding}
              onChange={(patch) =>
                setDrafts((prev) => prev && { ...prev, embedding: { ...prev.embedding, ...patch } })
              }
            />
          </fieldset>
          {(["chat", "reader", "vision"] as const).map((service) => (
            <fieldset key={service} className="mt-4 border-t border-dashed border-rule pt-3">
              <legend className="pr-2 font-display text-base">{ENDPOINT_TITLE[service]}</legend>
              <EndpointForm
                service={service}
                view={view[service]}
                draft={drafts[service]}
                onChange={(patch) => setDrafts((prev) => prev && { ...prev, [service]: { ...prev[service], ...patch } })}
              />
            </fieldset>
          ))}
          <fieldset className="mt-4 border-t border-dashed border-rule pt-3">
            <legend className="pr-2 font-display text-base">教务查询</legend>
            <JwForm
              draft={drafts.jw}
              onChange={(patch) => setDrafts((prev) => prev && { ...prev, jw: { ...prev.jw, ...patch } })}
            />
          </fieldset>
          <fieldset className="mt-4 border-t border-dashed border-rule pt-3">
            <legend className="pr-2 font-display text-base">学校通知来源</legend>
            <label className="block">
              <span className="field-label">列表页网址，一行一个（官网首页、教务处首页、通知公告栏目都行）</span>
              <textarea
                value={drafts.newsUrls}
                onChange={(event) => setDrafts((prev) => prev && { ...prev, newsUrls: event.target.value })}
                rows={3}
                placeholder={"https://www.xxx.edu.cn/\nhttps://jwc.xxx.edu.cn/"}
                className="field"
              />
            </label>
          </fieldset>
          {(["map", "search"] as const).map((service) => (
            <fieldset key={service} className="mt-4 border-t border-dashed border-rule pt-3">
              <legend className="pr-2 font-display text-base">{service === "map" ? "地图问路" : "联网搜索"}</legend>
              <KeyForm
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
