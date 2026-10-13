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
    "position:fixed;right:16px;bottom:96px;width:380px;max-width:calc(100vw - 32px);height:560px;" +
    "max-height:calc(100vh - 120px);border:2px solid #1b1214;border-radius:16px;overflow:hidden;" +
    "box-shadow:0 10px 28px rgba(27,18,20,.18);display:none;z-index:2147483646;background:#fffaf4;";

  // 启动器：桌宠从页面右下角探出半个脑袋，眼睛跟着鼠标转。坐标系与 hooks/useDeskPet.ts 的 PET_VIEWBOX / PET_EYES 一致
  var SVG_NS = "http://www.w3.org/2000/svg";
  var EYES = [
    { cx: 247, cy: 257, rx: 60, ry: 54 },
    { cx: 398, cy: 248, rx: 65, ry: 55 },
  ];
  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "打开校园助手");
  button.setAttribute("aria-expanded", "false");
  button.title = "有问题问我";
  button.style.cssText =
    "position:fixed;right:14px;bottom:-18px;width:96px;height:96px;padding:0;border:0;background:none;cursor:pointer;" +
    "z-index:2147483647;transition:transform .18s cubic-bezier(.3,1.5,.5,1);-webkit-tap-highlight-color:transparent;";

  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    for (var key in attrs) el.setAttribute(key, attrs[key]);
    return el;
  }

  var head = svgEl("svg", { viewBox: "40 0 566 560", width: "96", height: "96", "aria-hidden": "true" });
  head.style.cssText = "display:block;overflow:visible;filter:drop-shadow(0 3px 4px rgba(27,18,20,.18));";
  head.appendChild(svgEl("image", { href: base + "/pet/girl.webp", width: "646", height: "634" }));
  var pupils = EYES.map(function (eye, i) {
    var clipId = "zhiyue-eye-" + i;
    var clip = svgEl("clipPath", { id: clipId });
    clip.appendChild(svgEl("ellipse", { cx: eye.cx, cy: eye.cy, rx: eye.rx, ry: eye.ry }));
    head.appendChild(clip);
    var holder = svgEl("g", { "clip-path": "url(#" + clipId + ")" });
    var pupil = svgEl("g", {});
    pupil.style.transition = "transform .12s ease-out";
    pupil.appendChild(svgEl("ellipse", { cx: eye.cx, cy: eye.cy, rx: "11", ry: "14", fill: "#1b1214" }));
    pupil.appendChild(svgEl("circle", { cx: eye.cx + 4, cy: eye.cy - 5, r: "3.6", fill: "#fff" }));
    holder.appendChild(pupil);
    head.appendChild(holder);
    return pupil;
  });
  button.appendChild(head);

  function look(ev) {
    var rect = head.getBoundingClientRect();
    var scale = rect.width / 566;
    EYES.forEach(function (eye, i) {
      var dx = ev.clientX - (rect.left + (eye.cx - 40) * scale);
      var dy = ev.clientY - (rect.top + eye.cy * scale);
      var d = Math.hypot(dx, dy) || 1;
      var reach = Math.min(d / 240, 1);
      var x = (dx / d) * reach * (eye.rx - 22);
      var y = (dy / d) * reach * (eye.ry - 24);
      pupils[i].style.transform = "translate(" + x.toFixed(1) + "px," + y.toFixed(1) + "px)";
    });
  }

  function lift(up) {
    button.style.transform = up ? "translateY(-10px)" : "";
  }

  function open() {
    if (frame) {
      var show = panel.style.display === "none";
      panel.style.display = show ? "block" : "none";
      button.setAttribute("aria-expanded", String(show));
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
        button.setAttribute("aria-expanded", "true");
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
  button.addEventListener("pointerenter", function () { lift(true); });
  button.addEventListener("pointerleave", function () { lift(false); });
  button.addEventListener("focus", function () { lift(true); });
  button.addEventListener("blur", function () { lift(false); });
  window.addEventListener("pointermove", look, { passive: true });
  document.body.appendChild(panel);
  document.body.appendChild(button);
})();
