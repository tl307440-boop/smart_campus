"""
graph_app.py - 智悦校园 LangGraph 版本（含意图路由）

本文件使用 LangGraph 框架将原有的线性 RAG 流程重构为"状态图"（StateGraph）。
核心创新：引入意图路由节点，先判断用户问题类型，再决定是否走检索流程。

图拓扑结构：

                    ┌─── intent == "RAG" ───→ retrieve_node ───┐
    START → intent_node ┤                                       ├→ generate_node → emotion_node → END
                    └─── intent == "DIRECT" ───────────────────┘

数据在节点之间通过 GraphState 字典流动：

    用户提问
        │
        ▼
   ┌─────────────┐    intent ("RAG"/"DIRECT")
   │ intent_node  │ ──────────────────────────┐
   │ (意图判定)    │                            │
   └─────────────┘                            │
        │                                     │
        │ intent == "RAG"                     │ intent == "DIRECT"
        ▼                                     │ (跳过检索，节省 Token)
   ┌─────────────┐    context (知识片段)       │
   │ retrieve_node│ ──────────────────────┐   │
   │ (检索节点)    │                        │   │
   └─────────────┘                        │   │
                                           ▼   ▼
                                   ┌─────────────┐    final_answer
                                   │ generate_node│ ────────────────> 输出给用户
                                   │ (生成节点)    │
                                   └─────────────┘

作者: 智悦校园团队
日期: 2026-05
"""

import os
import re
import sys
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.redis import RedisSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

# ============================================================
# 第一步：导入已有的服务类（不修改它们，只使用它们）
# ============================================================
# 将 backend 目录加入 Python 路径，确保 import 能找到同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import comfort
import memory_store
from ai_config import ModelEndpoint, chat_endpoint, client_for, reader_endpoint, scrub
from env_config import chroma_persist_dir, load_backend_env
from kimi_extract_service import KimiExtractService
from knowledge import KIMI_CORPUS_BUDGET
from llm_service import FAILED_PREFIX, NOT_CONFIGURED, DeepSeekService, prompt_extras
from logging_config import get_logger
from tenancy import GLOBAL_TENANT
from vector_service import VectorRAGService
from workspace import WorkspaceSettings, workspace_settings

logger = get_logger(__name__)

load_backend_env()


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# ============================================================
# 第二步：定义状态字典 (State)
# ============================================================
# LangGraph 的核心：所有节点共享此状态字典。
# 每个节点从 state 中读取输入，将输出写回 state，
# 数据就是通过这个字典在节点之间流动的。

class GraphState(TypedDict):
    """
    LangGraph 的核心状态字典。
    所有节点共享此状态，每个节点从中读取输入、写入输出。

    字段说明:
        messages           -- 对话历史，格式: [{"role": "user", "content": "..."}]
        user_question      -- 当前用户问题（从最新消息提取，供节点直接使用）
        intent             -- 意图分类结果: "RAG"（需要检索）或 "DIRECT"（直接回答）
        context            -- 检索到的上下文（retrieve_node 的输出）
        current_doc_content -- 已加载的文档全文（供 Kimi 路径使用）
        route_strategy     -- 检索子路由: "auto" / "kimi" / "vectordb"
        final_answer       -- 最终回答（generate_node 的输出）
        emotion_data       -- 情感分析结果（后续扩展用）
    """
    # ---- 对话历史（可含 context / emotion，供 UI 从 Redis 恢复）----
    messages: list[dict[str, Any]]

    # ---- 当前用户问题 ----
    user_question: str

    # ---- 意图分类（intent_node 的输出）----
    intent: str

    # ---- 检索到的上下文（retrieve_node 的输出）----
    context: str

    # ---- 文档内容（供 Kimi 使用）----
    current_doc_content: str

    # ---- 检索子路由策略 ----
    route_strategy: Literal["auto", "kimi", "vectordb"]

    # ---- 提问者所属学校，检索只在「全局文档 + 本校文档」里进行 ----
    tenant_id: str

    # ---- 提问者 id，长期记忆的归属键；嵌入挂件的访客为空串，不读写记忆 ----
    user_id: str

    # ---- 本轮引用的来源（retrieve_node 的输出），前端渲染成来源卡片 ----
    sources: list[dict[str, Any]]

    # ---- 最终回答（generate_node 的输出）----
    final_answer: str

    # ---- 情感分析结果（后续扩展）----
    emotion_data: dict[str, Any] | None

    # ---- 本轮的求助卡片（comfort.safety_card），没有则为 None ----
    safety: dict[str, Any] | None


# ============================================================
# 第三步：懒加载服务实例（首次使用时才初始化，避免 import 时网络/SSL 报错）
# ============================================================
# 使用惰性初始化模式：模块加载时不创建实例，第一次调用节点时才创建。
# 这样即使网络不通或 API Key 未配置，import graph_app 也不会报错。

_deepseek_brain = None
_kimi_eye = None
_vector_db = None
_checkpointer = None
_compiled_graph = None

# 会话里保留的对话轮数（一轮 = user + assistant 两条）
MAX_STORED_TURNS = _env_int("MAX_STORED_TURNS", 30)
# 每次请求实际发给模型的轮数，比存储的更少，用来压 token 成本
MAX_PROMPT_TURNS = _env_int("MAX_PROMPT_TURNS", 10)
# 情感分析顺带抽出的个人事实是否自动写进长期记忆
MEMORY_AUTO_EXTRACT = os.getenv("MEMORY_AUTO_EXTRACT", "true").strip().lower() in {"1", "true", "yes", "on"}
# 不连任何模型、全部用模拟数据（本地演示）。线上必须关着，否则学生拿到的是假回答
LLM_MOCK = os.getenv("LLM_MOCK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _delta_emitter():
    """返回一个把生成中的文本片段推给调用方的函数。

    走 graph.stream(stream_mode=["updates", "custom"]) 时片段会实时到达前端；
    走 graph.invoke() 时 LangGraph 给的是空写入器，片段被丢弃，不影响返回值。
    """
    writer = get_stream_writer()

    def emit(text: str) -> None:
        if text:
            writer({"delta": text})

    return emit


def _append_turn_messages(
    state: GraphState,
    question: str,
    final_answer: str,
    context: str = "",
    sources: list[dict[str, Any]] | None = None,
    safety: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """将本轮 user/assistant 写入 messages；assistant 附带 context/sources 便于 UI 恢复。

    只保留最近 MAX_STORED_TURNS 轮：不裁剪的话 Redis 里的 state 会随会话
    无限膨胀，每轮读写的快照也越来越大。
    """
    updated_messages = list(state.get("messages", []))
    updated_messages.append({"role": "user", "content": question})
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": final_answer,
        "context": context or "",
        "sources": sources or [],
    }
    if safety:
        assistant["safety"] = safety
    updated_messages.append(assistant)
    return updated_messages[-(MAX_STORED_TURNS * 2):]


# 来源卡片里的片段只是预览，完整内容仍在 context 里
SOURCE_SNIPPET_CHARS = 240
_KIMI_SOURCE_MARK = re.compile(r"--- 文档来源: (.+?) ---")


def _chunk_sources(chunks) -> list[dict[str, Any]]:
    """把重排后的片段转成前端来源卡片用的结构。分数优先用重排分，没有则用向量相似度。"""
    sources: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.metadata or {}
        rerank = meta.get("rerank_score")
        sources.append({
            "index": index,
            "source": meta.get("source", "未知文件"),
            "snippet": chunk.text[:SOURCE_SNIPPET_CHARS],
            "score": round(float(chunk.vector_score), 4),
            "rerank_score": None if rerank is None else round(float(rerank), 4),
        })
    return sources


def _corpus_sources(doc_content: str) -> list[dict[str, Any]]:
    """Kimi 精读路径读的是整篇短文合集，只能给出文档名，没有片段与分数。"""
    names = list(dict.fromkeys(_KIMI_SOURCE_MARK.findall(doc_content)))
    return [
        {"index": i, "source": name, "snippet": "", "score": None, "rerank_score": None}
        for i, name in enumerate(names, start=1)
    ]


def _user_memories(state: GraphState, question: str) -> list[str]:
    user_id = state.get("user_id") or ""
    if not user_id:
        return []
    try:
        return memory_store.relevant_memories(state.get("tenant_id") or GLOBAL_TENANT, user_id, question)
    except Exception as e:
        logger.warning(f"[记忆] 读取失败，本轮不带记忆: {e}")
        return []


def _settings(state: GraphState) -> WorkspaceSettings:
    return workspace_settings(state.get("tenant_id") or GLOBAL_TENANT)


def _chat_endpoint(state: GraphState) -> ModelEndpoint | None:
    return chat_endpoint(state.get("tenant_id") or GLOBAL_TENANT)


def _refusal(settings: WorkspaceSettings) -> str:
    return (
        f"抱歉，{settings.assistant_name}在本校资料里没有找到与这个问题相关的内容。"
        "可以换个说法，或者联系学校相关部门确认。"
    )


def recent_history(messages: list[dict[str, Any]], max_turns: int) -> list[dict[str, Any]]:
    """取最近若干轮的纯对话内容，作为发给模型的上下文。

    历史全量发出去的话，token 成本随轮次线性上涨，聊够久还会撑破上下文窗口。
    """
    trimmed: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            trimmed.append({"role": role, "content": content})
    return trimmed[-(max_turns * 2):]


def get_deepseek() -> DeepSeekService:
    """获取 DeepSeek 服务单例（首次调用时创建）"""
    global _deepseek_brain
    if _deepseek_brain is None:
        _deepseek_brain = DeepSeekService(use_mock=LLM_MOCK)
    return _deepseek_brain


def get_kimi() -> KimiExtractService:
    """获取 Kimi 服务单例（首次调用时创建）"""
    global _kimi_eye
    if _kimi_eye is None:
        _kimi_eye = KimiExtractService(use_mock=LLM_MOCK)
    return _kimi_eye


def get_vector_db() -> VectorRAGService:
    """获取 VectorDB 服务单例（首次调用时创建，失败则返回 None）"""
    global _vector_db
    if _vector_db is None:
        try:
            _vector_db = VectorRAGService(persist_directory=chroma_persist_dir())
        except Exception as e:
            logger.error(f"[VectorDB] 初始化失败: {e}")
            return None
    return _vector_db


def get_checkpointer() -> RedisSaver:
    """
    获取 Redis Checkpointer 单例（首次调用时连接 Redis）。

    Redis Checkpointer 的作用:
        - 每次 graph.invoke() 执行完毕后，自动将整个 State 快照存入 Redis
        - 下次 invoke 时，如果传入相同的 thread_id，自动从 Redis 恢复上次的 State
        - 实现多轮对话记忆持久化

    Redis 连接配置:
        - 默认连接 localhost:6379
        - 可通过环境变量 REDIS_URL 覆盖，如: redis://password@host:port/db
    """
    global _checkpointer
    if _checkpointer is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        logger.info(f"[Redis] 正在连接 Redis: {redis_url}")
        _checkpointer = RedisSaver(redis_url)
        # setup() 会在 Redis 中创建必要的索引结构（首次必须调用）
        _checkpointer.setup()
        logger.info("[Redis] 连接成功，Checkpointer 就绪！")
    return _checkpointer


# ============================================================
# 第四步：定义节点函数 (Node Functions)
# ============================================================

def intent_node(state: GraphState) -> dict:
    """
    【意图判定节点】- 图的入口，负责判断用户问题的类型。

    输入:  state["user_question"] -- 用户当前问题

    输出:  {"intent": "RAG" 或 "DIRECT"}

    判定逻辑:
        - "RAG"    = 教务规章类问题，需要查阅校园手册/知识库（走检索流程）
        - "DIRECT" = 日常闲聊/通用知识，无需检索，直接由大模型回答

    数据流:
        user_question ──> 调用 DeepSeek API（非流式，极速）──> 二分类结果
                                                          └──> state["intent"]
    """
    question = state["user_question"]
    logger.info(f"\n{'='*50}")
    logger.info(f"[意图判定] 当前问题: \"{question}\"")

    # --- 高危表达：不等模型，直接走陪伴 + 求助卡片 ---
    if comfort.is_crisis(question):
        logger.warning("[意图判定] 命中高危表达，走危机陪伴")
        return {"intent": "CRISIS"}

    # --- 使用 DeepSeek 做极速意图分类 ---
    # 非流式调用（不带 stream=True），直接获取完整响应，速度快
    system_prompt = """
你是一个意图分类器。请判断用户的问题属于以下哪个类别：

1. 如果问题涉及【校园教务、规章制度、奖学金、学籍管理、学生手册、课程安排、考试规定、宿舍管理、图书馆规则】等需要查阅学校资料才能准确回答的内容，返回：RAG

2. 如果用户在【倾诉情绪、诉说压力或烦恼、心情低落、人际或感情困扰】，想被倾听和安慰，返回：COMFORT

3. 如果问题是【日常闲聊、打招呼、自我介绍、通用知识】等不需要查阅学校资料就能回答的内容，返回：DIRECT

只返回一个词：RAG、COMFORT 或 DIRECT。不要返回任何其他内容。
"""

    endpoint = None if get_deepseek().use_mock else _chat_endpoint(state)

    # --- Mock 模式或没配对话模型：用关键词规则做快速分类，不调用 API ---
    if endpoint is None:
        logger.info("[意图判定] 无可用对话模型（或 Mock 模式），使用关键词规则分类")
        # 定义教务相关的关键词列表
        rag_keywords = [
            "奖学金", "助学金", "学籍", "学分", "绩点", "考试", "挂科", "补考",
            "重修", "选课", "课表", "成绩", "毕业", "学位", "休学", "退学",
            "转专业", "辅修", "实习", "论文", "答辩", "宿舍", "门禁", "图书馆",
            "借书", "校园卡", "食堂", "学费", "缴费", "评优", "入党", "军训",
            "学生手册", "校规", "违纪", "处分", "请假", "出勤", "辅导员",
            "教务", "招生", "录取", "报到", "注册", "校历", "放假", "开学",
            "国家奖学金", "国家励志奖学金", "助学贷款", "勤工助学",
        ]
        # 带明显情绪词先当倾诉；再看教务关键词
        if comfort.looks_like_venting(question):
            intent = "COMFORT"
        else:
            intent = "RAG" if any(kw in question for kw in rag_keywords) else "DIRECT"

    else:
        # --- 真实 API 模式：调用对话模型做意图分类 ---
        try:
            response = client_for(endpoint).chat.completions.create(
                model=endpoint.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                temperature=0.1,  # 极低随机性，确保分类稳定
                max_tokens=10,    # 只需一个词，限制 token 消耗
            )
            raw_result = response.choices[0].message.content.strip().upper()
            intent = "RAG" if "RAG" in raw_result else "COMFORT" if "COMFORT" in raw_result else "DIRECT"

        except Exception as e:
            # API 调用失败时，默认走 RAG 路径（保守策略）；明显在倾诉的除外
            intent = "COMFORT" if comfort.looks_like_venting(question) else "RAG"
            logger.error(f"[意图判定]（{endpoint.label}）API 调用失败: {scrub(str(e), endpoint)}，按 {intent} 处理")

    logger.info(f"[意图判定] 判定意图为: {intent}")

    return {"intent": intent}


def route_query(state: GraphState) -> str:
    """
    【条件路由函数】- 根据意图决定下一个执行的节点。

    这不是一个普通的节点函数，而是 LangGraph 的条件边回调函数。
    它读取 state["intent"]，返回目标节点的名称。

    输入:  state["intent"] -- intent_node 的输出

    输出:  字符串 -- "retrieve" 或 "generate"（对应节点名称）

    数据流:
        intent ──> 判断 ──> "RAG"    → 返回 "retrieve"（走检索节点）
                        ──> "DIRECT" → 返回 "generate"（跳过检索，直连生成节点）
    """
    intent = state.get("intent", "RAG")

    # 高危表达任何设置下都直接回应；「只按资料回答」的学校不走闲聊和倾诉：一律先检索，查不到就拒答
    if intent == "CRISIS":
        target = "generate"
    elif intent == "RAG" or _settings(state).query_only:
        target = "retrieve"
    else:
        target = "generate"

    logger.info(f"[路由决策] 意图={intent} → 即将走向: {target}_node")

    return target


def retrieve_node(state: GraphState) -> dict:
    """
    【检索节点】- 根据路由策略，从 Kimi 或 VectorDB 获取与问题相关的上下文。

    仅当 intent == "RAG" 时才会执行此节点。

    输入:  state["user_question"]       -- 用户当前问题
           state["current_doc_content"] -- 已加载的文档全文（Kimi 路径需要）
           state["route_strategy"]      -- 路由策略

    输出:  {"context": "...", "sources": [...]}  -- 知识片段 + 结构化来源（前端来源卡片）

    数据流:
        user_question ──┬── route_strategy == "kimi" 或 (auto 且文档短)
        │               │       └──> KimiExtractService.extract_knowledge(doc, question)
        │               │               └──> 返回相关段落文本
        │               │
        │               └── route_strategy == "vectordb" 或 (auto 且文档长)
        │                       └──> VectorRAGService.search_chunks(question, top_k, min_score)
        │                               ├── 宽召回候选 Chunk + BGE CrossEncoder 重排序
        │                               └── top_k / min_score 取自本校工作区设置
        │
        └──> state["context"] / state["sources"] = 检索结果（供 generate_node 与 done 事件使用）
    """
    question = state["user_question"]
    strategy = state.get("route_strategy", "auto")
    doc_content = state.get("current_doc_content", "")
    tenant_id = state.get("tenant_id") or GLOBAL_TENANT

    logger.info(f"[检索节点] 开始检索，路由策略: {strategy}，租户: {tenant_id}")

    settings = workspace_settings(tenant_id)
    context = ""
    sources: list[dict[str, Any]] = []

    try:
        # 预算判定在 knowledge.short_corpus 里已经做过一次（超预算会返回空串），
        # 这里再兜一道，防止 ask_question 直接传进来的超长 doc_content。
        if strategy == "kimi" or (
            strategy == "auto" and 0 < len(doc_content) <= KIMI_CORPUS_BUDGET
        ):
            # --- Kimi 小文档路径 ---
            # 直接将全文交给 Kimi 阅读，让它摘录与问题相关的段落；没配精读模型或没摘到时落到向量检索
            logger.info(f"[检索节点] 走 Kimi 精读路径 (文档长度: {len(doc_content)} 字)")
            context = get_kimi().extract_knowledge(doc_content, question, endpoint=reader_endpoint(tenant_id))
            if context:
                sources = _corpus_sources(doc_content)
        if not context:
            # --- 向量数据库路径 ---
            # 使用智谱 embedding-3 向量化问题，在 ChromaDB 中检索最相似的片段
            logger.info("[检索节点] 走向量数据库检索路径 (ChromaDB)")
            vdb = get_vector_db()
            if vdb is not None:
                chunks = vdb.search_chunks(
                    question,
                    tenant_id,
                    top_k=settings.top_k,
                    min_score=settings.similarity_threshold,
                )
                context = vdb.format_context(chunks)
                sources = _chunk_sources(chunks)
            else:
                logger.warning("[检索节点] VectorDB 不可用，跳过检索")
    except Exception as e:
        logger.error(f"[检索节点] 检索失败: {e}")
        context = ""
        sources = []

    logger.info(f"[检索节点] 检索完成，上下文长度: {len(context)} 字，来源 {len(sources)} 条")

    return {"context": context, "sources": sources}


def generate_node(state: GraphState) -> dict:
    """【生成节点】见 _generate。state 存在 Redis 里，每轮都写一次 safety，上一轮的求助卡片才不会留到这一轮。"""
    out = _generate(state)
    out.setdefault("safety", None)
    return out


def _comfort_answer(state: GraphState, question: str, settings: WorkspaceSettings, memories: list[str], crisis: bool) -> dict:
    """倾诉与高危两种情况：用陪伴提示词带上历史多轮回应。高危时一定附求助卡片，模型不可用也给固定回复。"""
    emit = _delta_emitter()
    contact = settings.counseling_contact
    card = comfort.safety_card(contact) if crisis else None
    endpoint = None if get_deepseek().use_mock else _chat_endpoint(state)
    final_answer = ""
    if get_deepseek().use_mock and not crisis:
        final_answer = f"【{settings.assistant_name}】（模拟回复）听起来你最近不太好受，愿意和我多说说吗？"
        emit(final_answer)
    if endpoint is not None:
        system_prompt = comfort.comfort_prompt(settings.assistant_name, crisis, contact) + prompt_extras(settings.persona, memories)
        api_messages = (
            [{"role": "system", "content": system_prompt}]
            + recent_history(state.get("messages", []), MAX_PROMPT_TURNS)
            + [{"role": "user", "content": question}]
        )
        parts: list[str] = []
        try:
            for piece in get_deepseek().stream_chat(api_messages, endpoint, temperature=0.7):
                parts.append(piece)
                emit(piece)
            final_answer = "".join(parts)
        except Exception as e:
            logger.error(f"[陪伴]（{endpoint.label}）回复失败: {scrub(str(e), endpoint)}")
            # 高危时不把报错甩给学生，下面换成固定回复
            final_answer = "" if crisis else f"{FAILED_PREFIX}（{endpoint.label}）{scrub(str(e), endpoint)}"
    if not final_answer.strip():
        final_answer = comfort.crisis_fallback(contact) if crisis else NOT_CONFIGURED
        emit(final_answer)
    logger.info(f"[生成节点] {'危机' if crisis else '倾诉'}陪伴完成，长度: {len(final_answer)} 字")
    updated_messages = _append_turn_messages(state, question, final_answer, safety=card)
    return {"final_answer": final_answer, "messages": updated_messages, "safety": card}


def _generate(state: GraphState) -> dict:
    """
    【生成节点】- 调用 DeepSeek 大模型生成最终回答。

    此节点兼容两种场景:
        1. 有 context（来自 retrieve_node）→ 基于参考资料严谨回答
        2. 无 context（DIRECT 路径跳过检索）→ 以校园助手身份拟人化对话

    输入:  state["context"]        -- 检索到的知识片段（可能为空）
           state["user_question"]  -- 用户当前问题
           state["intent"]         -- 意图类型（用于日志）

    输出:  {"final_answer": "..."}  -- DeepSeek 生成的完整回答

    数据流:
        context (知识片段) ──┐
                             ├──> DeepSeekService.chat_with_context(context, question)
        user_question ───────┘         │
                                       ├── 拼接 system prompt（含参考资料）
                                       ├── 调用 deepseek-chat API（stream=True）
                                       └── 拼接所有 chunk 得到完整回答
                                             │
                                             └──> state["final_answer"] = 完整回答文本

    注意: chat_with_context 是一个 generator 函数（使用 yield 逐块返回）。
          在 Graph 节点内部，我们需要消费整个 generator 拼接成完整字符串，
          因为 LangGraph 节点必须返回确定性的 state 更新。
    """
    context = state.get("context", "")
    question = state["user_question"]
    intent = state.get("intent", "RAG")
    settings = _settings(state)
    name = settings.assistant_name
    memories = _user_memories(state, question)

    logger.info(f"[生成节点] 意图={intent}，上下文长度={len(context)} 字，记忆 {len(memories)} 条")

    if intent == "CRISIS" or (intent == "COMFORT" and not settings.query_only):
        return _comfort_answer(state, question, settings, memories, crisis=intent == "CRISIS")

    emit = _delta_emitter()

    # 只按资料回答时每轮都检索过，context 一定是本轮的；否则 DIRECT 轮的 context 是上一轮残留
    use_context = bool(context) and (intent == "RAG" or settings.query_only)

    # --- 场景零：只按资料回答，但没检索到内容 → 直接拒答，不调用模型 ---
    if settings.query_only and not use_context:
        final_answer = _refusal(settings)
        emit(final_answer)
        updated_messages = _append_turn_messages(state, question, final_answer)
        logger.info("[生成节点] 只按资料回答模式，未检索到内容，已拒答")
        return {"final_answer": final_answer, "messages": updated_messages}

    # --- 场景一：DIRECT 路径（无上下文，直接对话）---
    if not use_context:
        logger.info("[生成节点] 无上下文，走拟人化对话模式")

        # Mock 模式：返回模拟回答
        if get_deepseek().use_mock:
            final_answer = f"【{name}】你好呀！我是智悦校园的智能助手{name}～ 你问的「{question}」是一个有趣的问题呢！不过如果你想了解学校的具体政策或规定，可以试试加载相关文档后提问哦。"
            emit(final_answer)
            logger.info("[生成节点] Mock 模式，返回模拟回答")
            updated_messages = _append_turn_messages(state, question, final_answer, context="")
            logger.info(f"[生成节点] 回答生成完成，长度: {len(final_answer)} 字，messages 共 {len(updated_messages)} 条")
            return {"final_answer": final_answer, "messages": updated_messages}

        # 真实 API 模式：以校园助手身份直接回答
        endpoint = _chat_endpoint(state)
        if endpoint is None:
            emit(NOT_CONFIGURED)
            updated_messages = _append_turn_messages(state, question, NOT_CONFIGURED, context="")
            logger.warning("[生成节点] 个人、学校、平台都没有可用的对话模型")
            return {"final_answer": NOT_CONFIGURED, "messages": updated_messages}
        try:
            system_prompt = f"""
你是"{name}"，智悦校园的智能校园助手。
你性格温暖、友善、幽默，像一个贴心的学长/学姐。
请用自然、亲切的语气回答用户的问题。
如果用户问的是校园相关问题但你没有具体资料，请礼貌地建议他们使用 /read 命令加载相关文档。
请根据对话历史理解用户的上下文，比如用户之前告诉过你他的名字，你应该记住并使用。
""" + prompt_extras(settings.persona, memories)
            # 从 Redis 恢复的历史里取最近几轮，LLM 才能记住「我是唐亮」这类上文。
            # 只取最近几轮而非全量：否则 token 成本随会话长度线性上涨。
            history_messages = recent_history(state.get("messages", []), MAX_PROMPT_TURNS)

            # 将 system prompt + 历史消息 + 当前问题 组装为完整对话
            api_messages = [{"role": "system", "content": system_prompt}] + history_messages + [{"role": "user", "content": question}]

            # 边生成边下发，首字延迟不再等整段写完
            parts: list[str] = []
            for piece in get_deepseek().stream_chat(api_messages, endpoint, temperature=0.8):
                parts.append(piece)
                emit(piece)
            final_answer = "".join(parts)
        except Exception as e:
            final_answer = f"{FAILED_PREFIX}（{endpoint.label}）{scrub(str(e), endpoint)}"

        updated_messages = _append_turn_messages(state, question, final_answer, context="")
        logger.info(f"[生成节点] 回答生成完成，长度: {len(final_answer)} 字，messages 共 {len(updated_messages)} 条")
        return {"final_answer": final_answer, "messages": updated_messages}

    # --- 场景二：RAG 路径（有上下文，基于知识回答）---
    logger.info("[生成节点] 有上下文，走 RAG 知识问答模式")

    # chat_with_context 是 generator，逐块消费：一边拼完整答案，一边下发给前端
    parts: list[str] = []
    for chunk in get_deepseek().chat_with_context(
        context, question, persona=settings.persona, memories=memories, endpoint=_chat_endpoint(state)
    ):
        parts.append(chunk)
        emit(chunk)
    full_answer = "".join(parts)

    updated_messages = _append_turn_messages(
        state, question, full_answer, context=context, sources=state.get("sources") or []
    )
    logger.info(f"[生成节点] 回答生成完成，长度: {len(full_answer)} 字，messages 共 {len(updated_messages)} 条")
    return {"final_answer": full_answer, "messages": updated_messages}


def emotion_node(state: GraphState) -> dict:
    """
    【情感分析节点】- 分析用户情绪。

    它只读 user_question，与检索、生成没有数据依赖，所以从 START 起就与
    主链路并行跑。串在 generate 后面的话，这次 API 往返会整个加在
    「最后一个 token 吐完」到「回答结束」之间，用户干等。

    只返回 emotion_data，挂到消息上的事交给 finalize_node——
    那时候 generate 才刚把 assistant 消息写进去。

    同一次 JSON 调用顺带抽取 memory_facts（值得长期记住的个人事实），
    自动记忆不多花一次模型调用；抽到的条目写进记忆库，不留在 emotion_data 里。
    """
    question = state.get("user_question", "")
    logger.info(f"[情感节点] 分析用户问题: \"{question[:40]}\"")
    try:
        emotion = get_deepseek().analyze_emotion_and_reply(question, endpoint=_chat_endpoint(state))
    except Exception as e:
        # 这条分支出错不能拖垮整轮：危机回复和求助卡片都要等它跑完
        logger.error(f"[情感节点] 分析失败，本轮不挂情绪: {e}")
        return {"emotion_data": None}
    if isinstance(emotion, dict):
        emotion = dict(emotion)
        facts = emotion.pop("memory_facts", None)
        _store_auto_memories(state, facts)
    return {"emotion_data": emotion}


def _store_auto_memories(state: GraphState, facts: Any) -> None:
    user_id = state.get("user_id") or ""
    if not user_id or not MEMORY_AUTO_EXTRACT or not isinstance(facts, list):
        return
    contents = [item for item in facts if isinstance(item, str) and item.strip()][:3]
    if not contents:
        return
    try:
        added = memory_store.add_memories(
            state.get("tenant_id") or GLOBAL_TENANT, user_id, contents, origin="auto"
        )
        if added:
            logger.info(f"[记忆] 自动记下 {len(added)} 条")
    except Exception as e:
        logger.warning(f"[记忆] 自动写入失败: {e}")


def finalize_node(state: GraphState) -> dict:
    """
    【收尾节点】- 等生成与情感两条分支都完成后，把情绪挂到本轮 assistant 消息上，
    便于 Redis → UI 完整恢复。
    """
    emotion_data = state.get("emotion_data")
    messages = list(state.get("messages", []))
    if not emotion_data or not messages or messages[-1].get("role") != "assistant":
        return {}

    last = dict(messages[-1])
    last["emotion"] = emotion_data
    patch: dict[str, Any] = {}
    # 关键词没拦到、但情绪分析判出高风险：补一张求助卡片
    if not last.get("safety") and comfort.model_flags_risk(emotion_data):
        logger.warning("[收尾] 情绪分析判为高风险，补发求助卡片")
        last["safety"] = patch["safety"] = comfort.safety_card(_settings(state).counseling_contact)
    messages[-1] = last
    patch["messages"] = messages
    return patch


# ============================================================
# 第五步：构建并编译状态图
# ============================================================

def build_graph():
    """
    构建并编译 LangGraph 状态图（进程内单例缓存，避免每轮重编译）。

    图结构（含意图路由）:
        START ──> intent_node ──┬── RAG ──> retrieve_node ──┐
                 │             └── DIRECT ─────────────────┴──> generate_node ──┐
                 │                                                               ├──> finalize ──> END
                 └──> emotion_node ─────────────────────────────────────────────┘

    情感分析与主链路并行：它只依赖 user_question，串在生成后面只会平白拉长
    「最后一个 token」到「回答结束」之间的等待。finalize 等两条分支都到齐。
    """
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    graph = StateGraph(GraphState)

    graph.add_node("intent", intent_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("emotion", emotion_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "intent")
    graph.add_edge(START, "emotion")
    graph.add_conditional_edges(
        "intent",
        route_query,
        {
            "retrieve": "retrieve",
            "generate": "generate",
        }
    )
    graph.add_edge("retrieve", "generate")
    # 两条分支都到齐才收尾；分开写两条边的话，先完成的那条就会单独触发 finalize
    graph.add_edge(["generate", "emotion"], "finalize")
    graph.add_edge("finalize", END)

    checkpointer = get_checkpointer()
    _compiled_graph = graph.compile(checkpointer=checkpointer)
    return _compiled_graph


# ============================================================
# 第六步：提供便捷调用函数
# ============================================================

def ask_question(
    question: str,
    doc_content: str = "",
    strategy: str = "auto",
    history: list = None,
    thread_id: str = "default",
    tenant_id: str = GLOBAL_TENANT,
    user_id: str = "",
) -> dict:
    """
    一站式提问函数：封装 Graph 的调用流程。

    参数:
        question    -- 用户的问题
        doc_content -- 已加载的文档全文（为空则只走向量检索）
        strategy    -- 路由策略: "auto" / "kimi" / "vectordb"
        history     -- 对话历史（可选，通常由 Redis 自动管理）
        thread_id   -- 会话线程 ID（关键参数！）
                       同一 thread_id 的多次调用共享同一个 State，
                       Redis 根据此 ID 存取对应的历史记忆。

    返回:
        包含 final_answer、intent、context 的完整状态字典

    关键设计:
        invoke() 时只传「输入字段」，不传「计算字段」。
        LangGraph 会先从 Redis 恢复该 thread_id 的历史 State，
        然后将我们传入的 input 合并进去（而非覆盖）。
        这样 messages 历史就能跨轮次保留。
    """
    # 只传本轮输入，其余字段由 Redis 恢复或由节点填充
    input_state = {
        "user_question": question,          # 当前问题（必填）
        "route_strategy": strategy,         # 检索子路由策略
        "tenant_id": tenant_id,             # 决定检索可见范围
        "user_id": user_id,                 # 长期记忆归属；空串表示不读写记忆
    }

    # 如果显式传入了 history，才覆盖 messages（首次调用或重置场景）
    if history is not None:
        input_state["messages"] = history

    # 如果传入了文档内容，才设置
    if doc_content:
        input_state["current_doc_content"] = doc_content

    # 构造 config，指定 thread_id
    # LangGraph 会根据 thread_id 从 Redis 恢复历史 State，
    # 执行完毕后再将最新 State 存回 Redis
    config = {"configurable": {"thread_id": thread_id}}

    # 执行图
    graph = build_graph()
    result = graph.invoke(input_state, config=config)

    return result


# ============================================================
# 主程序入口（直接运行此文件时执行）
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  智悦校园 · LangGraph 意图路由版")
    print("=" * 60)
    print()

    # 测试1: 闲聊问题（预期走 DIRECT 路径）
    print("【测试1】闲聊问题")
    result1 = ask_question(question="你是谁？")
    print(f"最终回答: {result1['final_answer']}")
    print()

    # 测试2: 教务问题（预期走 RAG 路径）
    print("【测试2】教务问题")
    result2 = ask_question(question="奖学金怎么评？")
    print(f"最终回答: {result2['final_answer']}")
    print()

    print("=" * 60)
    print("  测试完成！")
    print("=" * 60)
