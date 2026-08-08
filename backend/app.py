import os
import uuid
import time
import streamlit as st
from file_service import FileService
from vector_service import VectorRAGService
from voice_service import SparkTTSService
import tempfile
import threading
import glob

from env_config import chroma_persist_dir, load_backend_env
from graph_app import build_graph
from session_thread import resolve_thread_id
from ui_theme import apply_theme, render_hero

load_backend_env()

# ============================================================
# 1. 页面基本配置
# ============================================================
st.set_page_config(page_title="智悦校园 · 小懒", page_icon=":books:", layout="centered")
apply_theme()


# ============================================================
# 2. 全局服务初始化（利用缓存机制，避免刷新页面重复加载）
# ============================================================
@st.cache_resource
def init_services():
    """初始化需要持久化的服务（向量库、语音），LangGraph 服务由 graph_app 懒加载管理"""
    try:
        vector_db = VectorRAGService(persist_directory=chroma_persist_dir())
        tts_mouth = SparkTTSService()
        return vector_db, tts_mouth
    except Exception as e:
        st.error(f"系统初始化失败: {e}")
        st.error("请检查 backend/.env 文件")
        return None, None

services = init_services()
vector_db = services[0]   # 向量数据库服务（用于文件索引）
tts_mouth = services[1]   # 语音服务（用于 TTS）


def _safe_tts_stop():
    if tts_mouth is None:
        return
    try:
        tts_mouth.stop()
    except Exception:
        pass


def _safe_tts_speak(text: str):
    if not st.session_state.get("tts_enabled") or tts_mouth is None or not text:
        return
    threading.Thread(target=tts_mouth.speak, args=(text,)).start()


def _sync_thread_id_to_url(thread_id: str):
    """把 thread_id 写入 URL query，便于刷新/分享后仍对齐 Redis。"""
    st.query_params["tid"] = thread_id


# ============================================================
# 3. Session State 状态管理
# ============================================================
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant",
         "content": "同学，你好！我是你的校园助手小懒。请问有什么可以帮你？"}
    ]

if "current_doc_content" not in st.session_state:
    st.session_state.current_doc_content = ""
if "current_file_name" not in st.session_state:
    st.session_state.current_file_name = ""
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()

# thread_id：URL ?tid= 优先于 session；变化时从 Redis 恢复 UI
_current_tid = st.session_state.get("thread_id")
_tid, _should_restore, _should_sync = resolve_thread_id(
    _current_tid,
    st.query_params.get("tid"),
)
if _current_tid != _tid:
    st.session_state.thread_id = _tid
    print(f"[Session] thread_id -> {_tid} (restore={_should_restore})")
elif "thread_id" not in st.session_state:
    st.session_state.thread_id = _tid
    print(f"[Session] 初始化 thread_id: {_tid}")
if _should_restore:
    st.session_state.pending_history_restore = True
if _should_sync:
    _sync_thread_id_to_url(st.session_state.thread_id)

# 从 Redis 恢复对话到 UI（失败时明确报错，不静默当成空会话）
if st.session_state.pop("pending_history_restore", False):
    try:
        graph = build_graph()
        snapshot = graph.get_state({"configurable": {"thread_id": st.session_state.thread_id}})
        stored = snapshot.values.get("messages") if snapshot and snapshot.values else None
        if stored:
            st.session_state.messages = [
                {
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "context": m.get("context", ""),
                    "emotion": m.get("emotion"),
                }
                for m in stored
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            print(f"[Session] 已从 Redis 恢复 {len(st.session_state.messages)} 条 UI 消息")
    except Exception as e:
        st.error(f"无法从 Redis 恢复会话（请确认 Redis Stack 已启动）: {e}")
        print(f"[Session] Redis 恢复失败: {e}")

if "tts_enabled" not in st.session_state:
    st.session_state.tts_enabled = False
if "typing_effect" not in st.session_state:
    st.session_state.typing_effect = False


# ============================================================
# 4. 自动加载知识库文件
#    短文始终读入 current_doc_content（供 Kimi）；索引按 content_hash 跳过未变更文档
# ============================================================
if "has_autoloaded" not in st.session_state:
    st.session_state.has_autoloaded = True

    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    knowledge_dir = os.path.abspath(os.path.join(current_file_dir, "..", "data"))

    if not os.path.exists(knowledge_dir):
        os.makedirs(knowledge_dir)

    file_paths = glob.glob(f"{knowledge_dir}/*.pdf") + glob.glob(f"{knowledge_dir}/*.txt")

    if file_paths and vector_db is not None:
        with st.spinner(f"正在检查/预加载 {len(file_paths)} 个知识库文件..."):
            for path in file_paths:
                file_name = os.path.basename(path)
                if file_name in st.session_state.processed_files:
                    continue

                content = FileService.read_file(path)
                if content.startswith("Error"):
                    print(f"[Autoload] 读取失败: {file_name}")
                    continue

                st.session_state.processed_files.add(file_name)
                if len(content) < 20000:
                    st.session_state.current_doc_content += (
                        f"\n\n--- 文档来源: {file_name} ---\n\n" + content
                    )
                try:
                    vector_db.index_document(content, file_name)
                except Exception as e:
                    print(f"自动加载文件 '{file_name}' 时建立索引失败: {e}")
        print(f"自动加载完成！共登记 {len(st.session_state.processed_files)} 个文件")


# ============================================================
# 5. 路由策略映射：中文 → 英文（graph_app 使用英文策略名）
# ============================================================
STRATEGY_MAP = {
    "自动路由 (按长度判定)": "auto",
    "Kimi 精读 (全文理解)": "kimi",
    "智谱向量检索 (海量片段)": "vectordb",
}


# ============================================================
# 6. 侧边栏
# ============================================================
with st.sidebar:
    st.markdown("### 小懒控制台")
    st.caption("会话与知识库设置")
    st.markdown("---")


    st.caption(f"会话 ID: `{st.session_state.thread_id[:8]}...`")
    st.caption("刷新页面可保留会话（URL 含 tid）")
    st.markdown("---")

    st.subheader("交互选项")
    st.session_state.tts_enabled = st.toggle(
        "语音播报 (TTS)",
        value=st.session_state.tts_enabled,
        help="关闭时不调用讯飞 TTS，适合无声卡或 Docker 环境。",
    )
    st.session_state.typing_effect = st.toggle(
        "打字机效果",
        value=st.session_state.typing_effect,
        help="开启后逐字显示，关闭则立刻展示完整回答。",
    )
    st.markdown("---")

    st.subheader("路由策略设置")
    route_strategy = st.radio(
        "选择文档阅读引擎：",
        options=["自动路由 (按长度判定)", "Kimi 精读 (全文理解)", "智谱向量检索 (海量片段)"],
        index=0,
        help="短文建议用 Kimi 把握全局，超长文件建议用智谱向量检索提取核心。"
    )
    st.markdown("---")

    st.subheader("知识库上传")
    uploaded_files = st.file_uploader("上传文档（支持 txt、pdf）", type=["txt", "pdf"], accept_multiple_files=True)

    if uploaded_files:
        new_files = [f for f in uploaded_files if f.name not in st.session_state.processed_files]
        for uploaded_file in new_files:
            with st.spinner(f"正在加载文件: {uploaded_file.name} ..."):
                file_ext = uploaded_file.name.split(".")[-1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name
                content = FileService.read_file(tmp_path)
                os.remove(tmp_path)

            if not content.startswith("Error"):
                st.session_state.current_doc_content += f"\n\n---文档来源：{uploaded_file.name}---\n\n" + content
                st.session_state.processed_files.add(uploaded_file.name)
                st.success(f"文件 '{uploaded_file.name}' 加载成功！ 文档长度: {len(content)} 字")

                strategy_en = STRATEGY_MAP.get(route_strategy, "auto")
                if strategy_en == "vectordb" or (strategy_en == "auto" and len(content) >= 20000):
                    with st.spinner(f"正在构建{uploaded_file.name}的向量索引..."):
                        if vector_db is not None:
                            vector_db.index_document(content, uploaded_file.name)
                            st.success(f"文件 '{uploaded_file.name}' 已存入向量数据库！")
                elif strategy_en == "kimi":
                    st.info(f"文件 '{uploaded_file.name}' 将使用 Kimi 精读引擎。")
            else:
                st.error("文件读取失败！")

    st.markdown("---")
    st.subheader("记忆与存储管理")

    if st.button("清空对话记忆（forget）"):
        st.session_state.thread_id = str(uuid.uuid4())
        _sync_thread_id_to_url(st.session_state.thread_id)
        st.session_state.messages = [
            {"role": "assistant", "content": "记忆已清空，让我们重新开始吧！", "context": ""}
        ]
        st.success("对话记忆已清空！已生成新的会话 ID。")
        st.rerun()

    if st.button("格式化向量库 (Clear DB)"):
        if vector_db is not None:
            vector_db.clear_database()
        st.session_state.current_doc_content = ""
        st.session_state.current_file_name = ""
        st.session_state.processed_files = set()
        if "has_autoloaded" in st.session_state:
            del st.session_state["has_autoloaded"]
        st.success("向量库已彻底清空！")
        st.rerun()


# ============================================================
# 7. 聊天交互区
# ============================================================
render_hero()

if "chat_active" not in st.session_state:
    st.session_state.chat_active = False

st.markdown('<p class="zy-section-label">对话</p>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    if not st.session_state.chat_active:
        if st.button("开始本次对话", type="primary", use_container_width=True):
            st.session_state.chat_active = True
            _safe_tts_stop()
            welcome_text = "同学，你好！我是你的校园助手小懒。请问有什么可以帮你？"
            st.session_state.messages.append({"role": "assistant", "content": welcome_text, "context": ""})
            _safe_tts_speak(welcome_text)
with col2:
    if st.session_state.chat_active:
        if st.button("结束本次对话", type="secondary", use_container_width=True):
            st.session_state.chat_active = False
            _safe_tts_stop()
            goodbye_text = "同学，再见！祝你学习愉快！"
            _safe_tts_speak(goodbye_text)
            st.session_state.messages = []
            st.rerun()


# ============================================================
# 8. 渲染历史聊天记录
# ============================================================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("context"):
            with st.expander("查看知识库来源片段"):
                st.info(msg["context"])
        if msg.get("emotion"):
            with st.expander("情感分析结果"):
                st.json(msg["emotion"])


# ============================================================
# 9. 聊天输入框 —— 接入 LangGraph
# ============================================================
if st.session_state.chat_active:

    if user_question := st.chat_input("请输入你的问题"):
        _safe_tts_stop()

        st.session_state.messages.append({"role": "user", "content": user_question, "context": ""})
        with st.chat_message("user"):
            st.markdown(user_question)

        with st.chat_message("assistant"):
            strategy_en = STRATEGY_MAP.get(route_strategy, "auto")

            input_state = {
                "user_question": user_question,
                "route_strategy": strategy_en,
            }
            if st.session_state.current_doc_content:
                input_state["current_doc_content"] = st.session_state.current_doc_content

            config = {"configurable": {"thread_id": st.session_state.thread_id}}

            with st.spinner("小懒正在思考..."):
                graph = build_graph()
                result = graph.invoke(input_state, config=config)

            intent = result.get("intent", "DIRECT")
            context = result.get("context", "")
            final_answer = result.get("final_answer", "抱歉，未能生成回答。")
            json_emotion = result.get("emotion_data") or {}

            st.toast(f"意图判定: {intent} | 路由: {strategy_en}")

            if st.session_state.typing_effect:
                placeholder = st.empty()
                displayed = ""
                for char in final_answer:
                    displayed += char
                    placeholder.markdown(displayed + "▌")
                    time.sleep(0.02)
                placeholder.markdown(displayed)
            else:
                st.markdown(final_answer)

            if json_emotion:
                with st.expander("情感分析结果", expanded=False):
                    st.json(json_emotion)

            _safe_tts_speak(final_answer)

            if context:
                with st.expander("查看知识库来源片段"):
                    st.info(context)

            st.session_state.messages.append({
                "role": "assistant",
                "content": final_answer,
                "context": context,
                "emotion": json_emotion
            })
else:
    st.markdown(
        '<div class="zy-idle">助手已休眠。点击上方「开始本次对话」，小懒就会醒来。</div>',
        unsafe_allow_html=True,
    )
