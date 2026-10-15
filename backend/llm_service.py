import json
from collections.abc import Iterator
from typing import Any

from ai_config import ModelEndpoint, client_for, platform_chat_endpoint, scrub
from logging_config import get_logger
from openai import OpenAIError

logger = get_logger(__name__)

# 两处兜底回答的开头被前端 lib/friendly-error.ts 的 FALLBACK_PREFIXES 认出来，改动要同步
FAILED_PREFIX = "抱歉，我暂时无法回答这个问题。错误信息:"
RAG_FAILED_PREFIX = "deepseek 回复失败："
NOT_CONFIGURED = f"{FAILED_PREFIX} 还没有可用的对话模型（未配置），请管理员在「AI 服务」页填好学校的模型密钥。"

# 闲聊与知识库两条路径手上没有教务工具：被问到个人数据时必须拒绝编造
PERSONAL_DATA_RULE = """
你没有任何渠道知道这个学生的成绩、课表、考试安排、学分和排名。被问到这些而手上没有查询结果时，
不要给出任何具体的课程名、分数、时间、教室或数字，直接说你这边查不到，请他在「工具 → 教务账号」里绑定后再问一次，或去教务系统查看。
同样，不确定的、会随时间变化的事实（今年的日期、政策、价格）不要凭记忆给出具体数字，说明需要以学校最新通知为准。
"""


def prompt_extras(persona: str = "", memories: list[str] | None = None) -> str:
    """闲聊与知识库两条路径共用的提示词尾巴：学校人设 + 用户记忆。"""
    extra = ""
    if persona:
        extra += f"\n【学校对你的人设要求】:\n{persona}\n"
    if memories:
        joined = "\n".join(f"- {item}" for item in memories)
        extra += f"\n【关于这位同学，你已知道的事】:\n{joined}\n"
    return extra


class DeepSeekService:
    """
    对话模型的调用封装：情感分析、按资料回答、直接对话。

    用哪家模型、哪把密钥由调用方按 ai_config 选好传进来（endpoint）；
    不传时用服务器 .env 里的平台默认，这样 main01.py 这类老脚本照旧能跑。
    """

    def __init__(self, use_mock: bool = False):
        """
        :param use_mock: 为 True 时不发网络请求，返回模拟数据（本地演示、省钱）。
            只能显式打开；线上没配密钥时要如实报「未配置」，不能拿假回答糊弄学生。
        """
        self.use_mock = use_mock

    @staticmethod
    def _resolve(endpoint: ModelEndpoint | None) -> ModelEndpoint | None:
        return endpoint or platform_chat_endpoint()

 # // AI辅助生成：DeepSeek-chat (API), 2026-04
# 辅助设计系统提示词，实现严格 JSON 格式的情感分析输出
    def analyze_emotion_and_reply(self, user_text: str, endpoint: ModelEndpoint | None = None) -> dict[str, Any]:
        """
        情感分析 + 共情回复，返回 JSON 字典；没有可用模型时返回空字典（本轮不挂情绪）。
        """
        system_prompt = """
        你是一名专业的校园心理咨询师助手“小懒”。
        请分析学生的输入，并返回严格正确的JSON格式数据，包含以下字段：
        1. core_emotion (核心情绪): 如焦虑、兴奋、迷茫等
        2. stress_source(压力源): 如学业、情感、就业等
        3. risk_level (风险等级): 1-10的整数。日常烦恼、学业压力一般在 1-6；
           只有流露出伤害自己、轻生或伤害他人的念头时才给 8 及以上（会触发求助提示）
        4. reply_text (回复内容): 温暖、共情、引导性的回复等
        5. memory_facts (值得长期记住的事): 字符串数组，只收学生明确说出的、关于自己的
           稳定事实（姓名、学院、专业、年级、长期偏好等），每条不超过 30 字；
           没有就返回空数组，不要推测，不要收录一次性的提问内容

        """

        if self.use_mock:
            logger.info("[Mock]正在模拟DeepSeek思考过程...")
            if "挂科" in user_text or "考试" in user_text:
                return {
                    "core_emotion": "焦虑",
                    "stress_source": "学业压力",
                    "risk_level": 6,
                    "reply_text": "同学，听到你这么说，我能感觉到你此刻的紧绷。一次考试的失利不代表整个大学生涯的失败。我们先深呼吸一下，要不要一起复盘下是复习方法的问题，还是最近状态不太好？"
                }
            return {
                "core_emotion": "平静/未知",
                "stressor": "无",
                "risk_level": 1,
                "reply_text": "我在听，你可以随时告诉我你的想法。"
            }

        resolved = self._resolve(endpoint)
        if resolved is None:
            return {}
        try:
            response = client_for(resolved).chat.completions.create(
                model=resolved.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except OpenAIError as e:
            logger.error(f"[情感分析]（{resolved.label}）API 请求失败：{scrub(str(e), resolved)}")
            return {"error": scrub(str(e), resolved)}
        except (json.JSONDecodeError, TypeError) as er:
            logger.info("模型未返回合法的JSON格式")
            return {"error": str(er)}

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        endpoint: ModelEndpoint,
        temperature: float = 0.8,
    ) -> Iterator[str]:
        """逐块产出回答；出错直接抛给调用方，由它决定兜底文案。"""
        client = client_for(endpoint)
        if not endpoint.stream:
            response = client.chat.completions.create(
                model=endpoint.model, messages=messages, temperature=temperature
            )
            yield response.choices[0].message.content or ""
            return
        response = client.chat.completions.create(
            model=endpoint.model, messages=messages, temperature=temperature, stream=True
        )
        for chunk in response:
            piece = chunk.choices[0].delta.content if chunk.choices else None
            if piece:
                yield piece

    def chat_with_context(
        self,
        context: str,
        user_question: str,
        persona: str = "",
        memories: list[str] | None = None,
        endpoint: ModelEndpoint | None = None,
    ) -> Iterator[str]:
        """
        RAG 核心接口：基于传入的 Context（来自 Kimi 或向量库）流式回答。

        Args:
            context: 检索到的参考资料
            user_question: 用户问题
            persona: 学校在工作区设置里配的人设说明，追加到系统提示词
            memories: 与问题相关的用户长期记忆
            endpoint: 这一轮该用的对话模型（ai_config.chat_endpoint）

        Returns:
            逐块产出回答文本的生成器
        """
        system_prompt = f"""
        你是一名知识渊博的校园助手。
        请严格基于下方的【参考资料】回答用户的问题。
        如果参考资料中没有相关信息，请礼貌诚实地告知用户“资料未提及”。
        请用温暖，有条理的语气回答。
        【参考资料】:
        {context}
        """
        system_prompt += PERSONAL_DATA_RULE + prompt_extras(persona, memories)
        if self.use_mock:
            yield f"【deepseek】这是一个模拟回复。{user_question}的解释是"
            return
        resolved = self._resolve(endpoint)
        if resolved is None:
            yield NOT_CONFIGURED
            return
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question},
        ]
        try:
            yield from self.stream_chat(messages, resolved)
        except Exception as e:
            yield f"{RAG_FAILED_PREFIX}（{resolved.label}）{scrub(str(e), resolved)}"
