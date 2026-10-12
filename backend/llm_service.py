import json
import os
from collections.abc import Iterator
from typing import Any

from env_config import load_backend_env
from llm_request import current_llm_request
from logging_config import get_logger
from openai import OpenAI, OpenAIError

logger = get_logger(__name__)

load_backend_env()


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# 不设超时的话，上游卡住会一直占着连接和线程池名额
LLM_TIMEOUT_SECONDS = _env_float("LLM_TIMEOUT_SECONDS", 60.0)
LLM_MAX_RETRIES = int(_env_float("LLM_MAX_RETRIES", 2))


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
    封装deepseek的客户端类
    实现情感分析 自然语言处理
    """

    def __init__(self,use_mock:bool = True):
        """
        初始化服务
        
        :param : use_mock为true,不发送真实网络请求，返回会模拟数据（调试 省钱）

        """
        self.use_mock = use_mock
        self.api_key = os.getenv("deepseek_API_key")
        self.url = os.getenv("deepseek_url")


        if self.api_key and not use_mock:
            self.client = OpenAI(api_key=self.api_key,
                                 base_url = self.url,
                                 timeout = LLM_TIMEOUT_SECONDS,
                                 max_retries = LLM_MAX_RETRIES)
        else:
            self.client = None
            if not use_mock:
                logger.warning("未检测到API KEY ，强制切换为mock模式")
                self.use_mock = True


 # // AI辅助生成：DeepSeek-chat (API), 2026-04
# 辅助设计系统提示词，实现严格 JSON 格式的情感分析输出   
    def analyze_emotion_and_reply(self,user_text:str) -> dict[str,Any]:
        """
        analyze_emotion_and_reply 的 Docstring
        情感生成 NLP回复
        :param user_text: 用户输入
        :type user_text: str
        :return: 返回json
        :rtype: Dict[str, Any]
        """
        #1.定义系统提示词 
        system_prompt = """
        你是一名专业的校园心理咨询师助手“小懒”。
        请分析学生的输入，并返回严格正确的JSON格式数据，包含以下字段：
        1. core_emotion (核心情绪): 如焦虑、兴奋、迷茫等
        2. stress_source(压力源): 如学业、情感、就业等
        3. risk_level (风险等级): 1-10的整数
        4. reply_text (回复内容): 温暖、共情、引导性的回复等
        5. memory_facts (值得长期记住的事): 字符串数组，只收学生明确说出的、关于自己的
           稳定事实（姓名、学院、专业、年级、长期偏好等），每条不超过 30 字；
           没有就返回空数组，不要推测，不要收录一次性的提问内容

        """

        #2.模拟返回
        if self.use_mock:
            logger.info("[Mock]正在模拟DeepSeek思考过程...")

            #模拟挂科 返回JSON格式
            if "挂科" in user_text or "考试" in user_text :
                return {
                    "core_emotion":"焦虑",
                    "stress_source":"学业压力",
                    "risk_level": 6,
                    "reply_text": "同学，听到你这么说，我能感觉到你此刻的紧绷。一次考试的失利不代表整个大学生涯的失败。我们先深呼吸一下，要不要一起复盘下是复习方法的问题，还是最近状态不太好？"
                }
            else :
                # 默认模拟返回
                return {
                    "core_emotion": "平静/未知",
                    "stressor": "无",
                    "risk_level": 1,
                    "reply_text": "我在听，你可以随时告诉我你的想法。"
                }
            
        
        #3.真实API调用
        """messages (列表) -> 发送给 API -> reponse (对象) -> 提取出 content (JSON 字符串) -> 转换为 Python 字典返回。"""
        try :
            logger.info("[真实API] 正在请求DeepSeek...")
            reponse = self.client.chat.completions.create(
                model = "deepseek-chat",
                messages = [
                    {"role":"system","content":system_prompt},
                    {"role":"user","content":user_text}
                ],
                response_format  = {"type":"json_object"} #强转json

            )

            #解析json字符串
            content = reponse.choices[0].message.content
            return json.loads(content)
        except OpenAIError as e:
            logger.error(f" API请求失败：{e}")
            return {"error" :str(e)}
        except json.JSONDecodeError as er:
            logger.info("模型未返回合法的JSON格式")
            return {"error": str(er)}
        
    def chat_with_context(
        self,
        context: str,
        user_question: str,
        persona: str = "",
        memories: list[str] | None = None,
    ) -> Iterator[str]:
        """
        RAG 核心接口：基于传入的 Context（来自 Kimi 或向量库）流式回答。

        Args:
            context: 检索到的参考资料
            user_question: 用户问题
            persona: 学校在工作区设置里配的人设说明，追加到系统提示词
            memories: 与问题相关的用户长期记忆

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
        system_prompt += prompt_extras(persona, memories)
        override = current_llm_request()
        client = self.client
        model = "deepseek-chat"
        stream = True
        if override and override.api_key:
            client = OpenAI(
                api_key=override.api_key,
                base_url=override.base_url,
                timeout=LLM_TIMEOUT_SECONDS,
                max_retries=LLM_MAX_RETRIES,
            )
            model = override.model or model
            stream = override.stream
        if client is None:
            yield f"【deepseek】这是一个模拟回复。{user_question}的解释是"
            return
        full_answer = ""    
        try:
            response = client.chat.completions.create(
                model=model,
                stream=stream,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_question}
                ],
                temperature=0.8, #增加多样性,回复更自然
            )
            if stream:
                for chunk in response:
                    content = chunk.choices[0].delta.content
                    if content:
                        full_answer += content
                        yield content
            else:
                content = response.choices[0].message.content or ""
                full_answer = content
                yield content 

        except Exception as e:
            yield f"deepseek 回复失败：{str(e)}"
        
    def clear_context(self):
        """
        清除上下文（如果需要）
        """
        # 目前 deepseek 是无状态的，每次请求都是独立的，所以不需要清除上下文。
        # 但如果未来 deepseek 支持多轮对话，我们可以在这里实现清除上下文的逻辑。
        pass
        




            






























