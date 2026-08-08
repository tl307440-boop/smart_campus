"""
test_memory.py - LangGraph Redis 多轮记忆持久化测试

使用方法:
    1. 确保 Redis 已启动: redis-server 或 docker run -d -p 6379:6379 redis:latest
    2. cd backend
    3. python test_memory.py

测试内容:
    模拟同一个用户（相同 thread_id）分两轮对话：
    - 第一轮: "我的名字叫唐亮" → 存入 Redis
    - 第二轮: "我刚才说了我叫什么" → 从 Redis 恢复历史，能回答"唐亮"

    证明 Redis Checkpointer 能正确持久化和恢复 State。

注意: 当前服务以 mock 模式运行，不消耗 API 额度。
"""

import os
import sys
import time

# 将 backend 目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graph_app import ask_question, build_graph


def test_multi_turn_memory():
    """
    核心测试：同一用户两轮对话，验证 Redis 记忆持久化。

    流程:
        第一轮 invoke(thread_id="test_user_001")
            → intent_node: "我的名字叫唐亮" → DIRECT
            → generate_node: 回答，messages 追加本轮对话
            → Redis 自动存储 State 快照 (thread_id="test_user_001")

        第二轮 invoke(thread_id="test_user_001")
            → Redis 自动恢复上次的 State（包含第一轮的 messages）
            → intent_node: "我刚才说了我叫什么" → DIRECT
            → generate_node: 读取 messages 历史，能回答"唐亮"
    """
    thread_id = "test_user_001"

    print("=" * 60)
    print("测试: Redis 多轮记忆持久化")
    print(f"thread_id: {thread_id}")
    print("=" * 60)

    # ---- 第一轮对话 ----
    print("\n" + "-" * 40)
    print("[第一轮] 发送: 我的名字叫唐亮")
    print("-" * 40)

    start = time.time()
    result1 = ask_question(
        question="我的名字叫唐亮",
        thread_id=thread_id
    )
    elapsed1 = time.time() - start

    print(f"\n--- 第一轮结果 ---")
    print(f"意图: {result1['intent']}")
    print(f"回答: {result1['final_answer']}")
    print(f"messages 数量: {len(result1.get('messages', []))}")
    print(f"耗时: {elapsed1:.2f} 秒")

    # 验证第一轮
    assert result1["intent"] == "DIRECT", f"预期 DIRECT，实际 {result1['intent']}"
    # messages 应包含本轮的 user + assistant 记录
    messages1 = result1.get("messages", [])
    assert len(messages1) >= 2, f"messages 应包含至少 2 条记录，实际 {len(messages1)}"
    # 验证 messages 中包含"唐亮"
    assert "唐亮" in str(messages1), "messages 中应包含'唐亮'"
    print("[PASS] 第一轮对话通过!")

    # ---- 第二轮对话 ----
    print("\n" + "-" * 40)
    print("[第二轮] 发送: 我刚才说了我叫什么")
    print("-" * 40)

    start = time.time()
    result2 = ask_question(
        question="我刚才说了我叫什么",
        thread_id=thread_id  # 使用相同的 thread_id！
    )
    elapsed2 = time.time() - start

    print(f"\n--- 第二轮结果 ---")
    print(f"意图: {result2['intent']}")
    print(f"回答: {result2['final_answer']}")
    print(f"messages 数量: {len(result2.get('messages', []))}")
    print(f"耗时: {elapsed2:.2f} 秒")

    # 验证第二轮
    assert result2["intent"] == "DIRECT", f"预期 DIRECT，实际 {result2['intent']}"

    # 关键验证：messages 应包含两轮对话的历史
    messages = result2.get("messages", [])
    assert len(messages) >= 4, f"messages 应包含至少 4 条记录（两轮对话），实际 {len(messages)}"

    # 验证 messages 中包含第一轮的"唐亮"内容
    messages_text = str(messages)
    assert "唐亮" in messages_text, f"messages 中应包含第一轮的'唐亮'，实际: {messages_text}"

    # 注意：mock 模式下 generate_node 返回固定模板，不会读取历史生成回答。
    # 切换到真实 API（use_mock=False）后，LLM 会读取 messages 历史，
    # 就能回答"你叫唐亮"了。这里只验证 Redis 持久化是否工作。
    print(f"\n[PASS] 第二轮对话通过! Redis 成功恢复了第一轮的记忆!")
    print(f"  messages 中包含'唐亮': 第一轮历史已被 Redis 持久化并恢复")
    print(f"  messages 数量: {len(messages)} (包含两轮对话的完整历史)")

    return result1, result2


def test_different_threads():
    """
    隔离性测试：不同 thread_id 的用户互不干扰。

    用户A (thread_id="user_A") 说"我叫张三"
    用户B (thread_id="user_B") 说"我叫李四"
    用户A 再问"我叫什么" → messages 中应包含"张三"（不是"李四"）
    用户B 再问"我叫什么" → messages 中应包含"李四"（不是"张三"）

    注意：mock 模式下 generate_node 返回固定模板，不读历史。
    这里验证的是 messages 持久化和隔离性，不是 LLM 回答质量。
    """
    print("\n" + "=" * 60)
    print("测试: 不同 thread_id 的隔离性")
    print("=" * 60)

    # 用户A 第一轮
    print("\n[用户A] 发送: 我叫张三")
    result_a1 = ask_question(question="我叫张三", thread_id="user_A")
    print(f"  messages 数量: {len(result_a1.get('messages', []))}")

    # 用户B 第一轮
    print("\n[用户B] 发送: 我叫李四")
    result_b1 = ask_question(question="我叫李四", thread_id="user_B")
    print(f"  messages 数量: {len(result_b1.get('messages', []))}")

    # 用户A 第二轮（messages 中应只包含"张三"，不包含"李四"）
    print("\n[用户A] 发送: 我叫什么")
    result_a2 = ask_question(question="我叫什么", thread_id="user_A")
    msgs_a = str(result_a2.get("messages", []))
    print(f"  messages 数量: {len(result_a2.get('messages', []))}")

    assert "张三" in msgs_a, "用户A 的 messages 中应包含'张三'"
    assert "李四" not in msgs_a, "用户A 的 messages 中不应包含'李四'（隔离性）"

    # 用户B 第二轮（messages 中应只包含"李四"，不包含"张三"）
    print("\n[用户B] 发送: 我叫什么")
    result_b2 = ask_question(question="我叫什么", thread_id="user_B")
    msgs_b = str(result_b2.get("messages", []))
    print(f"  messages 数量: {len(result_b2.get('messages', []))}")

    assert "李四" in msgs_b, "用户B 的 messages 中应包含'李四'"
    assert "张三" not in msgs_b, "用户B 的 messages 中不应包含'张三'（隔离性）"

    print("\n[PASS] 不同 thread_id 隔离性验证通过!")


# ============================================================
# 主测试入口
# ============================================================

if __name__ == "__main__":
    print()
    print("╔" + "═" * 58 + "╗")
    print("║    智悦校园 · LangGraph Redis 多轮记忆 · 测试脚本       ║")
    print("╚" + "═" * 58 + "╝")
    print()
    print("前置条件: Redis 服务必须已启动 (redis-server 或 Docker)")
    print()

    try:
        # 测试1: 同一用户两轮对话记忆
        test_multi_turn_memory()

        # 测试2: 不同用户隔离性
        test_different_threads()

        print("\n" + "╔" + "═" * 58 + "╗")
        print("║    所有测试通过! Redis 多轮记忆持久化工作正常!          ║")
        print("╚" + "═" * 58 + "╝")

    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
