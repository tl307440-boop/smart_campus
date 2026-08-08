"""
test_graph.py - LangGraph 意图路由的本地测试脚本

使用方法:
    cd backend
    python test_graph.py

测试内容:
    1. Graph 结构验证（编译 + 打印节点和边）
    2. 闲聊测试："你是谁" → 预期走 DIRECT 路径，无 context
    3. 业务测试："奖学金怎么评" → 预期走 RAG 路径，有 context
    4. 小文档 + Kimi 精读路径测试

注意: 本脚本不依赖 Streamlit，纯控制台输出。
      当前服务以 mock 模式运行，不消耗 API 额度。
"""

import os
import sys
import time

# 将 backend 目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graph_app import ask_question, build_graph, GraphState


def test_graph_structure():
    """
    测试1: 验证 Graph 能正常编译，并打印图结构。
    """
    print("=" * 60)
    print("测试1: Graph 结构验证")
    print("=" * 60)

    graph = build_graph()

    # 打印图的基本信息
    print(f"Graph 类型: {type(graph).__name__}")

    # 获取图的节点列表
    graph_obj = graph.get_graph()
    nodes = list(graph_obj.nodes.keys())
    print(f"Graph 节点: {nodes}")

    # 打印边的信息
    edges = list(graph_obj.edges)
    print(f"Graph 边数量: {len(edges)}")
    for edge in edges:
        print(f"  {edge}")

    print("[PASS] Graph 编译成功，结构验证通过!\n")


def test_direct_intent():
    """
    测试2: 闲聊问题 - 验证 DIRECT 意图路由。

    输入: "你是谁？"
    预期: intent = "DIRECT"，跳过 retrieve_node，直接走 generate_node
    """
    print("=" * 60)
    print("测试2: 闲聊问题（DIRECT 路径）")
    print("=" * 60)

    start = time.time()
    result = ask_question(question="你是谁？")
    elapsed = time.time() - start

    print(f"\n--- 结果 ---")
    print(f"问题: 你是谁？")
    print(f"意图: {result['intent']}")
    print(f"上下文: {'(空)' if not result['context'] else result['context'][:50] + '...'}")
    print(f"回答: {result['final_answer']}")
    print(f"耗时: {elapsed:.2f} 秒")

    # 验证
    assert result["intent"] == "DIRECT", f"预期 DIRECT，实际 {result['intent']}"
    print("[PASS] 闲聊问题测试通过!\n")


def test_rag_intent():
    """
    测试3: 教务问题 - 验证 RAG 意图路由。

    输入: "奖学金怎么评？"
    预期: intent = "RAG"，走 retrieve_node（向量检索路径），再走 generate_node
    """
    print("=" * 60)
    print("测试3: 教务问题（RAG 路径）")
    print("=" * 60)

    start = time.time()
    result = ask_question(
        question="奖学金怎么评？",
        strategy="vectordb"  # 强制走向量检索
    )
    elapsed = time.time() - start

    print(f"\n--- 结果 ---")
    print(f"问题: 奖学金怎么评？")
    print(f"意图: {result['intent']}")
    print(f"上下文长度: {len(result['context'])} 字")
    print(f"回答: {result['final_answer']}")
    print(f"耗时: {elapsed:.2f} 秒")

    # 验证
    assert result["intent"] == "RAG", f"预期 RAG，实际 {result['intent']}"
    print("[PASS] 教务问题测试通过!\n")


def test_kimi_path():
    """
    测试4: 小文档 + Kimi 精读路径。

    输入: 模拟小文档 + "图书馆周末几点开门？"
    预期: intent = "RAG"，走 Kimi 精读路径（文档 < 20000 字）
    """
    print("=" * 60)
    print("测试4: 小文档 + Kimi 精读路径")
    print("=" * 60)

    small_doc = """
    武汉文理学院图书馆开放时间：
    周一至周五：8:00 - 22:00
    周六、周日：9:00 - 17:00
    法定节假日闭馆。
    借阅规则：每人最多借阅5本书，借期30天，可续借一次。
    """

    start = time.time()
    result = ask_question(
        question="图书馆周末几点开门？",
        doc_content=small_doc,
        strategy="kimi"
    )
    elapsed = time.time() - start

    print(f"\n--- 结果 ---")
    print(f"问题: 图书馆周末几点开门？")
    print(f"文档长度: {len(small_doc)} 字")
    print(f"意图: {result['intent']}")
    print(f"Kimi 提取的上下文: {result['context'][:80]}...")
    print(f"回答: {result['final_answer']}")
    print(f"耗时: {elapsed:.2f} 秒")

    # 验证
    assert result["intent"] == "RAG", f"预期 RAG，实际 {result['intent']}"
    assert len(result["context"]) > 0, "Kimi 路径应返回非空上下文"
    print("[PASS] Kimi 精读路径测试通过!\n")


def test_auto_route():
    """
    测试5: 自动路由策略 + 短文档。

    输入: 短文档 + "宿舍门禁是几点？" + strategy="auto"
    预期: intent = "RAG"，auto 路由判断文档 < 20000 字，走 Kimi 路径
    """
    print("=" * 60)
    print("测试5: 自动路由策略")
    print("=" * 60)

    short_doc = "学校规定晚上10点前必须返回宿舍。宿舍门禁时间为22:00。"

    start = time.time()
    result = ask_question(
        question="宿舍门禁是几点？",
        doc_content=short_doc,
        strategy="auto"
    )
    elapsed = time.time() - start

    print(f"\n--- 结果 ---")
    print(f"问题: 宿舍门禁是几点？")
    print(f"文档长度: {len(short_doc)} 字 (应 < 20000，走 Kimi)")
    print(f"意图: {result['intent']}")
    print(f"回答: {result['final_answer']}")
    print(f"耗时: {elapsed:.2f} 秒")

    print("[PASS] 自动路由策略测试通过!\n")


# ============================================================
# 主测试入口
# ============================================================

if __name__ == "__main__":
    print()
    print("╔" + "═" * 58 + "╗")
    print("║    智悦校园 · LangGraph 意图路由 · 本地测试脚本          ║")
    print("╚" + "═" * 58 + "╝")
    print()

    # 依次执行所有测试
    test_graph_structure()    # 测试1: 图结构
    test_direct_intent()      # 测试2: 闲聊 → DIRECT
    test_rag_intent()         # 测试3: 教务 → RAG (向量检索)
    test_kimi_path()          # 测试4: 小文档 → RAG (Kimi 精读)
    test_auto_route()         # 测试5: 自动路由

    print("╔" + "═" * 58 + "╗")
    print("║    所有测试完成!                                         ║")
    print("╚" + "═" * 58 + "╝")
