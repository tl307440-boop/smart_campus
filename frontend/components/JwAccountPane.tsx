"use client";

import { useCallback, useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { WecomQrLogin } from "@/components/WecomQrLogin";
import { type JwAccount, bindJwAccount, errorDetail, fetchJwAccount, unbindJwAccount } from "@/lib/api";

type Method = "wecom" | "password";

const METHOD_LABEL: Record<Method, string> = { wecom: "企业微信扫码", password: "账号密码" };

/**
 * 学生绑定自己的教务账号，和学校统一身份认证一样两种登录方式：
 * 企业微信扫码（见 WecomQrLogin），或账号密码——密码只用来登录这一次，服务器只留登录后的凭证；
 * 学校要验证码时把图转给学生，填好后连同账号密码再提交一次。
 */
export function JwAccountPane() {
  const [account, setAccount] = useState<JwAccount | null>(null);
  const [method, setMethod] = useState<Method>("wecom");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captcha, setCaptcha] = useState("");
  const [captchaImage, setCaptchaImage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchJwAccount()
      .then((next) => !cancelled && setAccount(next))
      .catch(() => !cancelled && setNotice({ text: "没读到绑定状态，稍后再打开看看", error: true }));
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleBind() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await bindJwAccount({ username, password, captcha: captchaImage ? captcha : "" });
      if ("captcha" in result) {
        setCaptchaImage(result.captcha);
        setCaptcha("");
        setNotice({ text: "学校要求输入验证码，填好后再点一次绑定。", error: false });
        return;
      }
      setAccount(result);
      setPassword("");
      setCaptcha("");
      setCaptchaImage(null);
      setNotice({ text: "绑定好了，现在可以直接问「我明天有什么课」。", error: false });
    } catch (err) {
      setCaptchaImage(null);
      setNotice({ text: errorDetail(err), error: true });
    } finally {
      setBusy(false);
    }
  }

  const handleWecomBound = useCallback((next: JwAccount) => {
    setAccount(next);
    setNotice({ text: "扫码绑定好了，现在可以直接问「我明天有什么课」。", error: false });
  }, []);

  async function handleUnbind() {
    setBusy(true);
    setNotice(null);
    try {
      setAccount(await unbindJwAccount());
      setNotice({ text: "已解绑，服务器上的登录凭证已删除。", error: false });
    } catch (err) {
      setNotice({ text: errorDetail(err), error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <PaneTitle title="教务账号">
        绑定后可以直接问课表、考试安排、成绩和空教室。只做查询，不会替你选课或提交任何申请。
      </PaneTitle>

      {!account ? (
        <p className="text-muted">读取中…</p>
      ) : account.mode === "off" ? (
        <p className="sketch bg-skin px-3 py-2 text-[0.9375rem]">学校还没有开通教务查询。</p>
      ) : account.mode === "demo" ? (
        <p className="sketch bg-skin px-3 py-2 text-[0.9375rem]">
          学校目前用的是演示数据，不用绑定，问课表时看到的是样例。
        </p>
      ) : account.bound ? (
        <div className="space-y-3">
          <p>
            已绑定 <span className="font-display">{account.username}</span>
            {account.boundAt && (
              <span className="ml-2 text-sm text-muted">{new Date(account.boundAt * 1000).toLocaleDateString("zh-CN")} 绑定</span>
            )}
          </p>
          <p className="text-sm text-muted">登录凭证过期（一般是改了密码或太久没用）后，问课表时会提醒你重新绑定。</p>
          <button type="button" disabled={busy} onClick={() => void handleUnbind()} className="btn btn-sm">
            解绑
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <div role="tablist" aria-label="绑定方式" className="flex gap-5 border-b border-rule pb-2">
            {(Object.keys(METHOD_LABEL) as Method[]).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={item === method}
                onClick={() => {
                  setMethod(item);
                  setNotice(null);
                }}
                className={`font-display text-base ${item === method ? "marker marker-swipe text-ink" : "text-muted hover:text-ink"}`}
              >
                {METHOD_LABEL[item]}
              </button>
            ))}
          </div>
          {method === "wecom" ? (
            <WecomQrLogin onBound={handleWecomBound} />
          ) : (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                void handleBind();
              }}
            >
              <label className="block">
                <span className="field-label">学号（统一身份认证账号）</span>
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  className="field"
                />
              </label>
              <label className="block">
                <span className="field-label">统一身份认证密码</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  className="field"
                />
              </label>
              {captchaImage && (
                <label className="block">
                  <span className="field-label">验证码</span>
                  <span className="flex items-center gap-3">
                    {/* eslint-disable-next-line @next/next/no-img-element -- 学校 CAS 给的一次性验证码图 */}
                    <img src={`data:image/jpeg;base64,${captchaImage}`} alt="验证码" className="sketch h-10 bg-white" />
                    <input
                      value={captcha}
                      onChange={(event) => setCaptcha(event.target.value)}
                      autoComplete="off"
                      className="field flex-1"
                    />
                  </span>
                </label>
              )}
              <p className="text-sm text-muted">密码只用来登录这一次，不会保存；服务器只留加密后的登录凭证，随时可以解绑删除。</p>
              <button
                type="submit"
                disabled={busy || !username.trim() || !password || (Boolean(captchaImage) && !captcha.trim())}
                className="btn btn-primary"
              >
                {busy ? "登录中…" : "绑定"}
              </button>
            </form>
          )}
        </div>
      )}
      {notice && (
        <p className={`mt-3 text-[0.9375rem] ${notice.error ? "text-danger" : "text-muted"}`} role="status">
          {notice.text}
        </p>
      )}
    </section>
  );
}
