import csv
import io
import os
from datetime import date, datetime
from typing import Any

from docx import Document
from pypdf import PdfReader

MAX_ATTACHMENT_PDF_PAGES = 30
MAX_SHEET_COUNT = 5
MAX_SHEET_ROWS = 2000
MAX_PPT_SLIDES = 200


def _read_pptx(source: str | io.BytesIO, *, max_slides: int = MAX_PPT_SLIDES) -> str:
    """按幻灯片顺序提取标题、正文、表格与演讲者备注。"""
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("服务端未安装 python-pptx，暂无法解析 .pptx 课件") from exc

    prs = Presentation(source)
    blocks: list[str] = []
    for idx, slide in enumerate(list(prs.slides)[:max_slides], start=1):
        title_shape = getattr(slide.shapes, "title", None)
        title_text = title_shape.text.strip() if title_shape is not None and getattr(title_shape, "has_text_frame", False) else ""
        heading = f"## 第 {idx} 页" + (f"：{title_text}" if title_text else "")
        body_lines: list[str] = []
        for shape in slide.shapes:
            if title_shape is not None and shape == title_shape:
                continue
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    line = " ".join(para.text.split())
                    if line:
                        body_lines.append(line)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [" ".join(cell.text.split()) for cell in row.cells]
                    deduped = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
                    if deduped:
                        body_lines.append(" | ".join(deduped))
        notes = ""
        if getattr(slide, "has_notes_slide", False):
            tf = slide.notes_slide.notes_text_frame
            if tf is not None:
                notes = " ".join(tf.text.split())
        if notes:
            body_lines.append(f"> 讲义备注：{notes}")
        if title_text or body_lines:
            blocks.append("\n".join([heading, *body_lines]))
    return "\n\n".join(blocks).strip()


def _read_docx(source: str | io.BytesIO) -> str:
    """按文档顺序取正文段落和表格。规章里的条款常放在表格中，只读段落会漏掉。"""
    document = Document(source)
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


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).split())


def _normalize_sheet_rows(raw_rows: list[list[str]], name: str) -> dict[str, Any] | None:
    """将二维单元格转换成统一的表头 + 行结构，自动识别首行合并标题。"""
    cleaned: list[list[str]] = []
    for row in raw_rows:
        trimmed = list(row)
        while trimmed and not trimmed[-1]:
            trimmed.pop()
        if any(trimmed):
            cleaned.append(trimmed)
    if not cleaned:
        return None

    title = ""
    header_idx = 0
    first_non_empty = [c for c in cleaned[0] if c]
    if len(cleaned) >= 2 and len(first_non_empty) == 1 and len([c for c in cleaned[1] if c]) >= 2:
        title = first_non_empty[0]
        header_idx = 1

    raw_headers = cleaned[header_idx]
    width = max(len(r) for r in cleaned[header_idx:])
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx in range(width):
        base = (raw_headers[idx] if idx < len(raw_headers) and raw_headers[idx] else f"列{idx + 1}").strip()
        count = seen.get(base, 0) + 1
        seen[base] = count
        headers.append(base if count == 1 else f"{base}_{count}")

    data_rows: list[list[str]] = []
    for row in cleaned[header_idx + 1 :]:
        padded = [row[i] if i < len(row) else "" for i in range(width)]
        if any(padded):
            data_rows.append(padded)

    return {"name": name, "title": title, "headers": headers, "rows": data_rows}


def extract_sheets_from_bytes(data: bytes, ext: str) -> list[dict[str, Any]]:
    """解析内存中的表格文件（.xlsx / .csv），返回工作表列表。"""
    ext = ext.lower()
    if ext == ".csv":
        text = _decode_text(data)
        raw_rows = [[_cell_text(cell) for cell in row] for row in csv.reader(io.StringIO(text))]
        sheet = _normalize_sheet_rows(raw_rows[:MAX_SHEET_ROWS], "表格")
        return [sheet] if sheet else []

    if ext == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise RuntimeError("服务端未安装 openpyxl，暂无法解析 .xlsx 表格") from exc

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets: list[dict[str, Any]] = []
        try:
            for name in wb.sheetnames[:MAX_SHEET_COUNT]:
                ws = wb[name]
                raw_rows: list[list[str]] = []
                for idx, row in enumerate(ws.iter_rows(values_only=True)):
                    if idx >= MAX_SHEET_ROWS:
                        break
                    raw_rows.append([_cell_text(cell) for cell in row])
                sheet = _normalize_sheet_rows(raw_rows, name)
                if sheet:
                    sheets.append(sheet)
        finally:
            wb.close()
        return sheets

    raise ValueError(f"不支持解析为表格的格式: {ext}")


def extract_text_from_bytes(data: bytes, ext: str, *, max_pdf_pages: int = MAX_ATTACHMENT_PDF_PAGES) -> str:
    """从内存字节流中提取文档正文（支持 .txt / .md / .pdf / .docx / .pptx / .csv / .xlsx）。"""
    ext = ext.lower()
    if ext in {".txt", ".md", ".markdown"}:
        return _decode_text(data).strip()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages[:max_pdf_pages]]
        return "\n".join(parts).strip()
    if ext == ".docx":
        return _read_docx(io.BytesIO(data)).strip()
    if ext == ".pptx":
        return _read_pptx(io.BytesIO(data))
    if ext in {".xlsx", ".csv"}:
        sheets = extract_sheets_from_bytes(data, ext)
        blocks: list[str] = []
        for sheet in sheets:
            heading = f"## {sheet['title'] or sheet['name']}"
            lines = [heading, " | ".join(sheet["headers"])]
            lines.extend(" | ".join(row) for row in sheet["rows"])
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks).strip()
    raise ValueError(f"不支持的文件格式: {ext}")


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
                    content += (page.extract_text() or "") + "\n"
            elif file_ext == '.docx':
                content = _read_docx(file_path)
            elif file_ext == '.pptx':
                content = _read_pptx(file_path)
            else:
                return "不支持的文件格式，仅支持 PDF、Word(docx)、PPT(pptx)、TXT 和 Markdown 文件。"
            return content.strip()

        except Exception as e:
            return f"读取文件时出错: {str(e)}"