from ai_config import READER_TIMEOUT_SECONDS, ModelEndpoint, client_for, platform_reader_endpoint, scrub
from logging_config import get_logger

logger = get_logger(__name__)

# 模型按提示词在找不到时回的原话，等于没摘到
NOTHING_FOUND = "无相关信息"


class KimiExtractService:
    """
    长文档精读：读整篇短文合集，摘出与问题相关的段落（不直接回答用户）。

    用哪把密钥由调用方按 ai_config.reader_endpoint 选好传进来；不传时用服务器 .env 的平台默认。
    """

    def __init__(self, use_mock: bool = False):
        """
        :param use_mock: 为 True 时返回模拟摘录（本地演示用）。只能显式打开：
            模拟摘录会被当成「参考资料」交给模型，线上绝不能出现。
        """
        self.use_mock = use_mock

 # // AI辅助生成：Kimi (kimi-k2-0905-preview), 2026-04
# 负责从长文档中摘录与查询相关的核心知识点
    def extract_knowledge(self, doc_content: str, query: str, endpoint: ModelEndpoint | None = None) -> str:
        """
        :param doc_content: 文档内容
        :param query: 查询内容
        :return: 摘录出的相关内容；没配模型、调用失败或没摘到时返回空串，调用方改走向量检索
        """
        if not doc_content.strip():
            return ""

        system_prompt = f"""
        你是一个文档阅读助手。
        请在长文档中查找与用户问题【最相关】的段落或信息。
        直接提取关键内容，不要添加多余的客套话。
        如果找不到相关信息，返回“{NOTHING_FOUND}”。
        """

        if self.use_mock:
            logger.info("[模拟API] 正在请求Kimi知识引擎...")
            return f"(Kimi 提取的知识）文档第3章提到：{query} 的核心定义是......"

        resolved = endpoint or platform_reader_endpoint()
        if resolved is None:
            logger.info("[精读] 没有配置长文档精读模型，跳过")
            return ""
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"【长文档内容】\n{doc_content}"},
                {"role": "user", "content": f"请提取关于“{query}”的相关信息"}
            ]
            response = client_for(resolved, timeout=READER_TIMEOUT_SECONDS, retries=1).chat.completions.create(
                model=resolved.model,
                messages=messages,
                temperature=0.1,  # 降低随机性，提升准确度
            )
            content = (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"[精读]（{resolved.label}）调用失败，改走向量检索: {scrub(str(e), resolved)}")
            return ""
        return "" if content.strip("。. ") == NOTHING_FOUND else content
