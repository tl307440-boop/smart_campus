import os 
import json
import re
from openai import OpenAI,OpenAIError
from typing import Dict,Any

from env_config import load_backend_env

load_backend_env()

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
                                 base_url = self.url)
        else:
            self.client = None
            if not use_mock:
                print("未检测到API KEY ，强制切换为mock模式")
                self.use_mock = True


 # // AI辅助生成：DeepSeek-chat (API), 2026-04
# 辅助设计系统提示词，实现严格 JSON 格式的情感分析输出   
    def analyze_emotion_and_reply(self,user_text:str) -> Dict[str,Any]:
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

        """

        #2.模拟返回
        if self.use_mock:
            print("[Mock]正在模拟DeepSeek思考过程...")

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
            print("[真实API] 正在请求DeepSeek...")
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
            print(f" API请求失败：{e}")
            return {"error" :str(e)}
        except json.JSONDecodeError as er:
            print("模型未返回合法的JSON格式")
            return {"error": str(er)}
        
    def chat_with_context(self,context:str,user_question:str) -> str:
        """
       RAG 核心接口：deepseek 基于传入的Context(上下文)进行对话（来自kimi或者向量数据库）
                    回答问题,并且分析用户情绪生成回复,流式输出提升响应速度
        """
        #1.构建提示词：强制deepseek只根据Context回答

        system_prompt = f"""
        你是一名知识渊博的校园助手。
        请严格基于下方的【参考资料】回答用户的问题。
        如果参考资料中没有相关信息，请礼貌诚实地告知用户“资料未提及”。
        请用温暖，有条理的语气回答。
        【参考资料】:
        {context}
        """
        if self.use_mock:
            yield f"【deepseek】这是一个模拟回复。{user_question}的解释是"
            return
        full_answer = ""    
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_question}
                ],
                temperature=0.8, #增加多样性,回复更自然
                stream=True #开启流式输出，提升响应速度
            )
            for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    full_answer += content
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
        




            






























