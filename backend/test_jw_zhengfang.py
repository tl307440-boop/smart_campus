"""正方连接器：RSA 与学校原版 security.js 逐字一致，登录、验证码、会话续期都走模拟的 CAS。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote

import httpx
import outbound
import pytest
from jw_zhengfang import JwAuthError, JwCaptcha, JwError, ZhengfangClient, current_term, rsa_encrypt
from outbound import UnsafeUrlError

VECTORS = json.loads((Path(__file__).parent / "testdata" / "cas_rsa_vectors.json").read_text(encoding="utf-8"))
CAS = "https://cas.test.edu.cn/cas"
JW = "https://jw.test.edu.cn/jwglxt"
SERVICE = f"{JW}/xtgl/index_initMenu.html"
PUB = VECTORS[0]


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: f"{len(c['modulus'])}-{c['text'][:8]}")
def test_rsa_matches_school_security_js(case: dict[str, str]) -> None:
    assert rsa_encrypt(case["text"], case["modulus"], case["exponent"]) == case["expected"]


@pytest.mark.parametrize(
    ("today", "term"),
    [(date(2026, 10, 14), ("2026", "3")), (date(2027, 1, 10), ("2026", "3")), (date(2027, 3, 1), ("2026", "12")), (date(2027, 7, 1), ("2026", "12"))],
)
def test_current_term(today: date, term: tuple[str, str]) -> None:
    assert current_term(today) == term


class FakeSchool:
    """模拟学校的 CAS + 教务。只认学号 2023001 / 密码 secret。"""

    def __init__(self, captcha: bool = False) -> None:
        self.captcha = captcha
        self.posts: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, cookies = request.url, request.headers.get("cookie", "")
        if url.host == "evil.example.com":
            return httpx.Response(200, text="evil")
        if url.host == "cas.test.edu.cn":
            if url.path == "/cas/login" and request.method == "GET":
                if "TGC=ok" in cookies:
                    return httpx.Response(302, headers={"location": f"{SERVICE}?ticket=ST-1"})
                page = '<form id="fm1" action="login?v=0.1" method="post"><input type="hidden" name="execution" value="e1s1"/></form>'
                return httpx.Response(200, text=page, headers={"set-cookie": "CASSESSION=s1; Path=/cas"})
            if url.path == "/cas/v2/getKaptchaStatus":
                return httpx.Response(200, text="true" if self.captcha else "false")
            if url.path == "/cas/kaptcha":
                return httpx.Response(200, content=b"PNG")
            if url.path == "/cas/v2/getPubKey":
                return httpx.Response(200, json={"modulus": PUB["modulus"], "exponent": PUB["exponent"]})
            if url.path == "/cas/login" and request.method == "POST":
                form = parse_qs(request.content.decode())
                self.posts.append(form)
                good = (
                    url.params.get("v") == "0.1"
                    and "CASSESSION=s1" in cookies
                    and form["username"] == ["2023001"]
                    and form["password"] == [rsa_encrypt("terces", PUB["modulus"], PUB["exponent"])]
                    and form["execution"] == ["e1s1"]
                    and (not self.captcha or form.get("authcode") == ["ab12"])
                )
                if good:
                    return httpx.Response(302, headers={"location": f"{SERVICE}?ticket=ST-1", "set-cookie": "TGC=ok; Path=/cas"})
                # 学校的页面里 errormsg 是一段空白（换行加缩进），真正的报错在 msg 里
                return httpx.Response(200, text='<span id="errormsg">\n      \n    </span><span id="msg">用户名或密码错误</span>')
        if url.host == "jw.test.edu.cn":
            if url.path == "/jwglxt/xtgl/index_initMenu.html":
                if url.params.get("ticket"):
                    return httpx.Response(200, text="home", headers={"set-cookie": "JSESSIONID=jw1; Path=/jwglxt"})
                return httpx.Response(200, text="home")
            if "JSESSIONID=jw1" not in cookies:
                # 会话失效：先跳教务自己的登录页，再转到 CAS
                if url.path == "/jwglxt/xtgl/login_slogin.html":
                    return httpx.Response(302, headers={"location": f"{CAS}/login?service={quote(SERVICE, safe='')}"})
                return httpx.Response(302, headers={"location": f"{JW}/xtgl/login_slogin.html"})
            if url.path == "/jwglxt/kbcx/xskbcx_cxXsgrkb.html":
                return httpx.Response(200, json={"kbList": [
                    {"kcmc": "高等数学", "xqj": "1", "jcs": "1-2", "zcd": "1-16周", "cdmc": "1-101", "xm": "张三", "xqh_id": "2"},
                ]})
            if url.path == "/jwglxt/kbcx/xskbcx_cxRjc.html":
                return httpx.Response(200, json=[{"jcmc": "1", "qssj": "08:00", "jssj": "08:45"}, {"jcmc": "2", "qssj": "08:55", "jssj": "09:40"}])
            if url.path == "/jwglxt/cjcx/cjcx_cxXsgrcj.html":
                return httpx.Response(200, json={"items": [{"kcmc": "高等数学", "cj": "90", "xf": "4", "jd": "4.0", "xnmmc": "2025-2026", "xqmmc": "1"}]})
            if url.path == "/jwglxt/cdjy/cdjy_cxKxcdlb.html":
                form = parse_qs(request.content.decode())
                assert form["zcd"] == [str(1 << 6)] and form["jcd"] == [str(0b11)] and form["xqh_id"] == ["2"]
                return httpx.Response(200, json={"items": [
                    {"cdmc": "1-201", "cdlbmc": "多媒体教室", "zws": 120},
                    {"cdmc": "篮球场", "cdlbmc": "运动场", "zws": 0},
                    # 有的学校类别只写「多媒体」，以前按「教室」二字过滤会把它整个丢掉
                    {"cdmc": "3-105", "jxlmc": "三教", "cdlbmc": "多媒体", "zws": 60},
                ]})
            if url.path == "/jwglxt/xsxy/xsxyqk_cxXsxyqkIndex.html":
                return httpx.Response(200, text=ACADEMIA_PAGE)
        return httpx.Response(404)


# 学业情况页的骨架：顶部提示框 + 每类课程一行学分说明，后面跟一个装课程明细的 span
ACADEMIA_PAGE = """<html><body>
<div id="alertBox" class="alert alert-info">
  平均学分绩点(GPA)：3.21
  计划总课程 48 门通过 30 门, 未通过 1 门；未修 12 门；在读 5 门；计划外：通过 2 门，未通过 0 门
</div>
var a = "通识必修课&nbsp;&nbsp;要求学分:40.0&nbsp;获得学分:32.0&nbsp;未获得学分:8.0";
<span id='showKcA1'></span>
var b = "专业选修课&nbsp;&nbsp;要求学分:20.0&nbsp;获得学分:0&nbsp;未获得学分:20.0";
<span id='showKcB2'></span>
</body></html>"""


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbound, "_host_is_public", lambda host, port: True)


def _client(school: FakeSchool) -> ZhengfangClient:
    return ZhengfangClient(CAS, JW, transport=httpx.MockTransport(school))


def test_login_posts_reversed_rsa_password_to_form_action_and_queries() -> None:
    client = _client(FakeSchool())
    client.login("2023001", "secret")
    table = client.timetable("2026", "3")
    assert table["courses"] == [{"course": "高等数学", "weekday": 1, "periods": "1-2", "weeks": "1-16周", "room": "1-101", "teacher": "张三"}]
    assert table["periods"][0] == {"period": "1", "start": "08:00", "end": "08:45"}
    assert table["campus_id"] == "2"
    rooms = client.free_rooms("2026", "3", "2", 7, 3, [1, 2])
    assert rooms == [{"room": "1-201", "building": "", "type": "多媒体教室", "seats": "120"}, {"room": "3-105", "building": "三教", "type": "多媒体", "seats": "60"}]


def test_academia_parses_summary_and_category_credits() -> None:
    client = _client(FakeSchool())
    client.login("2023001", "secret")
    data = client.academia()
    assert data["gpa"] == 3.21
    assert data["plan"] == {"total": 48, "passed": 30, "failed": 1, "not_taken": 12, "in_progress": 5, "extra_passed": 2, "extra_failed": 0}
    assert data["categories"] == [
        {"name": "通识必修课", "required": 40.0, "earned": 32.0, "missing": 8.0},
        {"name": "专业选修课", "required": 20.0, "earned": 0.0, "missing": 20.0},
    ]


def test_academia_surfaces_school_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(FakeSchool())
    client.login("2023001", "secret")
    monkeypatch.setattr(client, "_page", lambda path, params: '<div class="alert alert-danger">学业情况查询暂未开放</div>')
    with pytest.raises(JwError, match="暂未开放"):
        client.academia()


def test_wrong_password_reports_cas_message() -> None:
    with pytest.raises(JwAuthError, match="用户名或密码错误"):
        _client(FakeSchool()).login("2023001", "wrong")


def test_captcha_is_handed_to_student_then_accepted_in_same_session() -> None:
    school = FakeSchool(captcha=True)
    client = _client(school)
    with pytest.raises(JwCaptcha) as caught:
        client.login("2023001", "secret")
    assert caught.value.image_base64 == "UE5H"
    assert school.posts == []  # 没填验证码时不提交密码
    cookies = client.export_cookies()
    again = _client(school)
    again.load_cookies(cookies)
    again.login("2023001", "secret", "ab12")
    assert again.grades()[0]["score"] == "90"


def test_expired_jw_session_is_renewed_through_cas_without_password() -> None:
    school = FakeSchool()
    first = _client(school)
    first.login("2023001", "secret")
    # 只带 CAS 的登录 Cookie，丢掉教务会话：第一次查询会被绕回首页，不能当成查询结果
    cas_only = [c for c in first.export_cookies() if c[2] == "TGC"]
    client = _client(school)
    client.load_cookies(cas_only)
    assert len(client.timetable("2026", "3")["courses"]) == 1
    assert len(school.posts) == 1


def test_renewal_without_cas_login_asks_to_rebind() -> None:
    with pytest.raises(JwAuthError, match="重新绑定"):
        _client(FakeSchool()).grades()


def test_cas_outage_during_renewal_is_not_treated_as_expired() -> None:
    class Down(FakeSchool):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.host == "cas.test.edu.cn":
                return httpx.Response(502, text="Bad Gateway")
            return super().__call__(request)

    with pytest.raises(JwError) as caught:
        _client(Down()).grades()
    assert not isinstance(caught.value, JwAuthError)


def test_cas_outage_on_submit_is_not_a_wrong_password() -> None:
    class Flaky(FakeSchool):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(502, text="Bad Gateway")
            return super().__call__(request)

    with pytest.raises(JwError, match="暂时连不上") as caught:
        _client(Flaky()).login("2023001", "secret")
    assert not isinstance(caught.value, JwAuthError)


def test_redirect_to_another_host_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cas.test.edu.cn":
            return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})
        return httpx.Response(200)

    client = ZhengfangClient(CAS, JW, transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUrlError):
        client.login("2023001", "secret")
