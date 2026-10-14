"""校园工具：学校最新通知、联网搜索、校内地点与步行路线。

外网请求一律经 outbound.check_url：只走 http(s)、只去公网地址；读通知正文只许去学校配置的站点，
防止网页里的提示词注入诱导模型把学生数据拼进网址带出去。
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from logging_config import get_logger
from outbound import USER_AGENT, check_url, ssl_context
from web_import import html_to_text

logger = get_logger(__name__)

TIMEOUT = httpx.Timeout(12.0)
MAX_PAGE_BYTES = 2 * 1024 * 1024
NOTICE_CHARS = 4000
AMAP = "https://restapi.amap.com"
BOCHA = "https://api.bochaai.com/v1/web-search"
URI_SRC = "smartcampus"
AMAP_RETRIES = 2
AMAP_RETRY_WAIT = 0.5
_AMAP_BUSY = frozenset({"CUQPS_HAS_EXCEEDED_THE_LIMIT", "CKQPS_HAS_EXCEEDED_THE_LIMIT", "CQPS_HAS_EXCEEDED_THE_LIMIT", "ACCESS_TOO_FREQUENT"})
_DATE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})|(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)")


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


def read_notice(url: str, news_urls: tuple[str, ...]) -> dict[str, str]:
    hosts = frozenset(h for h in (urlsplit(u).hostname for u in news_urls) if h)
    if urlsplit(url).hostname not in hosts:
        raise ToolError("只能打开学校官网上的通知")
    with _client(hosts) as client:
        html = _get_html(client, url)
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".v_news_content, #vsb_content, .article, article")
    title, text = html_to_text(str(body) if body else html)
    heading = soup.find("title")
    return {"title": (heading.get_text(strip=True) if heading else title), "url": url, "text": text[:NOTICE_CHARS]}


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


def find_places(api_key: str, school: str, keyword: str, limit: int = 5) -> list[Place]:
    """在学校附近 3 公里内找地点；找不到学校本身时退回全国搜「学校名 + 地点」。"""
    campus = _campus(api_key, school) if school else None
    if campus:
        pois = _amap(
            api_key, "/v5/place/around",
            {"keywords": keyword, "location": f"{campus.lng},{campus.lat}", "radius": "3000", "page_size": str(limit)},
        ).get("pois") or []
    else:
        pois = _amap(api_key, "/v5/place/text", {"keywords": f"{school} {keyword}".strip(), "page_size": str(limit)}).get("pois") or []
    return [_place(p) for p in pois]


def _distance_m(a: Place, b: Place) -> int:
    rad = math.pi / 180
    x = (b.lng - a.lng) * rad * math.cos((a.lat + b.lat) * rad / 2)
    y = (b.lat - a.lat) * rad
    return int(math.hypot(x, y) * 6371000)


def walking_route(api_key: str, school: str, origin: str, destination: str) -> dict[str, Any]:
    starts, ends = find_places(api_key, school, origin, 1), find_places(api_key, school, destination, 1)
    if not starts or not ends:
        raise ToolError(f"地图上没有标注「{origin if not starts else destination}」，校内地点请以学校资料为准")
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
