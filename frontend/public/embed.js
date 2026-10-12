/**
 * 智悦校园 · 嵌入挂件加载器。学校官网只需加一行：
 *   <script src="https://<智悦站点>/embed.js" data-embed-id="<挂件 id>" defer></script>
 *
 * 流程：在宿主页面向 /api/embed/<id>/token 申请访客令牌（浏览器带上宿主 Origin，
 * 服务端按挂件的允许网站逐字比对），再把令牌放进 iframe 地址的 hash 里打开聊天页。
 * 令牌缓存在宿主站的 sessionStorage，关掉标签页即失效。
 */
(function () {
  var script = document.currentScript;
  if (!script) return;
  var embedId = script.getAttribute("data-embed-id");
  if (!embedId || !/^[A-Za-z0-9_-]{1,64}$/.test(embedId)) return;

  var base = new URL(script.src).origin;
  var tokenKey = "zhiyue-embed-token:" + embedId;
  var visitorKey = "zhiyue-embed-visitor";
  var frame = null;
  var opening = false;

  function visitorId() {
    try {
      var id = localStorage.getItem(visitorKey);
      if (!id) {
        id = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random().toString(16).slice(2);
        localStorage.setItem(visitorKey, id);
      }
      return id;
    } catch {
      return "";
    }
  }

  function cachedSession() {
    try {
      var raw = sessionStorage.getItem(tokenKey);
      var session = raw ? JSON.parse(raw) : null;
      // 提前 5 分钟视为过期，避免聊到一半令牌失效
      return session && session.expiresAt - 300000 > Date.now() ? session : null;
    } catch {
      return null;
    }
  }

  function requestSession() {
    return fetch(base + "/api/embed/" + encodeURIComponent(embedId) + "/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitorId: visitorId() }),
    }).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok) throw new Error(body.message || "聊天挂件暂不可用");
        var session = {
          token: body.token,
          assistantName: body.assistantName,
          tenantName: body.tenantName,
          expiresAt: Date.now() + body.expiresIn * 1000,
        };
        try {
          sessionStorage.setItem(tokenKey, JSON.stringify(session));
        } catch {
          // 无痕模式写不进去也不影响本次打开
        }
        return session;
      });
    });
  }

  var panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;right:20px;bottom:88px;width:380px;max-width:calc(100vw - 40px);height:560px;" +
    "max-height:calc(100vh - 120px);border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.25);" +
    "display:none;z-index:2147483646;background:#0c1319;";

  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "打开校园助手");
  button.textContent = "问";
  button.style.cssText =
    "position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;" +
    "background:linear-gradient(135deg,#c8954a,#e2b866);color:#1a1208;font-size:22px;font-weight:700;" +
    "box-shadow:0 6px 20px rgba(0,0,0,.25);z-index:2147483647;";

  function open() {
    if (frame) {
      panel.style.display = panel.style.display === "none" ? "block" : "none";
      return;
    }
    if (opening) return;
    opening = true;
    var session = cachedSession();
    (session ? Promise.resolve(session) : requestSession())
      .then(function (s) {
        var hash = new URLSearchParams({
          token: s.token,
          name: s.assistantName || "",
          school: s.tenantName || "",
        });
        frame = document.createElement("iframe");
        frame.src = base + "/embed/" + encodeURIComponent(embedId) + "#" + hash.toString();
        frame.title = "校园助手";
        frame.style.cssText = "width:100%;height:100%;border:0;";
        panel.appendChild(frame);
        panel.style.display = "block";
      })
      .catch(function (err) {
        button.title = err.message;
        console.warn("[智悦校园挂件]", err.message);
      })
      .then(function () {
        opening = false;
      });
  }

  button.addEventListener("click", open);
  document.body.appendChild(panel);
  document.body.appendChild(button);
})();
