import os

from docx import Document
from pypdf import PdfReader


def _read_docx(file_path: str) -> str:
    """按文档顺序取正文段落和表格。规章里的条款常放在表格中，只读段落会漏掉。"""
    document = Document(file_path)
    body = document.element.body
    lines: list[str] = []
    paragraphs = {p._p: p for p in document.paragraphs}
    tables = {t._tbl: t for t in document.tables}
    for child in body.iterchildren():
        if child in paragraphs:
            text = paragraphs[child].text.strip()
            if text:
                lines.append(text)
        elif child in tables:
            for row in tables[child].rows:
                cells = [cell.text.strip() for cell in row.cells]
                # 合并单元格会在每一格重复出现，去掉相邻重复
                deduped = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
                if deduped:
                    lines.append(" | ".join(deduped))
    return "\n".join(lines)


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
            if file_ext in {'.txt', '.md', '.markdown'}:
                with open(file_path, encoding='utf-8') as f:
                    content = f.read()
            elif file_ext == '.pdf':
                reader = PdfReader(file_path)
                for page in reader.pages:
                    content += page.extract_text() + "\n"
            elif file_ext == '.docx':
                content = _read_docx(file_path)
            else:
                return "不支持的文件格式，仅支持 PDF、Word(docx)、TXT 和 Markdown 文件。"
            return content.strip() 


        except Exception as e:
            return f"读取文件时出错: {str(e)}"