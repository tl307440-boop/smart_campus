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
import sys
from typing import TypedDict, Literal, List, Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.redis import RedisSaver

# ============================================================
# 第一步：导入已有的服务类（不修改它们，只使用它们）
# ============================================================
# 将 backend 目录加入 Python 路径，确保 import 能找到同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_config import chroma_persist_dir, load_backend_env
from llm_service import DeepSeekService
from kimi_extract_service import KimiExtractService
from vector_service import VectorRAGService

load_backend_env()


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
    messages: List[Dict[str, Any]]

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

    # ---- 最终回答（generate_node 的输出）----
    final_answer: str

    # ---- 情感分析结果（后续扩展）----
    emotion_data: Optional[Dict[str, Any]]


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


def _append_turn_messages(
    state: GraphState,
    question: str,
    final_answer: str,
    context: str = "",
) -> List[Dict[str, Any]]:
    """将本轮 user/assistant 写入 messages；assistant 附带 context 便于 UI 恢复。"""
    updated_messages = list(state.get("messages", []))
    updated_messages.append({"role": "user", "content": question})
    updated_messages.append({
        "role": "assistant",
        "content": final_answer,
        "context": context or "",
    })
    return updated_messages


def get_deepseek() -> DeepSeekService:
    """获取 DeepSeek 服务单例（首次调用时创建）"""
    global _deepseek_brain
    if _deepseek_brain is None:
        _deepseek_brain = DeepSeekService(use_mock=False)
    return _deepseek_brain


def get_kimi() -> KimiExtractService:
    """获取 Kimi 服务单例（首次调用时创建）"""
    global _kimi_eye
    if _kimi_eye is None:
        _kimi_eye = KimiExtractService(use_mock=False)
    return _kimi_eye


def get_vector_db() -> VectorRAGService:
    """获取 VectorDB 服务单例（首次调用时创建，失败则返回 None）"""
    global _vector_db
    if _vector_db is None:
        try:
            _vector_db = VectorRAGService(persist_directory=chroma_persist_dir())
        except Exception as e:
            print(f"[VectorDB] 初始化失败: {e}")
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
        print(f"[Redis] 正在连接 Redis: {redis_url}")
        _checkpointer = RedisSaver(redis_url)
        # setup() 会在 Redis 中创建必要的索引结构（首次必须调用）
        _checkpointer.setup()
        print("[Redis] 连接成功，Checkpointer 就绪！")
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
    print(f"\n{'='*50}")
    print(f"[意图判定] 当前问题: \"{question}\"")

    # --- 使用 DeepSeek 做极速意图分类 ---
    # 非流式调用（不带 stream=True），直接获取完整响应，速度快
    system_prompt = """
你是一个意图分类器。请判断用户的问题属于以下哪个类别：

1. 如果问题涉及【校园教务、规章制度、奖学金、学籍管理、学生手册、课程安排、考试规定、宿舍管理、图书馆规则】等需要查阅学校资料才能准确回答的内容，返回：RAG

2. 如果问题是【日常闲聊、打招呼、自我介绍、通用知识、情感倾诉】等不需要查阅学校资料就能回答的内容，返回：DIRECT

只返回一个词：RAG 或 DIRECT。不要返回任何其他内容。
"""

    # --- Mock 模式：用关键词规则做快速分类，不调用 API ---
    if get_deepseek().use_mock:
        print("[意图判定] Mock 模式，使用关键词规则分类")
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
        # 如果问题中包含任何教务关键词，走 RAG 路径
        intent = "RAG" if any(kw in question for kw in rag_keywords) else "DIRECT"

    else:
        # --- 真实 API 模式：调用 DeepSeek 做意图分类 ---
        try:
            response = get_deepseek().client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                temperature=0.1,  # 极低随机性，确保分类稳定
                max_tokens=10,    # 只需一个词，限制 token 消耗
            )
            raw_result = response.choices[0].message.content.strip().upper()
            intent = "RAG" if "RAG" in raw_result else "DIRECT"

        except Exception as e:
            # API 调用失败时，默认走 RAG 路径（保守策略）
            print(f"[意图判定] API 调用失败: {e}，默认走 RAG 路径")
            intent = "RAG"

    print(f"[意图判定] 判定意图为: {intent}")
    print(f"{'='*50}")

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

    if intent == "RAG":
        target = "retrieve"
    else:
        target = "generate"

    print(f"[路由决策] 意图={intent} → 即将走向: {target}_node")
    print(f"{'='*50}\n")

    return target


def retrieve_node(state: GraphState) -> dict:
    """
    【检索节点】- 根据路由策略，从 Kimi 或 VectorDB 获取与问题相关的上下文。

    仅当 intent == "RAG" 时才会执行此节点。

    输入:  state["user_question"]       -- 用户当前问题
           state["current_doc_content"] -- 已加载的文档全文（Kimi 路径需要）
           state["route_strategy"]      -- 路由策略

    输出:  {"context": "..."}           -- 检索到的知识片段

    数据流:
        user_question ──┬── route_strategy == "kimi" 或 (auto 且文档短)
        │               │       └──> KimiExtractService.extract_knowledge(doc, question)
        │               │               └──> 返回相关段落文本
        │               │
        │               └── route_strategy == "vectordb" 或 (auto 且文档长)
        │                       └──> VectorRAGService.search(question, top_k=3)
        │                               └──> 返回 ChromaDB 检索的 top-k 片段
        │
        └──> state["context"] = 检索结果（供 generate_node 使用）
    """
    question = state["user_question"]
    strategy = state.get("route_strategy", "auto")
    doc_content = state.get("current_doc_content", "")

    print(f"[检索节点] 开始检索，路由策略: {strategy}")

    context = ""

    try:
        if strategy == "kimi" or (strategy == "auto" and 0 < len(doc_content) < 20000):
            # --- Kimi 小文档路径 ---
            # 直接将全文交给 Kimi 阅读，让它摘录与问题相关的段落
            print(f"[检索节点] 走 Kimi 精读路径 (文档长度: {len(doc_content)} 字)")
            context = get_kimi().extract_knowledge(doc_content, question)
        else:
            # --- 向量数据库路径 ---
            # 使用智谱 embedding-3 向量化问题，在 ChromaDB 中检索最相似的片段
            print(f"[检索节点] 走向量数据库检索路径 (ChromaDB)")
            vdb = get_vector_db()
            if vdb is not None:
                context = vdb.search(question, top_k=3)
            else:
                print("[检索节点] VectorDB 不可用，跳过检索")
    except Exception as e:
        print(f"[检索节点] 检索失败: {e}")
        context = ""

    print(f"[检索节点] 检索完成，上下文长度: {len(context)} 字")

    return {"context": context}


def generate_node(state: GraphState) -> dict:
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

    print(f"[生成节点] 意图={intent}，上下文长度={len(context)} 字")

    # --- 场景一：DIRECT 路径（无上下文，直接对话）---
    if not context or intent == "DIRECT":
        print("[生成节点] 无上下文，走拟人化对话模式")

        # Mock 模式：返回模拟回答
        if get_deepseek().use_mock:
            final_answer = f"【小懒】你好呀！我是智悦校园的智能助手小懒～ 你问的「{question}」是一个有趣的问题呢！不过如果你想了解学校的具体政策或规定，可以试试加载相关文档后提问哦。"
            print(f"[生成节点] Mock 模式，返回模拟回答")
            updated_messages = _append_turn_messages(state, question, final_answer, context="")
            print(f"[生成节点] 回答生成完成，长度: {len(final_answer)} 字，messages 共 {len(updated_messages)} 条")
            return {"final_answer": final_answer, "messages": updated_messages}

        # 真实 API 模式：调用 DeepSeek 以校园助手身份直接回答
        try:
            system_prompt = """
你是"小懒"，武汉文理学院的智能校园助手。
你性格温暖、友善、幽默，像一个贴心的学长/学姐。
请用自然、亲切的语气回答用户的问题。
如果用户问的是校园相关问题但你没有具体资料，请礼貌地建议他们使用 /read 命令加载相关文档。
请根据对话历史理解用户的上下文，比如用户之前告诉过你他的名字，你应该记住并使用。
"""
            # 【关键修复】从 Redis 恢复的历史消息中提取对话记录，构建完整上下文
            # 这样 LLM 就能看到之前的对话，比如用户说过"我是唐亮"
            history_messages = []
            for msg in state.get("messages", []):
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    history_messages.append({"role": role, "content": content})

            # 将 system prompt + 历史消息 + 当前问题 组装为完整对话
            api_messages = [{"role": "system", "content": system_prompt}] + history_messages + [{"role": "user", "content": question}]

            response = get_deepseek().client.chat.completions.create(
                model="deepseek-chat",
                messages=api_messages,
                temperature=0.8,
                stream=False,  # 非流式，直接获取完整回答
            )
            final_answer = response.choices[0].message.content
        except Exception as e:
            final_answer = f"抱歉，我暂时无法回答这个问题。错误信息: {str(e)}"

        updated_messages = _append_turn_messages(state, question, final_answer, context="")
        print(f"[生成节点] 回答生成完成，长度: {len(final_answer)} 字，messages 共 {len(updated_messages)} 条")
        return {"final_answer": final_answer, "messages": updated_messages}

    # --- 场景二：RAG 路径（有上下文，基于知识回答）---
    print("[生成节点] 有上下文，走 RAG 知识问答模式")

    # chat_with_context 是 generator，需要逐块消费并拼接
    full_answer = ""
    for chunk in get_deepseek().chat_with_context(context, question):
        full_answer += chunk

    updated_messages = _append_turn_messages(state, question, full_answer, context=context)
    print(f"[生成节点] 回答生成完成，长度: {len(full_answer)} 字，messages 共 {len(updated_messages)} 条")
    return {"final_answer": full_answer, "messages": updated_messages}


def emotion_node(state: GraphState) -> dict:
    """
    【情感分析节点】- 在回答生成后分析用户情绪，结果写入 emotion_data，
    并挂到本轮 assistant 消息上，便于 Redis → UI 完整恢复。
    """
    question = state.get("user_question", "")
    print(f"[情感节点] 分析用户问题: \"{question[:40]}\"")
    emotion_data = get_deepseek().analyze_emotion_and_reply(question)
    updated_messages = list(state.get("messages", []))
    if updated_messages and updated_messages[-1].get("role") == "assistant":
        last = dict(updated_messages[-1])
        last["emotion"] = emotion_data
        updated_messages[-1] = last
        return {"emotion_data": emotion_data, "messages": updated_messages}
    return {"emotion_data": emotion_data}


# ============================================================
# 第五步：构建并编译状态图
# ============================================================

def build_graph():
    """
    构建并编译 LangGraph 状态图（进程内单例缓存，避免每轮重编译）。

    图结构（含意图路由）:
        START ──> intent_node ──┬── RAG ──> retrieve_node ──┐
                               │                            ├──> generate_node ──> emotion_node ──> END
                               └── DIRECT ─────────────────┘
    """
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    graph = StateGraph(GraphState)

    graph.add_node("intent", intent_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("emotion", emotion_node)

    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent",
        route_query,
        {
            "retrieve": "retrieve",
            "generate": "generate",
        }
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "emotion")
    graph.add_edge("emotion", END)

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
    thread_id: str = "default"
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
