import os

from env_config import load_backend_env
from logging_config import get_logger
from openai import OpenAI

logger = get_logger(__name__)

load_backend_env()

try:
    # 精读长文档比普通对话慢，超时给得比 DeepSeek 宽一些
    KIMI_TIMEOUT_SECONDS = max(1.0, float(os.getenv("KIMI_TIMEOUT_SECONDS", "120")))
except (TypeError, ValueError):
    KIMI_TIMEOUT_SECONDS = 120.0

class KimiExtractService:
    """
    
    """

    def __init__(self,use_mock:bool = True):
        """
        kimi 知识引擎 ：负责阅读长文档并提取相关片段
        """
        self.use_mock = use_mock
        self.api_key = os.getenv("kimi_API_key")
        self.url = os.getenv("kimi_url")

        if self.api_key and not use_mock:
            self.client = OpenAI(api_key=self.api_key,
                                 base_url = self.url,
                                 timeout = KIMI_TIMEOUT_SECONDS,
                                 max_retries = 1)
        else:
            self.client = None
            if not use_mock:
                logger.warning("未检测到API KEY ，强制切换为mock模式")
                self.use_mock = True
            

 # // AI辅助生成：Kimi (kimi-k2-0905-preview), 2026-04
# 负责从长文档中摘录与查询相关的核心知识点   
    def extract_knowledge(self,doc_content: str,query: str) -> str:
        """
        param doc_content: 文档内容
        param query: 查询内容
        return: 返回提取的内容
        Kimi 不直接回答用户，而是从长文档中'摘录'出和 query 相关的核心知识点。
        """
        if not doc_content.strip():
            return "文档内容为空，无法提取知识。"
        
        #1.提示词 做一名高效的阅读理解助手
        system_prompt = """"
        你是一个文档阅读助手。
        请在长文档中查找与用户问题【最相关】的段落或信息。
        直接提取关键内容，不要添加多余的客套话。
        如果找不到相关信息，返回“无相关信息”。
        """

        if self.use_mock:
            logger.info("[模拟API] 正在请求Kimi知识引擎...")
            #模拟返回
            return f"(Kimi 提取的知识）文档第3章提到：{query} 的核心定义是......"
        
        try :
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"【长文档内容】\n{doc_content}"},
                {"role": "user", "content": f"请提取关于“{query}”的相关信息"}
            ]
            reponse = self.client.chat.completions.create(
                model = "kimi-k2-0905-preview",
                messages = messages,
                temperature=0.1,#降低随机性，提升准确度
            )
            return reponse.choices[0].message.content
        
        except Exception as e:
            return f"Kimi 知识引擎检索失败：{str(e)}"






















