"use client";

import { useEffect, useRef, useState } from "react";
import { PencilIcon } from "@/components/SketchIcons";
import { type JwAccount, type WecomStatus, errorDetail, pollJwWecom, startJwWecom } from "@/lib/api";

type Phase = "loading" | Exclude<WecomStatus, "bound"> | "error";

const POLL_GAP_MS = 1000;

const HINT: Record<Exclude<Phase, "error">, string> = {
  loading: "正在向学校要一张二维码…",
  waiting: "打开企业微信，扫一扫上面的二维码",
  scanned: "扫到了！在企业微信里点「确认登录」",
  cancelled: "你在企业微信里取消了，想绑定的话再扫一次",
  expired: "二维码过期了，点一下换一张",
};

/**
 * 复刻学校统一身份认证的「企业微信」登录：二维码由服务器从企业微信扫码页转过来，
 * 学生确认后服务器拿 code 回 CAS 换登录凭证，这里一直长轮询到绑定成功或二维码过期。
 */
export function WecomQrLogin({ onBound }: { onBound: (account: JwAccount) => void }) {
  const [qr, setQr] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  const onBoundRef = useRef(onBound);

  useEffect(() => {
    onBoundRef.current = onBound;
  }, [onBound]);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    (async () => {
      try {
        const started = await startJwWecom();
        if (!alive) return;
        setQr(started.qr);
        setPhase("waiting");
        while (alive) {
          const sent = Date.now();
          const result = await pollJwWecom(started.id, controller.signal);
          if (!alive) return;
          if (result.status === "bound") {
            if (result.account) onBoundRef.current(result.account);
            return;
          }
          setPhase(result.status);
          if (result.status === "expired") return;
          // 正常一轮要挂十几秒；秒回（服务器忙、刚取消）时别连着打
          await new Promise((resolve) => setTimeout(resolve, Math.max(0, POLL_GAP_MS - (Date.now() - sent))));
        }
      } catch (err) {
        if (!alive) return;
        setError(errorDetail(err));
        setPhase("error");
      }
    })();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [round]);

  function refresh() {
    setQr(null);
    setError("");
    setPhase("loading");
    setRound((n) => n + 1);
  }

  const dimmed = phase === "scanned" || phase === "expired" || phase === "error";

  return (
    <div className="flex flex-col items-center gap-4 pt-1">
      <div className="sketch relative grid size-56 place-items-center overflow-hidden bg-white p-3">
        {qr ? (
          // eslint-disable-next-line @next/next/no-img-element -- 企业微信给的一次性二维码
          <img
            src={`data:image/png;base64,${qr}`}
            alt="企业微信登录二维码"
            className={`size-full transition duration-300 ${dimmed ? "scale-95 opacity-15 blur-[2px]" : ""}`}
          />
        ) : (
          phase !== "error" && <PencilIcon size={36} className="pencil-bob text-pink" />
        )}
        {phase === "waiting" && <span className="qr-scan" aria-hidden="true" />}
        {phase === "scanned" && (
          <span className="pop-in absolute grid place-items-center gap-1 text-center">
            <span className="grid size-14 place-items-center rounded-full bg-pink font-display text-3xl text-paper">✓</span>
            <span className="font-display text-lg">已扫码</span>
          </span>
        )}
        {(phase === "expired" || phase === "error") && (
          <button type="button" onClick={refresh} className="btn btn-primary pop-in absolute">
            换一张二维码
          </button>
        )}
      </div>

      <p className="text-center text-[0.9375rem]" role="status">
        {phase === "error" ? <span className="text-danger">{error}</span> : HINT[phase]}
      </p>
      {phase === "cancelled" && (
        <button type="button" onClick={refresh} className="link-ink text-sm">
          换一张二维码
        </button>
      )}
      <p className="text-center text-sm text-muted">
        走的是学校统一身份认证的企业微信登录，和官网扫码一样；服务器只留加密后的登录凭证，不经手密码。
      </p>
    </div>
  );
}
