"""正方教务（zfsoft）连接器：替学生登录，查课表、考试、成绩、空教室、学业情况。

登录走学校的统一身份认证（CAS）：
  1. GET 登录页，拿到隐藏的 execution 和会话 Cookie；
  2. GET v2/getKaptchaStatus 判断要不要验证码，要就把验证码图交给学生填；
  3. 密码按 cas/js/login/security.js 的做法「倒序 + RSA 公钥加密」后 POST；
  4. 成功后 CAS 302 带 ticket 跳回教务，拿到 CASTGC（CAS 侧）和 JSESSIONID（教务侧）。
之后教务会话过期时，带着 CASTGC 再走一次 CAS 就能换新会话，不需要密码。

只加密保存登录后的 Cookie，不保存学生的教务密码（见 jw_account.py）。
查询全是只读的，绝不发报名、选课、申请这类写操作。
"""
from __future__ import annotations

import base64
import html
import re
import time
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from logging_config import get_logger
from outbound import USER_AGENT, UnsafeUrlError, check_url, ssl_context

logger = get_logger(__name__)

TIMEOUT = httpx.Timeout(20.0)
_EXECUTION = re.compile(r'name="execution"\s+value="([^"]+)"')
_FORM_ACTION = re.compile(r'<form[^>]+id="fm1"[^>]*action="([^"]*)"')
# CAS 登录失败时页面里的报错区
_ERROR = re.compile(r'id="(?:errormsg|msg)"[^>]*>\s*([^<\s][^<]{1,99})')
_CURRENT_WEEK = re.compile(r'id\s*=\s*"dqzc_hide"\s+value\s*=\s*"(\d+)"')
# CAS 登录页「第三方登录」里的企业微信按钮
_WECOM_LINK = re.compile(r'href="(https://open\.work\.weixin\.qq\.com/wwopen/sso/qrConnect\?[^"]+)"')
# 教务首页隐藏域里的学号
_USER_KEY = re.compile(r'id="sessionUserKey"\s+value="(\w+)"')
_JSON_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
}
_PAGE = {"_search": "false", "queryModel.currentPage": "1", "queryModel.sortName": "", "queryModel.sortOrder": "asc", "time": "0"}
# 空教室结果里剔掉的场地类别：上不了自习的
_NOT_STUDY_ROOMS = ("运动", "体育", "球场", "操场", "田径", "游泳", "办公", "会议", "仓库", "宿舍")
# 学业情况页：顶部提示框（去掉空白后）与各类课程的学分行，写法沿用 openschoolcn/zfn_api
_DECIMAL = re.compile(r"\d+\.\d*")
_ACADEMIA_PLAN = re.compile(r"计划总课程(\d+)门通过(\d+)门?.*未通过(\d+)门?.*未修(\d+)?.*在读(\d+)门?.*计划外?.*通过(\d+)门?.*未通过(\d+)门")
_ACADEMIA_CATEGORY = re.compile(
    r"\"(.*)&nbsp.*要求学分.*:(\d+\.\d*|0|&nbsp;).*获得学分.*:(\d+\.\d*|0| ).*未获得学分.*:(\d+\.\d*|0| )[\s\S]*?<span id='showKc(.*)'></span>"
)


def _credit(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


class JwError(Exception):
    """教务对接出错，message 可直接给用户看。"""


class JwAuthError(JwError):
    """账号密码不对，或登录凭证已过期：需要学生重新绑定。"""


class JwCaptcha(JwError):
    """需要验证码。image_base64 是验证码图，学生填好后连同账号密码再提交一次。"""

    def __init__(self, image_base64: str) -> None:
        super().__init__("需要输入验证码")
        self.image_base64 = image_base64


def rsa_encrypt(text: str, modulus_hex: str, exponent_hex: str) -> str:
    """与 security.js 的 RSAUtils.encryptedString 输出逐字一致。

    它把字符串按字符码拆开，补 0 到 chunkSize 的整数倍；每两个字符拼成一个 16 位 digit
    （前一个是低字节），chunkSize = 2 × 模数最高 digit 的下标。每块做无填充 RSA，
    结果按 digit 从高到低、每个 digit 4 位十六进制拼出来，块之间用空格隔开。
    """
    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16)
    chunk_size = 2 * ((modulus.bit_length() - 1) // 16)
    if chunk_size <= 0:
        raise JwError("教务公钥异常")
    codes = [ord(c) for c in text]
    codes += [0] * (-len(codes) % chunk_size)
    blocks: list[str] = []
    for start in range(0, len(codes), chunk_size):
        block = 0
        for j in range(chunk_size // 2):
            digit = codes[start + 2 * j] + (codes[start + 2 * j + 1] << 8)
            block += digit << (16 * j)
        hex_text = format(pow(block, exponent, modulus), "x")
        blocks.append(hex_text.rjust(-(-len(hex_text) // 4) * 4, "0"))
    return " ".join(blocks)


def current_term(today: date | None = None) -> tuple[str, str]:
    """正方的学年学期编码：xnm=学年起始年，xqm=3 第一学期 / 12 第二学期。8 月到次年 1 月算第一学期。"""
    today = today or date.today()
    if today.month >= 8:
        return str(today.year), "3"
    if today.month == 1:
        return str(today.year - 1), "3"
    return str(today.year - 1), "12"


class ZhengfangClient:
    """一个学校一套地址。实例不保存密码，只在内存里持有会话 Cookie。"""

    def __init__(self, cas_url: str, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.cas_url = cas_url.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self._jw_host = (urlsplit(self.base_url).hostname or "").lower()
        hosts = frozenset({self._jw_host, (urlsplit(self.cas_url).hostname or "").lower()})
        # 每一跳（包括跳转）都校验：只许去这两个主机，且解析到公网
        self._client = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            verify=ssl_context(),
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            event_hooks={"request": [lambda request: check_url(str(request.url), hosts)]},
        )

    # ---- 会话 Cookie 的导入导出：存进 Redis 前由上层加密 ----
    def export_cookies(self) -> list[list[str]]:
        return [[c.domain, c.path, c.name, c.value or ""] for c in self._client.cookies.jar]

    def load_cookies(self, cookies: list[list[str]]) -> None:
        for domain, path, name, value in cookies:
            self._client.cookies.set(name, value, domain=domain, path=path)

    def close(self) -> None:
        self._client.close()

    def _service(self) -> str:
        return f"{self.base_url}/xtgl/index_initMenu.html"

    def _login_url(self) -> str:
        return f"{self.cas_url}/login?service={quote(self._service(), safe='')}"

    def _on_jw(self, resp: httpx.Response) -> bool:
        """落到教务主机、且不是教务自己的登录页，才算有会话。"""
        return (resp.url.host or "").lower() == self._jw_host and "login" not in resp.url.path.lower()

    def probe(self) -> None:
        """管理员「测一下」：统一身份认证登录页能打开、是正方 CAS 的样子，就算地址填对了。"""
        page = self._client.get(self._login_url())
        if not _EXECUTION.search(page.text):
            raise JwError("打开了，但不像正方统一身份认证的登录页，检查 CAS 地址")
        self._client.get(f"{self.cas_url}/v2/getPubKey").json()

    def login(self, username: str, password: str, captcha: str = "") -> None:
        """用学生的统一身份认证账号登录。需要验证码而没给时抛 JwCaptcha。"""
        page = self._client.get(self._login_url())
        if self._on_jw(page):
            return
        match = _EXECUTION.search(page.text)
        if match is None:
            raise JwError("统一身份认证登录页异常，请稍后再试")

        status = self._client.get(f"{self.cas_url}/v2/getKaptchaStatus").text.strip().lower()
        if status == "true" and not captcha:
            image = self._client.get(f"{self.cas_url}/kaptcha", params={"time": int(time.time() * 1000)})
            raise JwCaptcha(base64.b64encode(image.content).decode())

        pub = self._client.get(f"{self.cas_url}/v2/getPubKey").json()
        # 和浏览器一样提交到表单自己的 action（service 已经记在 CAS 会话里）
        action = _FORM_ACTION.search(page.text)
        resp = self._client.post(
            str(page.url.join(action.group(1))) if action and action.group(1) else self._login_url(),
            data={
                "username": username,
                "password": rsa_encrypt(password[::-1], pub["modulus"], pub["exponent"]),
                "authcode": captcha,
                "rememberMe": "true",
                "execution": match.group(1),
                "_eventId": "submit",
            },
        )
        if not self._on_jw(resp):
            err = _ERROR.search(resp.text)
            if err:
                raise JwAuthError(err.group(1).strip())
            if _EXECUTION.search(resp.text):
                raise JwAuthError("账号、密码或验证码不正确")
            raise JwError("统一身份认证暂时连不上，请稍后再试")

    def wecom_link(self) -> str:
        """CAS 登录页上企业微信按钮的扫码地址；回跳 CAS 后的 service 换成教务，扫完直接落到教务。"""
        page = self._client.get(self._login_url())
        match = _WECOM_LINK.search(page.text)
        if match is None:
            raise JwError("学校的统一身份认证没有开通企业微信扫码登录，请用账号密码绑定")
        parts = urlsplit(html.unescape(match.group(1)))
        query = dict(parse_qsl(parts.query))
        redirect = urlsplit(query.get("redirect_uri", ""))
        if (redirect.hostname or "").lower() != (urlsplit(self.cas_url).hostname or "").lower():
            raise JwError("企业微信扫码回跳的不是学校统一身份认证，请用账号密码绑定")
        query["redirect_uri"] = urlunsplit(redirect._replace(query=urlencode({**dict(parse_qsl(redirect.query)), "service": self._service()})))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def wecom_login(self, redirect_uri: str, code: str, state: str, appid: str) -> str:
        """学生在企业微信里确认后，带 code 回 CAS 换登录态；返回教务里的学号（读不到为空）。"""
        params = urlencode({"code": code, "state": state, "appid": appid})
        try:
            resp: httpx.Response | None = self._client.get(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{params}")
        except UnsafeUrlError:
            # CAS 坚持跳回门户等别的站：登录态已经记在 CAS 的 Cookie 里，下面再走一次教务的 service 就行
            resp = None
        err = _ERROR.search(resp.text) if resp is not None else None
        if resp is None or not self._on_jw(resp):
            resp = self._client.get(self._login_url())
        if not self._on_jw(resp):
            err = err or _ERROR.search(resp.text)
            raise JwAuthError(err.group(1).strip() if err else "企业微信登录没成功，可能是你的企业微信还没关联学号，请改用账号密码绑定")
        match = _USER_KEY.search(resp.text)
        return match.group(1) if match else ""

    def _renew(self) -> None:
        """教务会话过期：带 CASTGC 再走一次 CAS 换票；CAS 也要求登录就是凭证过期了。

        落到别处（CAS 报 502、教务维护公告页）只算暂时连不上，不能让上层把学生解绑。
        """
        resp = self._client.get(self._login_url())
        if self._on_jw(resp):
            return
        if _EXECUTION.search(resp.text):
            raise JwAuthError("教务登录已过期，请重新绑定教务账号")
        raise JwError("教务系统暂时连不上，请稍后再试")

    def _query(self, path: str, data: dict[str, str], params: dict[str, str]) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            resp = self._client.post(url, params=params, data=data, headers=_JSON_HEADERS)
            # 会话失效时教务会经 CAS 绕一圈落回首页，状态码照样是 200；被跳转过就不是本次查询的结果
            if self._on_jw(resp) and not resp.history:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise JwError("教务返回的数据无法解析") from exc
            if attempt == 0:
                self._renew()
        raise JwError("教务系统暂时连不上，请稍后再试")

    # ---------------- 只读查询 ----------------

    def _page(self, path: str, params: dict[str, str]) -> str:
        """GET 一个教务页面；被跳转过（会话失效后经 CAS 绕回来）就续一次会话再取。"""
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, params=params)
        if not self._on_jw(resp) or resp.history:
            self._renew()
            resp = self._client.get(url, params=params)
        return resp.text

    def current_week(self) -> int | None:
        page = self._page("/kbcx/xskbcxMobile_cxTimeTableIndex.html", {"gnmkdm": "Y2105", "layout": "default"})
        match = _CURRENT_WEEK.search(page)
        return int(match.group(1)) if match else None

    def timetable(self, xnm: str, xqm: str) -> dict[str, Any]:
        """个人课表：课程列表 + 每节课的起止时间。"""
        data = self._query("/kbcx/xskbcx_cxXsgrkb.html", {"xnm": xnm, "xqm": xqm, "kzlx": "ck", "xsdm": ""}, {"gnmkdm": "N2151"})
        rows = data.get("kbList", []) if isinstance(data, dict) else []
        campus = next((str(r.get("xqh_id")) for r in rows if r.get("xqh_id")), "")
        periods: list[dict[str, str]] = []
        if campus:
            raw = self._query("/kbcx/xskbcx_cxRjc.html", {"xnm": xnm, "xqm": xqm, "xqh_id": campus}, {"gnmkdm": "N2151"})
            periods = [
                {"period": str(p.get("jcmc", "")), "start": str(p.get("qssj", "")), "end": str(p.get("jssj", ""))}
                for p in (raw if isinstance(raw, list) else [])
            ]
        courses = [
            {
                "course": r.get("kcmc", ""),
                "weekday": int(r.get("xqj") or 0),
                "periods": r.get("jcs", ""),
                "weeks": r.get("zcd", ""),
                "room": r.get("cdmc", ""),
                "teacher": r.get("xm", ""),
            }
            for r in rows
        ]
        return {"courses": courses, "periods": periods, "campus_id": campus}

    def grades(self, xnm: str = "", xqm: str = "") -> list[dict[str, str]]:
        """成绩。学年学期留空表示全部学期。"""
        data = self._query(
            "/cjcx/cjcx_cxXsgrcj.html",
            {"xnm": xnm, "xqm": xqm, "queryModel.showCount": "300", **_PAGE},
            {"doType": "query", "gnmkdm": "N305005"},
        )
        return [
            {"term": f"{r.get('xnmmc', '')}-{r.get('xqmmc', '')}", "course": r.get("kcmc", ""), "score": r.get("cj", ""),
             "credit": r.get("xf", ""), "gpa": r.get("jd", ""), "type": r.get("kcxzmc", ""), "exam": r.get("ksxz", "")}
            for r in (data.get("items", []) if isinstance(data, dict) else [])
        ]

    def exams(self, xnm: str, xqm: str) -> list[dict[str, str]]:
        data = self._query(
            "/kwgl/kscx_cxXsksxxIndex.html",
            {"xnm": xnm, "xqm": xqm, "queryModel.showCount": "100", **_PAGE},
            {"doType": "query", "gnmkdm": "N358105"},
        )
        return [
            {"course": r.get("kcmc", ""), "time": r.get("kssj", ""), "room": r.get("cdmc", ""),
             "campus": r.get("cdxqmc", ""), "name": r.get("ksmc", ""), "seat": r.get("zwh", "")}
            for r in (data.get("items", []) if isinstance(data, dict) else [])
        ]

    def free_rooms(self, xnm: str, xqm: str, campus_id: str, week: int, weekday: int, periods: list[int]) -> list[dict[str, str]]:
        """空教室：周次、星期、节次都用位图传（第 n 项 = 1 << (n-1)）。

        场地类别各校叫法不一（「多媒体」「普通教室」「智慧教室」都有），所以只剔掉明显不能上自习的场地，
        类别为空的照样保留。
        """
        data = self._query(
            "/cdjy/cdjy_cxKxcdlb.html",
            {
                "fwzt": "cx", "xqh_id": campus_id, "xnm": xnm, "xqm": xqm, "cdlb_id": "", "cdejlb_id": "",
                "qszws": "", "jszws": "", "cdmc": "", "lh": "", "jyfs": "0", "cdjylx": "",
                "zcd": str(1 << (week - 1)), "xqj": str(weekday), "jcd": str(sum(1 << (p - 1) for p in periods)),
                "queryModel.showCount": "300", **{**_PAGE, "queryModel.sortName": "cdbh"},
            },
            {"doType": "query", "gnmkdm": "N2155"},
        )
        rows = data.get("items", []) if isinstance(data, dict) else []
        return [
            {"room": r.get("cdmc", ""), "building": r.get("jxlmc", "") or "", "type": r.get("cdlbmc", "") or "", "seats": str(r.get("zws", ""))}
            for r in rows
            if not any(word in str(r.get("cdlbmc") or "") for word in _NOT_STUDY_ROOMS)
        ]

    def academia(self) -> dict[str, Any]:
        """学业情况（gnmkdm=N105515）：平均绩点、培养计划内外的课程通过情况、各类课程要求学分与已获学分。

        页面是 HTML，没有 JSON 接口；解析方式与 openschoolcn/zfn_api 的 get_academia 一致。
        各类课程的逐门明细要每类再发一次请求，这里不取：问到具体哪门课时由成绩工具回答。
        """
        page = self._page("/xsxy/xsxyqk_cxXsxyqkIndex.html", {"gnmkdm": "N105515", "layout": "default"})
        soup = BeautifulSoup(page, "html.parser")
        danger = soup.select_one("div.alert-danger")
        if danger is not None:
            raise JwError(danger.get_text(" ", strip=True)[:100] or "学业情况暂时查不了")
        alert = soup.select_one("div#alertBox")
        summary = "".join((alert.get_text() if alert else "").split())
        if not summary and "showKc" not in page:
            raise JwError("教务没有返回学业情况，可能本校没开放这个查询")
        gpa = _DECIMAL.search(summary)
        result: dict[str, Any] = {"gpa": float(gpa.group(0)) if gpa else None, "plan": None, "categories": []}
        plan = _ACADEMIA_PLAN.search(summary)
        if plan:
            total, passed, failed, missed, studying, extra_passed, extra_failed = (int(n or 0) for n in plan.groups())
            result["plan"] = {"total": total, "passed": passed, "failed": failed, "not_taken": missed, "in_progress": studying,
                              "extra_passed": extra_passed, "extra_failed": extra_failed}
        seen: set[str] = set()
        for name, required, earned, missing, anchor in _ACADEMIA_CATEGORY.findall(page):
            name = html.unescape(name).strip()
            # 贪婪匹配会从页头的长串里误抓到「主修」「某级某专业」这类根节点，名字太长、锚点不干净或重复的跳过
            if not name or len(name) > 20 or "span" in anchor or name in seen:
                continue
            seen.add(name)
            result["categories"].append({"name": name, "required": _credit(required), "earned": _credit(earned), "missing": _credit(missing)})
        return result
