"""校园工具：学校最新通知、联网搜索、校内地点与步行路线。

外网请求一律经 outbound.check_url：只走 http(s)、只去公网地址；读通知正文只许去学校配置的站点，
防止网页里的提示词注入诱导模型把学生数据拼进网址带出去。
"""
from __future__ import annotations

import io
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from logging_config import get_logger
from outbound import USER_AGENT, check_url, ssl_context
from web_import import html_to_text

logger = get_logger(__name__)

TIMEOUT = httpx.Timeout(12.0)
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_ATTACHMENTS = 10
NOTICE_CHARS = 4000
AMAP = "https://restapi.amap.com"
BOCHA = "https://api.bochaai.com/v1/web-search"
URI_SRC = "smartcampus"
NEARBY_M = 3000
# 「瑞幸咖啡(武汉文理学院店)」这类校内小店不算校内地点
_SHOP = re.compile(r"店\)?$")
AMAP_RETRIES = 2
AMAP_RETRY_WAIT = 0.5
_AMAP_BUSY = frozenset({"CUQPS_HAS_EXCEEDED_THE_LIMIT", "CKQPS_HAS_EXCEEDED_THE_LIMIT", "CQPS_HAS_EXCEEDED_THE_LIMIT", "ACCESS_TOO_FREQUENT"})
_DATE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})|(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)")
ATTACHMENT_EXTS = (".xlsx", ".xls", ".docx", ".doc", ".pdf", ".csv", ".txt")
READABLE_ATTACHMENT_EXTS = frozenset({".xlsx", ".docx", ".pdf", ".csv", ".txt"})
_DOWNLOAD_LINK_HINT = re.compile(r"download|attach|file|fj", re.I)
_EXT_IN_TEXT = re.compile(r"(\.(?:xlsx|xls|docx|doc|pdf|csv|txt))\b", re.I)
_DISPOSITION_STAR = re.compile(r"filename\*\s*=\s*([^']*)''([^;]+)", re.I)
_DISPOSITION_PLAIN = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.I)


class ToolError(Exception):
    """工具没拿到结果，message 会原样交给模型转述。"""


_TRANSPORT: httpx.BaseTransport | None = None  # 测试注入


def _client(allowed_hosts: frozenset[str] | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        verify=ssl_context(),
        headers={"User-Agent": USER_AGENT},
        transport=_TRANSPORT,
        event_hooks={"request": [lambda request: check_url(str(request.url), allowed_hosts)]},
    )


def _get_html(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    if resp.status_code != 200:
        raise ToolError(f"网页返回 {resp.status_code}")
    if len(resp.content) > MAX_PAGE_BYTES:
        raise ToolError("网页太大")
    return resp.text


# ---------------- 学校最新通知 ----------------


def _parse_date(text: str, today: date) -> str:
    match = _DATE.search(text)
    if not match:
        return ""
    if match.group(1):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        month, day = int(match.group(4)), int(match.group(5))
        year = today.year if (month, day) <= (today.month, today.day) else today.year - 1
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_notice_list(html: str, page_url: str, today: date | None = None) -> list[dict[str, str]]:
    """从学校网站的列表页里拣出文章链接：同站、标题像样，日期取同一行里的。"""
    today = today or date.today()
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(page_url).hostname
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        title = re.sub(r"\s+", " ", a.get("title") or a.get_text(" ", strip=True)).strip()
        if urlsplit(url).hostname != host or "/info/" not in url or len(title) < 6 or url in seen:
            continue
        seen.add(url)
        row = a.find_parent("li") or a.parent
        items.append({"title": title, "url": url, "date": _parse_date(row.get_text(" ", strip=True) if row else "", today)})
    return items


def school_news(news_urls: tuple[str, ...], keyword: str = "", limit: int = 8) -> list[dict[str, str]]:
    if not news_urls:
        raise ToolError("学校还没有配置通知来源网址")
    items: list[dict[str, str]] = []
    with _client() as client:
        for url in news_urls:
            try:
                items += parse_notice_list(_get_html(client, url), url)
            except (httpx.HTTPError, ToolError, ValueError) as exc:
                logger.warning(f"[SchoolTools] 通知列表抓取失败 {url}: {exc}")
    if not items:
        raise ToolError("学校网站暂时打不开")
    # 同一篇常挂在好几个栏目下，网址不同
    unique = {(item["title"], item["date"]): item for item in reversed(items)}.values()
    words = [w for w in re.split(r"\s+", keyword.strip()) if w]
    hits = [item for item in unique if all(w in item["title"] for w in words)] if words else list(unique)
    return sorted(hits, key=lambda item: item["date"], reverse=True)[:limit]


def _extract_attachments(soup: BeautifulSoup, page_url: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
    """提取通知正文及附件区内的同站附件链接（支持静态路径与博达/VSB download.jsp）。"""
    containers = soup.select(
        ".v_news_content, #vsb_content, .article, article, .vsb_fj, [class*='attach'], [id*='attach'], [class*='fujian'], [class*='fj']"
    )
    if not containers:
        root = BeautifulSoup(str(soup), "html.parser")
        for tag in root.select("nav, header, footer"):
            tag.decompose()
        containers = [root]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        for a in container.find_all("a", href=True):
            href = str(a["href"]).strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            att_url = urljoin(page_url, href)
            parsed = urlsplit(att_url)
            if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in hosts:
                continue
            if att_url == page_url or att_url in seen:
                continue

            name = re.sub(r"\s+", " ", a.get_text(" ", strip=True) or str(a.get("title") or "")).strip()
            path_ext = os.path.splitext(parsed.path)[1].lower()
            download_attr = str(a.get("download") or "")
            is_download = bool(_DOWNLOAD_LINK_HINT.search(att_url) or a.get("download") is not None)

            ext = ""
            if path_ext in ATTACHMENT_EXTS:
                ext = path_ext
            elif is_download:
                for candidate in (name, download_attr, str(a.get("title") or "")):
                    match = _EXT_IN_TEXT.search(candidate)
                    if match:
                        ext = match.group(1).lower()
                        break
            else:
                continue

            if not ext and not (is_download and len(name) >= 2):
                continue

            seen.add(att_url)
            display_name = name or os.path.basename(unquote(parsed.path)) or f"附件{len(items) + 1}{ext}"
            items.append({
                "name": display_name[:120],
                "url": att_url,
                "ext": ext,
                "readable": (ext in READABLE_ATTACHMENT_EXTS) if ext else True,
            })
            if len(items) >= MAX_ATTACHMENTS:
                return items
    return items


def read_notice(url: str, news_urls: tuple[str, ...]) -> dict[str, Any]:
    hosts = frozenset(h.lower() for h in (urlsplit(u).hostname for u in news_urls) if h)
    if (urlsplit(url).hostname or "").lower() not in hosts:
        raise ToolError("只能打开学校官网上的通知")
    with _client(hosts) as client:
        html = _get_html(client, url)
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".v_news_content, #vsb_content, .article, article")
    title, text = html_to_text(str(body) if body else html)
    heading = soup.find("title")
    attachments = _extract_attachments(soup, url, hosts)
    return {
        "title": (heading.get_text(strip=True) if heading else title),
        "url": url,
        "text": text[:NOTICE_CHARS],
        "attachments": attachments,
    }


def _attachment_filename(url: str, headers: httpx.Headers) -> str:
    disp = headers.get("content-disposition", "")
    star = _DISPOSITION_STAR.search(disp)
    if star:
        charset = star.group(1).strip() or "utf-8"
        try:
            return unquote(star.group(2).strip(), encoding=charset)
        except Exception:
            return unquote(star.group(2).strip())
    plain = _DISPOSITION_PLAIN.search(disp)
    if plain:
        raw_name = plain.group(1).strip()
        try:
            # 处理服务端按 latin-1 拼进头部的 UTF-8/GBK 文件名
            return raw_name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw_name
    path_name = os.path.basename(unquote(urlsplit(url).path))
    return path_name or "通知附件"


def _detect_attachment_ext(filename: str, url: str, content_type: str, data: bytes) -> str:
    for candidate in (os.path.splitext(filename)[1].lower(), os.path.splitext(urlsplit(url).path)[1].lower()):
        if candidate in ATTACHMENT_EXTS:
            return candidate
    ctype = content_type.split(";")[0].strip().lower()
    ctype_map = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "text/csv": ".csv",
        "text/plain": ".txt",
    }
    if ctype in ctype_map:
        return ctype_map[ctype]
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return ".xls" if filename.lower().endswith(".xls") else ".doc"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
                if any(n.startswith("word/") for n in names):
                    return ".docx"
        except zipfile.BadZipFile:
            pass
    return ""


def _split_terms(keyword: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[\s,，;；]+", keyword.strip()) if t.strip()]


def _filter_sheet_rows(
    sheets: list[dict[str, Any]],
    terms: list[str],
    max_rows: int,
) -> tuple[list[tuple[str, str]], list[dict[str, str]], list[dict[str, Any]], int, int, str]:
    """跨工作表筛选与整理表格行。多关键词先做全匹配，未命中再退回任一匹配。"""
    sheet_stats: list[dict[str, Any]] = []
    matched_entries: list[tuple[dict[str, Any], list[list[str]]]] = []
    match_mode = "all" if terms else "none"
    total_rows = 0

    for sheet in sheets:
        total_rows += len(sheet["rows"])

    if not terms:
        for sheet in sheets:
            sheet_stats.append({"name": sheet["name"], "total_rows": len(sheet["rows"]), "matched_rows": len(sheet["rows"])})
            if sheet["rows"]:
                matched_entries.append((sheet, sheet["rows"]))
        total_matched = total_rows
    else:
        def _collect(require_all: bool) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], list[list[str]]]], int]:
            stats: list[dict[str, Any]] = []
            entries: list[tuple[dict[str, Any], list[list[str]]]] = []
            count = 0
            for sheet in sheets:
                hits = []
                for row in sheet["rows"]:
                    joined = " ".join(row).lower()
                    ok = all(t in joined for t in terms) if require_all else any(t in joined for t in terms)
                    if ok:
                        hits.append(row)
                count += len(hits)
                stats.append({"name": sheet["name"], "total_rows": len(sheet["rows"]), "matched_rows": len(hits)})
                if hits:
                    entries.append((sheet, hits))
            return stats, entries, count

        sheet_stats, matched_entries, total_matched = _collect(True)
        if total_matched == 0 and len(terms) > 1:
            sheet_stats, matched_entries, total_matched = _collect(False)
            if total_matched > 0:
                match_mode = "any"

    if not matched_entries:
        first = sheets[0]
        columns = [(f"c{i}", h) for i, h in enumerate(first["headers"])]
        return columns, [], sheet_stats, total_rows, 0, match_mode

    primary_sheet, _ = matched_entries[0]
    multi_sheet = len(matched_entries) > 1
    base_headers = primary_sheet["headers"]
    columns: list[tuple[str, str]] = ([("_sheet", "工作表")] if multi_sheet else []) + [
        (f"c{i}", h) for i, h in enumerate(base_headers)
    ]
    rows: list[dict[str, str]] = []
    for sheet, hits in matched_entries:
        for raw_row in hits:
            if len(rows) >= max_rows:
                break
            item: dict[str, str] = {}
            if multi_sheet:
                item["_sheet"] = sheet["name"]
            for i, _ in enumerate(base_headers):
                item[f"c{i}"] = raw_row[i] if i < len(raw_row) else ""
            rows.append(item)
        if len(rows) >= max_rows:
            break
    return columns, rows, sheet_stats, total_rows, total_matched, match_mode


def read_notice_attachment(
    url: str,
    news_urls: tuple[str, ...],
    keyword: str = "",
    max_rows: int = 80,
) -> dict[str, Any]:
    """下载并解析学校通知里的附件（xlsx/csv/docx/pdf/txt），支持按关键词在本地筛行。"""
    from file_service import extract_sheets_from_bytes, extract_text_from_bytes

    hosts = frozenset(h.lower() for h in (urlsplit(u).hostname for u in news_urls) if h)
    if (urlsplit(url).hostname or "").lower() not in hosts:
        raise ToolError("只能打开学校官网上的通知附件")

    with _client(hosts) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise ToolError(f"附件下载失败（HTTP {resp.status_code}）")
    declared = resp.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_ATTACHMENT_BYTES:
        raise ToolError("附件超过 6MB，请点击原链接下载后查看")
    data = resp.content
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ToolError("附件超过 6MB，请点击原链接下载后查看")

    filename = _attachment_filename(url, resp.headers)
    ext = _detect_attachment_ext(filename, url, resp.headers.get("content-type", ""), data)
    if ext in {".xls", ".doc"}:
        raise ToolError(f"附件「{filename}」是旧版 {ext} 格式，暂不支持直接解析，请点击原链接下载查看")
    if ext not in READABLE_ATTACHMENT_EXTS:
        raise ToolError(f"附件「{filename}」不是支持的文档或表格格式（支持 xlsx / csv / docx / pdf / txt）")

    terms = _split_terms(keyword)
    if ext in {".xlsx", ".csv"}:
        try:
            sheets = extract_sheets_from_bytes(data, ext)
        except Exception as exc:
            raise ToolError(f"表格附件「{filename}」解析失败：{exc}") from exc
        if not sheets:
            return {"title": filename, "url": url, "kind": "table", "total_rows": 0, "matched_rows": 0, "preview_text": "附件表格是空的。"}

        columns, rows, sheet_stats, total_rows, total_matched, match_mode = _filter_sheet_rows(sheets, terms, max_rows)
        preview_lines = [f"附件《{filename}》共 {total_rows} 行（{len(sheets)} 个工作表）"]
        if terms:
            preview_lines.append(f"按关键词「{keyword.strip()}」匹配到 {total_matched} 行（匹配方式：{match_mode}）")
        if rows:
            preview_lines.append(" | ".join(label for _, label in columns))
            for r in rows[:12]:
                preview_lines.append(" | ".join(r.get(k, "") for k, _ in columns))
            if total_matched > 12:
                preview_lines.append(f"…其余 {total_matched - 12} 行已在下方表格中展示")
        return {
            "title": filename,
            "url": url,
            "kind": "table",
            "sheets": sheet_stats,
            "columns": columns,
            "rows": rows,
            "total_rows": total_rows,
            "matched_rows": total_matched,
            "match_mode": match_mode,
            "preview_text": "\n".join(preview_lines)[:NOTICE_CHARS],
        }

    try:
        text = extract_text_from_bytes(data, ext)
    except Exception as exc:
        raise ToolError(f"文档附件「{filename}」解析失败：{exc}") from exc

    if ext == ".pdf" and len(text.strip()) < 20:
        return {
            "title": filename,
            "url": url,
            "kind": "text",
            "scanned": True,
            "text": "这份 PDF 附件可能是扫描版或图片版，未提取到可检索文字，建议点击原链接下载查看。",
        }

    snippets: list[str] = []
    if terms and text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        hit_indices: set[int] = set()
        for idx, line in enumerate(lines):
            low = line.lower()
            if all(t in low for t in terms) or (len(terms) > 1 and any(t in low for t in terms)):
                hit_indices.update(range(max(0, idx - 1), min(len(lines), idx + 2)))
        snippets = [lines[i] for i in sorted(hit_indices)][:40]

    return {
        "title": filename,
        "url": url,
        "kind": "text",
        "scanned": False,
        "matched_snippets": "\n".join(snippets)[:NOTICE_CHARS] if snippets else "",
        "text": text[:NOTICE_CHARS],
    }


# ---------------- 联网搜索（博查） ----------------


def web_search(api_key: str, query: str, count: int = 6) -> list[dict[str, str]]:
    if not api_key:
        raise ToolError("学校还没有配置联网搜索的 Key")
    with _client(frozenset({"api.bochaai.com"})) as client:
        resp = client.post(
            BOCHA,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "freshness": "noLimit", "summary": True, "count": count},
        )
    if resp.status_code in (401, 403):
        raise ToolError("联网搜索的 Key 无效或已欠费")
    if resp.status_code != 200:
        raise ToolError(f"联网搜索失败（{resp.status_code}）")
    pages = ((resp.json().get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {
            "title": p.get("name", ""),
            "url": p.get("url", ""),
            "site": p.get("siteName", ""),
            "date": (p.get("datePublished") or "")[:10],
            "summary": (p.get("summary") or p.get("snippet") or "")[:600],
        }
        for p in pages
    ]


# ---------------- 地图（高德 Web 服务） ----------------


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    lng: float
    lat: float

    @property
    def link(self) -> str:
        return f"https://uri.amap.com/marker?position={self.lng},{self.lat}&name={quote(self.name)}&src={URI_SRC}&callnative=1"


def search_link(keyword: str) -> str:
    return f"https://uri.amap.com/search?keyword={quote(keyword)}&src={URI_SRC}&callnative=1"


def _amap(api_key: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    # 个人认证的 Key 每秒只许 3 次，一次路线规划就要连调三四次：被限流就稍等再试
    for attempt in range(AMAP_RETRIES + 1):
        with _client(frozenset({"restapi.amap.com"})) as client:
            data = client.get(f"{AMAP}{path}", params={**params, "key": api_key}).json()
        if str(data.get("status")) == "1":
            return data
        info = str(data.get("info") or "")
        if info not in _AMAP_BUSY or attempt == AMAP_RETRIES:
            break
        time.sleep(AMAP_RETRY_WAIT * (attempt + 1))
    if info in ("INVALID_USER_KEY", "USERKEY_PLAT_NOMATCH", "INSUFFICIENT_PRIVILEGES"):
        raise ToolError("地图 Key 无效，或不是「Web 服务」类型")
    if info in _AMAP_BUSY:
        raise ToolError("地图接口这会儿太忙，请稍后再问")
    if "LIMIT" in info:
        raise ToolError("地图接口今天的调用额度用完了")
    raise ToolError(f"地图接口出错：{info}")


def _place(poi: dict[str, Any]) -> Place:
    lng, lat = (float(x) for x in str(poi.get("location", "0,0")).split(","))
    address = poi.get("address")
    return Place(name=poi.get("name", ""), address=address if isinstance(address, str) else "", lng=lng, lat=lat)


def campus(api_key: str, school: str) -> Place | None:
    """学校本身在地图上的位置（校区大门附近的那个点）。"""
    return _campus(api_key, school) if school else None


@lru_cache(maxsize=64)
def _campus(api_key: str, school: str) -> Place | None:
    pois = _amap(api_key, "/v5/place/text", {"keywords": school, "page_size": "1"}).get("pois") or []
    return _place(pois[0]) if pois else None


def _tips(api_key: str, campus: Place, keywords: str) -> list[Place]:
    """输入提示：收录了周边搜索漏掉的校门、楼栋。只留学校附近、带坐标的。"""
    tips = _amap(api_key, "/v3/assistant/inputtips", {"keywords": keywords, "location": f"{campus.lng},{campus.lat}"}).get("tips") or []
    places = []
    for tip in tips:
        location = tip.get("location")
        if not isinstance(location, str) or "," not in location:
            continue
        place = _place({"name": tip.get("name", ""), "address": tip.get("address"), "location": location})
        if _distance_m(campus, place) <= NEARBY_M:
            places.append(place)
    return places


def find_places(api_key: str, school: str, keyword: str, limit: int = 5) -> list[Place]:
    """在学校附近 3 公里内找地点；找不到学校本身时退回全国搜「学校名 + 地点」。"""
    campus = _campus(api_key, school) if school else None
    if not campus:
        pois = _amap(api_key, "/v5/place/text", {"keywords": f"{school} {keyword}".strip(), "page_size": str(limit)}).get("pois") or []
        return [_place(p) for p in pois]
    keyword = keyword.removeprefix(school).strip() or keyword
    places = [_place(p) for p in _amap(
        api_key, "/v5/place/around",
        {"keywords": keyword, "location": f"{campus.lng},{campus.lat}", "radius": str(NEARBY_M), "page_size": str(limit)},
    ).get("pois") or []]
    matched = [p for p in places if name_matches(keyword, p)]
    if not matched:
        # 周边搜索不收「通行设施」：东门、北门这类只在输入提示里有；名字里没有这个词的提示多半不相干
        matched = [p for p in _tips(api_key, campus, f"{school}{keyword}") if name_matches(keyword, p)]
    # 名字对得上的优先；都对不上（问「医院」得到「卫生服务站」）就照周边搜索的结果给
    return (matched or places)[:limit]


def _plain(text: str) -> str:
    return re.sub(r"[\s()（）·\-]", "", text)


def name_matches(keyword: str, place: Place, school: str = "") -> bool:
    """「高车地铁站」对得上「高车(地铁站)」，「武汉文理学院东门」对得上「武汉文理学院(东门)」。"""
    return _plain(keyword.removeprefix(school).strip() or keyword) in _plain(place.name)


def short_name(place: Place, school: str) -> str:
    """「武汉文理学院(东门)」→「东门」，「武汉文理学院和澄苑」→「和澄苑」。"""
    rest = place.name.removeprefix(school).lstrip("-· ")
    if re.fullmatch(r"[(（][^()（）]+[)）]", rest):
        rest = rest[1:-1]
    return rest or place.name


def campus_places(api_key: str, school: str) -> list[Place]:
    """学校在地图上标注过的校内地点（楼、宿舍、食堂、校门等），去掉校内小店。"""
    return list(_campus_places(api_key, school)) if school else []


@lru_cache(maxsize=16)
def _campus_places(api_key: str, school: str) -> tuple[Place, ...]:
    campus = _campus(api_key, school)
    if not campus:
        return ()
    found: list[Place] = []
    for page in ("1", "2"):
        pois = _amap(
            api_key, "/v5/place/around",
            {"keywords": school, "location": f"{campus.lng},{campus.lat}", "radius": "1500", "page_size": "25", "page_num": page},
        ).get("pois") or []
        found += [_place(p) for p in pois]
        if len(pois) < 25:
            break
    found += [p for p in _tips(api_key, campus, f"{school}门") if "门" in p.name]
    seen: set[str] = set()
    places = []
    for p in found:
        if p.name.startswith(school) and p.name != school and not _SHOP.search(p.name) and p.name not in seen:
            seen.add(p.name)
            places.append(p)
    return tuple(places)


def _distance_m(a: Place, b: Place) -> int:
    rad = math.pi / 180
    x = (b.lng - a.lng) * rad * math.cos((a.lat + b.lat) * rad / 2)
    y = (b.lat - a.lat) * rad
    return int(math.hypot(x, y) * 6371000)


def walking_route(api_key: str, school: str, origin: str, destination: str) -> dict[str, Any]:
    # 名字对不上的「相近结果」不能拿来导航，会把人带到不相干的地方
    starts = [p for p in find_places(api_key, school, origin, 3) if name_matches(origin, p, school)][:1]
    ends = [p for p in find_places(api_key, school, destination, 3) if name_matches(destination, p, school)][:1]
    if not starts or not ends:
        known = "、".join(short_name(p, school) for p in campus_places(api_key, school)[:20])
        hint = f"。地图上标注过的校内地点有：{known}" if known else ""
        raise ToolError(f"地图上没有标注「{origin if not starts else destination}」，校内地点请以学校资料为准{hint}")
    start, end = starts[0], ends[0]
    if _distance_m(start, end) > 20000:
        raise ToolError("两地相距太远，不适合步行")
    paths = (_amap(
        api_key, "/v3/direction/walking", {"origin": f"{start.lng},{start.lat}", "destination": f"{end.lng},{end.lat}"}
    ).get("route") or {}).get("paths") or []
    if not paths:
        raise ToolError("没规划出步行路线")
    path = paths[0]
    nav = (
        f"https://uri.amap.com/navigation?from={start.lng},{start.lat},{quote(start.name)}"
        f"&to={end.lng},{end.lat},{quote(end.name)}&mode=walk&src={URI_SRC}&callnative=1"
    )
    return {
        "from": start.name,
        "to": end.name,
        "distance_m": int(path.get("distance", 0)),
        "minutes": max(1, round(int(path.get("duration", 0)) / 60)),
        "steps": [s.get("instruction", "") for s in (path.get("steps") or [])][:12],
        "nav_link": nav,
    }
