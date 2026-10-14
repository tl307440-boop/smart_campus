"""学校通知解析、读正文的站点限制、博查与高德的返回解析。"""
from __future__ import annotations

from datetime import date

import httpx
import outbound
import pytest
import school_tools
from school_tools import ToolError, parse_notice_list

LIST_PAGE = """
<ul>
<li><span style="float:right;">09-28</span>· <a href="info/1016/3645.htm" title="关于拟聘机械系系主任人选的公示">关于拟聘机械系…</a></li>
<li><a href="info/1016/3620.htm">2026-2027-1学期公选课网上选课通知</a><span>2026-09-10</span></li>
<li><a href="info/1089/3619.htm">2026-2027-1学期公选课网上选课通知</a><span>2026-09-10</span></li>
<li><a href="xxgk/xyfg.htm">校园风光</a></li>
<li><a href="https://mp.weixin.qq.com/s/abc">学姐带你看校园的一篇推文</a></li>
<li><span>12-30</span><a href="info/1016/3000.htm">去年年底的期末考试安排通知</a></li>
</ul>
"""


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbound, "_host_is_public", lambda host, port: True)


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(school_tools, "_TRANSPORT", httpx.MockTransport(handler))


def test_list_page_keeps_same_site_articles_with_dates() -> None:
    items = parse_notice_list(LIST_PAGE, "https://jwc.test.edu.cn/", today=date(2026, 10, 14))
    assert [i["title"] for i in items] == [
        "关于拟聘机械系系主任人选的公示",
        "2026-2027-1学期公选课网上选课通知",
        "2026-2027-1学期公选课网上选课通知",
        "去年年底的期末考试安排通知",
    ]
    assert items[0] == {"title": "关于拟聘机械系系主任人选的公示", "url": "https://jwc.test.edu.cn/info/1016/3645.htm", "date": "2026-09-28"}
    # 只有月日、且比今天晚的，算去年
    assert items[-1]["date"] == "2025-12-30"


def test_school_news_dedupes_and_filters_by_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, lambda request: httpx.Response(200, text=LIST_PAGE))
    items = school_tools.school_news(("https://jwc.test.edu.cn/",), "选课")
    assert len(items) == 1 and items[0]["date"] == "2026-09-10"
    assert school_tools.school_news(("https://jwc.test.edu.cn/",), "不存在的词") == []


def test_read_notice_only_opens_configured_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    page = "<title>选课通知-教务处</title><nav>首页</nav><div class='v_news_content'><p>9 月 15 日开始选课。</p></div>"
    _mock(monkeypatch, lambda request: httpx.Response(200, text=page))
    notice = school_tools.read_notice("https://jwc.test.edu.cn/info/1/2.htm", ("https://jwc.test.edu.cn/",))
    assert notice["title"] == "选课通知-教务处" and notice["text"] == "9 月 15 日开始选课。"
    with pytest.raises(ToolError):
        school_tools.read_notice("https://evil.example.com/?grades=90", ("https://jwc.test.edu.cn/",))


def test_web_search_parses_bocha_and_maps_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        if request.headers["authorization"] == "Bearer bad":
            return httpx.Response(401)
        return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": [
            {"name": "武汉地铁 21 号线", "url": "https://x.cn/1", "siteName": "x", "summary": "阳逻线", "datePublished": "2026-09-01T08:00:00+08:00"},
        ]}}})

    _mock(monkeypatch, handler)
    results = school_tools.web_search("good", "武汉地铁")
    assert seen["auth"] == "Bearer good"
    assert results == [{"title": "武汉地铁 21 号线", "url": "https://x.cn/1", "site": "x", "date": "2026-09-01", "summary": "阳逻线"}]
    with pytest.raises(ToolError, match="无效"):
        school_tools.web_search("bad", "x")
    with pytest.raises(ToolError, match="Key"):
        school_tools.web_search("", "x")


def test_places_are_searched_around_campus_and_route_has_nav_link(monkeypatch: pytest.MonkeyPatch) -> None:
    school_tools._campus.cache_clear()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.path} {request.url.params.get('keywords', '')}")
        path = request.url.path
        if path == "/v5/place/text":
            return httpx.Response(200, json={"status": "1", "pois": [{"name": "测试学院", "address": "某路 1 号", "location": "114.40,30.72"}]})
        if path == "/v5/place/around":
            name = request.url.params["keywords"]
            lng = "114.401" if name == "图书馆" else "114.403"
            return httpx.Response(200, json={"status": "1", "pois": [{"name": f"测试学院{name}", "address": [], "location": f"{lng},30.721"}]})
        if path == "/v3/direction/walking":
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "260", "duration": "200", "steps": [{"instruction": "向东步行 260 米"}]}]}})
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    places = school_tools.find_places("k", "测试学院", "图书馆")
    assert places[0].name == "测试学院图书馆" and places[0].address == ""
    assert "uri.amap.com/marker?position=114.401,30.721" in places[0].link
    route = school_tools.walking_route("k", "测试学院", "图书馆", "北门")
    assert route["distance_m"] == 260 and route["minutes"] == 3 and route["steps"] == ["向东步行 260 米"]
    assert "mode=walk" in route["nav_link"]
    assert calls.count("/v5/place/text 测试学院") == 1  # 学校位置只查一次


def test_gates_come_from_input_tips_and_unmapped_places_list_campus(monkeypatch: pytest.MonkeyPatch) -> None:
    school_tools._campus.cache_clear()
    school_tools._campus_places.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path == "/v5/place/text":
            return httpx.Response(200, json={"status": "1", "pois": [{"name": "测试学院", "location": "114.40,30.72"}]})
        if path == "/v5/place/around":
            if params["keywords"] == "测试学院":
                return httpx.Response(200, json={"status": "1", "pois": [
                    {"name": "测试学院", "location": "114.40,30.72"},
                    {"name": "测试学院报到处", "location": "114.401,30.72"},
                    {"name": "测试学院和泽楼(4号楼)", "location": "114.402,30.72"},
                    {"name": "测试学院校内超市店", "location": "114.402,30.72"},
                    {"name": "瑞幸咖啡(测试学院店)", "location": "114.402,30.72"},
                ]})
            if params["keywords"] == "报到处":
                return httpx.Response(200, json={"status": "1", "pois": [{"name": "测试学院报到处", "location": "114.401,30.72"}]})
            # 地图上没有的词，周边搜索也会凑几个不相干的
            return httpx.Response(200, json={"status": "1", "pois": [{"name": "7号仓库", "location": "114.41,30.72"}]})
        if path == "/v3/assistant/inputtips":
            return httpx.Response(200, json={"status": "1", "tips": [
                {"name": "测试学院(东门)", "location": "114.403,30.72"},
                {"name": "测试学院和润楼(1号楼)", "location": "114.404,30.72"},
                {"name": "某奶茶(测试学院店)", "location": "114.403,30.72"},
                {"name": "外地大学(东门)", "location": "116.30,39.90"},
                {"name": "测试学院东门公交线", "location": []},
            ]})
        if path == "/v3/direction/walking":
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "164", "duration": "120", "steps": []}]}})
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    assert [p.name for p in school_tools.find_places("k", "测试学院", "东门")] == ["测试学院(东门)"]
    assert [p.name for p in school_tools.find_places("k", "测试学院", "测试学院东门")] == ["测试学院(东门)"]
    assert [p.name for p in school_tools.find_places("k", "测试学院", "1号楼")] == ["测试学院和润楼(1号楼)"]
    # 南门哪儿都没有：只剩周边搜索凑的结果，名字对不上
    assert not any(school_tools.name_matches("南门", p) for p in school_tools.find_places("k", "测试学院", "南门"))
    names = [school_tools.short_name(p, "测试学院") for p in school_tools.campus_places("k", "测试学院")]
    assert names == ["报到处", "和泽楼(4号楼)", "东门"]
    assert school_tools.walking_route("k", "测试学院", "东门", "报到处")["distance_m"] == 164
    with pytest.raises(ToolError, match="标注过的校内地点有：报到处、和泽楼\\(4号楼\\)、东门"):
        school_tools.walking_route("k", "测试学院", "南门", "报到处")


def test_amap_qps_limit_is_retried_not_reported_as_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    school_tools._campus.cache_clear()
    monkeypatch.setattr(school_tools.time, "sleep", lambda seconds: None)
    busy = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if busy["left"]:
            busy["left"] -= 1
            return httpx.Response(200, json={"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"})
        return httpx.Response(200, json={"status": "1", "pois": [{"name": "测试学院", "address": "某路 1 号", "location": "114.40,30.72"}]})

    _mock(monkeypatch, handler)
    assert school_tools.campus("k", "测试学院").name == "测试学院"

    school_tools._campus.cache_clear()
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"}))
    with pytest.raises(ToolError, match="太忙"):
        school_tools.campus("k", "测试学院")
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"status": "0", "info": "DAILY_QUERY_OVER_LIMIT"}))
    with pytest.raises(ToolError, match="额度用完"):
        school_tools.campus("k", "测试学院")


def test_amap_key_errors_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    school_tools._campus.cache_clear()
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"status": "0", "info": "USERKEY_PLAT_NOMATCH"}))
    with pytest.raises(ToolError, match="Web 服务"):
        school_tools.find_places("k", "测试学院", "图书馆")
