"""企业微信扫码绑定教务：二维码转交、扫码状态轮询、拿 code 回 CAS 换登录凭证，全程不经手密码。"""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import jw_account
import outbound
import pytest
import wecom_qr
from jw_zhengfang import JwAuthError, JwError, ZhengfangClient
from test_jw_account import FakeRedis
from test_jw_zhengfang import CAS, JW, SERVICE, FakeSchool
from workspace import SchoolAi

PORTAL = "https://portal.test.edu.cn/personal-center"
CALLBACK = f"{CAS}/login?client_name=QyWeiXinClient&service={quote(PORTAL, safe='')}"
# 和学校登录页一样：href 里的 & 写成 &amp;，回跳的 service 是门户
BUTTON = (
    '<a href="https://open.work.weixin.qq.com/wwopen/sso/qrConnect?appid=ww123&amp;agentid=1000007'
    f'&amp;redirect_uri={quote(CALLBACK, safe="")}&amp;state=STATE"><i class="fa fa-weixin"></i></a>'
)
PNG = b"\x89PNG\r\n\x1a\nqr"


class WeComSchool(FakeSchool):
    """登录页多一个企业微信按钮；code=good 的回跳给 TGC，教务首页带学号。"""

    def __init__(self, button: bool = True, portal_only: bool = False) -> None:
        super().__init__()
        self.button, self.portal_only = button, portal_only
        self.callbacks: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "cas.test.edu.cn" and url.path == "/cas/login" and url.params.get("client_name") == "QyWeiXinClient":
            query = parse_qs(url.query.decode())
            self.callbacks.append(query)
            if query.get("code") != ["good"]:
                return httpx.Response(200, text='<span id="msg">该企业微信账号未关联学号</span>')
            target = PORTAL if self.portal_only else f"{query['service'][0]}?ticket=ST-9"
            return httpx.Response(302, headers={"location": target, "set-cookie": "TGC=ok; Path=/cas"})
        resp = super().__call__(request)
        if url.host == "cas.test.edu.cn" and url.path == "/cas/login" and request.method == "GET" and resp.status_code == 200 and self.button:
            return httpx.Response(200, text=resp.text + BUTTON, headers=resp.headers)
        if url.host == "jw.test.edu.cn" and url.path == "/jwglxt/xtgl/index_initMenu.html" and url.params.get("ticket"):
            return httpx.Response(200, text='home<input type="hidden" id="sessionUserKey" value="2023001" />', headers=resp.headers)
        return resp


class FakeWeCom:
    """企业微信扫码页：按顺序吐出扫码状态。"""

    def __init__(self, statuses: list[str], code: str = "good") -> None:
        self.statuses, self.code = statuses, code
        self.polls: list[httpx.QueryParams] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == wecom_qr.HOST
        if request.url.path == "/wwopen/sso/qrConnect":
            return httpx.Response(200, text='<img src="//open.work.weixin.qq.com/wwopen/sso/qrImg?key=k123">')
        if request.url.path == "/wwopen/sso/qrImg":
            assert request.url.params["key"] == "k123"
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if request.url.path == "/wwopen/sso/l/qrConnect":
            self.polls.append(request.url.params)
            status = self.statuses.pop(0)
            code = self.code if status == "QRCODE_SCAN_SUCC" else ""
            return httpx.Response(200, text=f'jsonpCallback({json.dumps({"status": status, "auth_code": code})})')
        return httpx.Response(404)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRedis, WeComSchool]:
    fake, school = FakeRedis(), WeComSchool()
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-0123456789abcdef")
    monkeypatch.setattr(outbound, "_host_is_public", lambda host, port: True)
    monkeypatch.setattr(jw_account, "_redis", lambda: fake)
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="zhengfang", jw_cas_url=CAS, jw_base_url=JW))
    monkeypatch.setattr(jw_account, "_new_client", lambda s: ZhengfangClient(CAS, JW, transport=httpx.MockTransport(school)))
    return fake, school


def use_wecom(monkeypatch: pytest.MonkeyPatch, wecom: FakeWeCom) -> None:
    real = wecom_qr._client
    monkeypatch.setattr(wecom_qr, "_client", lambda timeout=20.0, transport=None: real(timeout, httpx.MockTransport(wecom)))


def test_wecom_link_comes_from_cas_button_and_returns_to_jw() -> None:
    client = ZhengfangClient(CAS, JW, transport=httpx.MockTransport(WeComSchool()))
    query = parse_qs(urlsplit(client.wecom_link()).query)
    assert query["appid"] == ["ww123"] and query["agentid"] == ["1000007"] and query["state"] == ["STATE"]
    redirect = urlsplit(query["redirect_uri"][0])
    assert redirect.hostname == "cas.test.edu.cn"
    assert parse_qs(redirect.query) == {"client_name": ["QyWeiXinClient"], "service": [SERVICE]}


def test_school_without_wecom_button_says_use_password() -> None:
    client = ZhengfangClient(CAS, JW, transport=httpx.MockTransport(WeComSchool(button=False)))
    with pytest.raises(JwError, match="账号密码"):
        client.wecom_link()


def qr_key(qr_id: str) -> str:
    return f"jw:wecom:t1:u1:{qr_id}"


def test_scan_binds_with_encrypted_cookies_only(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, school = env
    wecom = FakeWeCom(["QRCODE_SCAN_NEVER", "QRCODE_SCAN_ING", "QRCODE_SCAN_SUCC"])
    use_wecom(monkeypatch, wecom)

    started = jw_account.wecom_start("t1", "u1")
    qr_id = started["id"]
    assert base64.b64decode(started["qr"]) == PNG and started["expiresIn"] == jw_account.PENDING_TTL
    assert fake.data[qr_key(qr_id)].startswith("v1.") and "k123" not in fake.data[qr_key(qr_id)]

    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "waiting"}
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "scanned"}
    assert fake.ttl[qr_key(qr_id)] == jw_account.PENDING_TTL
    done = jw_account.wecom_poll("t1", "u1", qr_id)
    assert done["status"] == "bound" and done["account"]["bound"] is True and done["account"]["username"] == "20***01"
    # 轮询把上一轮的状态带给企业微信，和扫码页自己的脚本一样
    assert [p["lastStatus"] for p in wecom.polls[1:]] == ["QRCODE_SCAN_NEVER", "QRCODE_SCAN_ING"]
    assert school.callbacks[-1]["code"] == ["good"] and school.callbacks[-1]["state"] == ["STATE"]
    assert qr_key(qr_id) not in fake.data and fake.data["jw:wecom-polls:t1:u1"] == "0"
    assert json.loads(fake.data["jw:cred:t1:u1"])["cookies"].startswith("v1.")
    with jw_account.source_for("t1", "u1") as jw:
        assert jw.grades()[0]["course"] == "高等数学"


def test_cas_returning_to_portal_still_lands_on_jw(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    monkeypatch.setattr(
        jw_account, "_new_client", lambda s: ZhengfangClient(CAS, JW, transport=httpx.MockTransport(WeComSchool(portal_only=True)))
    )
    use_wecom(monkeypatch, FakeWeCom(["QRCODE_SCAN_SUCC"]))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    assert jw_account.wecom_poll("t1", "u1", qr_id)["status"] == "bound"
    assert "jw:cred:t1:u1" in fake.data


def test_unlinked_wecom_account_is_not_bound(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    use_wecom(monkeypatch, FakeWeCom(["QRCODE_SCAN_SUCC"], code="nobody"))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    with pytest.raises(JwAuthError, match="未关联学号"):
        jw_account.wecom_poll("t1", "u1", qr_id)
    assert "jw:cred:t1:u1" not in fake.data and qr_key(qr_id) not in fake.data


def test_expired_qr_and_cancel(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    use_wecom(monkeypatch, FakeWeCom(["QRCODE_SCAN_FAIL", "QRCODE_SCAN_ERR"]))
    assert jw_account.wecom_poll("t1", "u1", "nope") == {"status": "expired"}
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "cancelled"}
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "expired"}
    assert qr_key(qr_id) not in fake.data


def test_qr_expiring_during_long_poll_is_not_revived(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    use_wecom(monkeypatch, FakeWeCom([]))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]

    def poll_while_expiring(qr: wecom_qr.QrLogin) -> tuple[str, str]:
        fake.delete(qr_key(qr_id))
        return "scanned", ""

    monkeypatch.setattr(wecom_qr, "poll", poll_while_expiring)
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "scanned"}
    assert qr_key(qr_id) not in fake.data
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "expired"}


def test_old_qr_poll_returning_late_leaves_new_qr_alone(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    use_wecom(monkeypatch, FakeWeCom([]))
    old = jw_account.wecom_start("t1", "u1")["id"]
    new = jw_account.wecom_start("t1", "u1")["id"]
    assert old != new
    new_raw = fake.data[qr_key(new)]
    monkeypatch.setattr(wecom_qr, "poll", lambda qr: ("expired", ""))
    assert jw_account.wecom_poll("t1", "u1", old) == {"status": "expired"}
    assert fake.data[qr_key(new)] == new_raw


def test_auth_code_is_used_once_when_two_pages_poll_the_same_qr(
    env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, school = env
    use_wecom(monkeypatch, FakeWeCom([]))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    inner: list[dict[str, object]] = []

    def both_confirmed(qr: wecom_qr.QrLogin) -> tuple[str, str]:
        if not inner:
            inner.append({})
            # 另一个页面的那一轮先回来、先用掉了 code
            inner[0] = jw_account.wecom_poll("t1", "u1", qr_id)
        return "confirmed", "good"

    monkeypatch.setattr(wecom_qr, "poll", both_confirmed)
    outer = jw_account.wecom_poll("t1", "u1", qr_id)
    assert inner[0]["status"] == "bound" and outer == {"status": "scanned"}
    assert len(school.callbacks) == 1


def test_confirmed_without_code_keeps_waiting_as_scanned(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    use_wecom(monkeypatch, FakeWeCom([]))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    monkeypatch.setattr(wecom_qr, "poll", lambda qr: ("confirmed", ""))
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "scanned"}


def test_long_polls_per_student_are_capped(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    use_wecom(monkeypatch, FakeWeCom([]))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    fake.data["jw:wecom-polls:t1:u1"] = str(jw_account.POLLS_PER_USER)
    # 已经有两轮挂着：不再去企业微信（FakeWeCom 没准备状态，真去了会报错），直接报上一轮的状态
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "waiting"}
    assert fake.data["jw:wecom-polls:t1:u1"] == str(jw_account.POLLS_PER_USER)


def test_long_poll_timeout_keeps_waiting(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wwopen/sso/l/qrConnect":
            raise httpx.ReadTimeout("slow", request=request)
        return FakeWeCom([])(request)

    real = wecom_qr._client
    monkeypatch.setattr(wecom_qr, "_client", lambda timeout=20.0, transport=None: real(timeout, httpx.MockTransport(slow)))
    qr_id = jw_account.wecom_start("t1", "u1")["id"]
    assert jw_account.wecom_poll("t1", "u1", qr_id) == {"status": "waiting"}


def test_qr_refresh_is_rate_limited(env: tuple[FakeRedis, WeComSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    use_wecom(monkeypatch, FakeWeCom([]))
    for _ in range(jw_account.QR_TRIES):
        jw_account.wecom_start("t1", "u1")
    with pytest.raises(JwError, match="次数太多"):
        jw_account.wecom_start("t1", "u1")


def test_qr_page_is_only_fetched_from_wecom() -> None:
    with pytest.raises(outbound.UnsafeUrlError):
        wecom_qr.start("https://evil.example.com/wwopen/sso/qrConnect?appid=x", transport=httpx.MockTransport(FakeWeCom([])))
