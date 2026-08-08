import os 
from pypdf import PdfReader

class FileService:
    """
    读取pdf文件的内容方便上传到kimi
    """
    
# // AI辅助生成：DeepSeek-chat (Web), 2026-04
# 辅助实现基于 pypdf 的多格式文本提取及异常处理逻辑
    @staticmethod
    def read_file(file_path: str) -> str:
        """
        加载 PDF 文件并提取文本内容
        :param file_path: PDF 文件路径
        :type file_path: str
        :return: 返回提取的文本内容
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件未找到: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()

        try:
            content = ""
            if file_ext == '.txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read() 
            elif file_ext == '.pdf':
                reader = PdfReader(file_path)
                for page in reader.pages:
                    content += page.extract_text() + "\n"
            else:
                return "不支持的文件格式，仅支持 PDF 和 TXT 文件。"
            return content.strip() 


        except Exception as e:
            return f"读取文件时出错: {str(e)}"