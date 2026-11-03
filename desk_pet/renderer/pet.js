/**
 * pet.js — 独立桌面摸鱼解压 & 校园小助手桌宠
 * 包含：眼睛跟踪、闲置睡眠、戳一戳、摸头、拖动吸附、快捷启动 Cursor/Docker/自定义软件、校园课表与考试提醒联动
 */

// ─────────────────────────────────────────────
// 常量
// ─────────────────────────────────────────────
const PET_VIEWBOX  = { w: 646, h: 634 };
const PET_WIDTH    = 130;
const PET_HEIGHT   = 130;
const PET_EYES = [
  { cx: 247, cy: 257, rx: 60, ry: 54, restX: 280, restY: 241, thinkX: 24, thinkY: -20 },
  { cx: 398, cy: 248, rx: 65, ry: 55, restX: 367, restY: 237, thinkX: 26, thinkY: -20 },
];
const MOUTH_CENTER = { x: 336, y: 339 };
const INK  = '#1b1214';
const MOUTH_COLOR = '#e8487e';
const SKIN = '#fde3e9';

const SLEEP_AFTER_MS  = 6000;
const BLINK_MS        = 130;
const DRAG_THRESHOLD  = 5;

// 默认台词
const BASE_POKE_LINES = [
  '干嘛戳我～',
  '嘿嘿',
  '今天也要加油鸭！',
  '代码写累了？摸会儿鱼吧～',
  '想要打开 Cursor 还是 Docker 呀？',
  '双击我随时帮你开软件或看课表哦！',
];
const PET_LINES  = ['好舒服～', '再摸摸嘛', '嘿嘿嘿，好惬意～'];
const WAKE_LINES = ['啊…我没睡着！', '嗯？要开始写代码了吗？', '你回来啦！'];

const pick = arr => arr[Math.floor(Math.random() * arr.length)];

// ─────────────────────────────────────────────
// DOM 元素引用
// ─────────────────────────────────────────────
const $pet       = document.getElementById('pet');
const $motion    = document.getElementById('pet-motion');
const $body      = document.getElementById('pet-body');
const $bubble    = document.getElementById('bubble');
const $bubbleTxt = document.getElementById('bubble-text');
const $bubbleAct = document.getElementById('bubble-actions');
const $customBtn = document.getElementById('custom-app-btn');
const $campusBtn = document.getElementById('campus-app-btn');
const $think     = document.getElementById('think');
const $exclaim   = document.getElementById('exclaim');
const $zzz       = document.getElementById('zzz');
const $orbit     = document.getElementById('orbit');
const $blush     = document.getElementById('blush');
const $noseBub   = document.getElementById('nose-bubble');
const $mouthW    = document.getElementById('mouth-wrap');
const $mouthS    = document.getElementById('mouth-shape');
const $lid0      = document.getElementById('lid0');
const $lid1      = document.getElementById('lid1');
const $eye0line  = document.getElementById('eye0-line');
const $eye1line  = document.getElementById('eye1-line');
const $gaze0     = document.getElementById('eye0-gaze');
const $gaze1     = document.getElementById('eye1-gaze');

// ─────────────────────────────────────────────
// 状态
// ─────────────────────────────────────────────
let mood       = 'normal';
let asleep     = false;
let blinking   = false;
let reaction   = null;
let thinking   = false;
let bounceN    = 0;

let pressing   = false;
let dragging   = false;
let pressStart = null;
let swingVal   = 0;

let pointer    = null;
let rafId      = 0;

const cur  = { fx: 0, fy: 0, tilt: 0, eyes: [{ x: 0, y: 0 }, { x: 0, y: 0 }] };
const goal = { fx: 0, fy: 0, tilt: 0, eyes: [{ x: 0, y: 0 }, { x: 0, y: 0 }] };

const timers = { reaction: 0, bubble: 0, idle: 0 };
let currentConfig = null;
let currentCampusData = null;

// ─────────────────────────────────────────────
// 鼠标穿透管理
// ─────────────────────────────────────────────
let mouseIgnoring = true;

function setIgnore(ignore) {
  if (mouseIgnoring === ignore) return;
  mouseIgnoring = ignore;
  if (window.petAPI) {
    window.petAPI.setIgnoreMouseEvents(ignore, { forward: true });
  }
}

window.addEventListener('mousemove', e => {
  if (pressing || dragging) {
    setIgnore(false);
    return;
  }
  const petRect = $pet.getBoundingClientRect();
  const bubbleRect = $bubble.classList.contains('show') ? $bubble.getBoundingClientRect() : null;

  const inPet = e.clientX >= petRect.left && e.clientX <= petRect.right &&
                e.clientY >= petRect.top  && e.clientY <= petRect.bottom;
  const inBubble = bubbleRect &&
                e.clientX >= bubbleRect.left && e.clientX <= bubbleRect.right &&
                e.clientY >= bubbleRect.top  && e.clientY <= bubbleRect.bottom;

  if (inPet || inBubble) {
    setIgnore(false);
  } else {
    setIgnore(true);
  }
});

// ─────────────────────────────────────────────
// 心情与面部表情更新
// ─────────────────────────────────────────────
function computeMood() {
  if (dragging) return 'dragging';
  if (reaction) return reaction;
  if (thinking) return 'thinking';
  if (asleep)   return 'sleepy';
  return 'normal';
}

function applyMood() {
  const next = computeMood();
  if (next === mood) return;
  mood = next;

  $pet.className = $pet.className.replace(/\bmood-\S+/g, '').trim();
  $pet.classList.add(`mood-${mood}`);

  $blush.style.display   = (mood === 'petted' || mood === 'happy') ? '' : 'none';
  $think.classList.toggle('show', mood === 'thinking');
  $exclaim.classList.toggle('show', mood === 'surprised');
  $zzz.classList.toggle('show', mood === 'sleepy');
  $orbit.classList.toggle('show', mood === 'dizzy');
  $noseBub.style.display = mood === 'sleepy' ? '' : 'none';

  updateMouth();
  updateEyes();
}

function updateMouth() {
  const x = MOUTH_CENTER.x, y = MOUTH_CENTER.y;
  let show = true;

  if (mood === 'sleepy') {
    $mouthW.innerHTML = `
      <ellipse cx="${x}" cy="${y}" rx="45" ry="42" fill="${SKIN}"/>
      <ellipse cx="${x}" cy="${y - 2}" rx="9" ry="7" fill="${MOUTH_COLOR}" stroke="${INK}" stroke-width="5"/>
    `;
  } else if (mood === 'surprised') {
    $mouthW.innerHTML = `
      <ellipse cx="${x}" cy="${y}" rx="45" ry="42" fill="${SKIN}"/>
      <ellipse cx="${x}" cy="${y}" rx="17" ry="21" fill="${MOUTH_COLOR}" stroke="${INK}" stroke-width="6"/>
    `;
  } else if (mood === 'dizzy') {
    $mouthW.innerHTML = `
      <ellipse cx="${x}" cy="${y}" rx="45" ry="42" fill="${SKIN}"/>
      <path d="M${x - 30} ${y} q7.5 -10 15 0 t15 0 t15 0 t15 0"
        fill="none" stroke="${INK}" stroke-width="6" stroke-linecap="round"/>
    `;
  } else if (mood === 'thinking') {
    $mouthW.innerHTML = `
      <ellipse cx="${x}" cy="${y}" rx="45" ry="42" fill="${SKIN}"/>
      <path d="M${x - 18} ${y + 3} Q${x} ${y - 6} ${x + 16} ${y + 5}"
        fill="none" stroke="${INK}" stroke-width="6" stroke-linecap="round"/>
    `;
  } else {
    show = false;
  }

  $mouthW.style.display = show ? '' : 'none';
}

function closedEyePath(kind, e, side) {
  const { cx, cy, rx, ry } = e;
  if (kind === 'sleep') {
    return `M${cx - rx * 0.62} ${cy - 2} Q${cx} ${cy + ry * 0.45} ${cx + rx * 0.62} ${cy - 2}`;
  }
  if (kind === 'smile') {
    return `M${cx - rx * 0.6} ${cy + ry * 0.22} Q${cx} ${cy - ry * 0.5} ${cx + rx * 0.6} ${cy + ry * 0.22}`;
  }
  if (kind === 'squeeze') {
    const tip  = cx + side * rx * 0.32;
    const back = cx - side * rx * 0.45;
    return `M${back} ${cy - ry * 0.4} L${tip} ${cy} L${back} ${cy + ry * 0.4}`;
  }
  if (kind === 'spiral') {
    const pts = Array.from({ length: 41 }, (_, i) => {
      const t = i / 40, a = t * Math.PI * 4.5;
      return `${(cx + Math.cos(a) * ry * 0.62 * t).toFixed(1)} ${(cy + Math.sin(a) * ry * 0.62 * t).toFixed(1)}`;
    });
    return `M${pts.join(' L')}`;
  }
  return '';
}

const CLOSED_EYES_MAP = {
  sleepy: 'sleep', happy: 'smile', petted: 'smile', dragging: 'squeeze', dizzy: 'spiral',
};

function updateEyes() {
  const closedKind = (blinking && mood === 'normal') ? 'sleep' : (CLOSED_EYES_MAP[mood] ?? null);
  const lids  = [$lid0, $lid1];
  const lines = [$eye0line, $eye1line];

  lids.forEach(lid => lid.classList.toggle('closed', !!closedKind));

  lines.forEach((line, i) => {
    if (closedKind) {
      const d = closedEyePath(closedKind, PET_EYES[i], i === 0 ? 1 : -1);
      line.setAttribute('d', d);
      line.style.display = '';
      line.setAttribute('stroke-width', closedKind === 'spiral' ? '6' : '8');
      if (closedKind === 'spiral') {
        line.classList.add('spiral-path');
      } else {
        line.classList.remove('spiral-path');
      }
    } else {
      line.style.display = 'none';
    }
  });
}

// ─────────────────────────────────────────────
// 原版气泡与快捷启动按钮
// ─────────────────────────────────────────────
function say(text, ms = 3200, showButtons = false) {
  clearTimeout(timers.bubble);
  $bubbleTxt.textContent = text;
  
  if (showButtons) {
    updateButtonsUI();
    $bubbleAct.classList.add('show');
  } else {
    $bubbleAct.classList.remove('show');
  }

  $bubble.classList.add('show');
  $bubble.style.animation = 'none';
  $bubble.offsetHeight;
  $bubble.style.animation = '';

  timers.bubble = setTimeout(() => {
    $bubble.classList.remove('show');
    $bubbleAct.classList.remove('show');
  }, ms);
}

// 弹出快捷操作气泡
function showAppChooserBubble() {
  wake();
  doReact('happy', 2000);
  burst('flower', 5);
  doBounce('hop');
  const appDesc = currentConfig?.customAppName ? `还是 ${currentConfig.customAppName}` : '';
  say(`想要打开 Cursor 吗？还是 Docker？${appDesc}`, 6000, true);
}

// 弹出今日课表与考试详情气泡
function showScheduleBubble() {
  wake();
  doReact('happy', 1500);
  doBounce('hop');

  if (!currentConfig?.campusToken) {
    say('还没绑定校园助手呢～右键点我「绑定校园助手」即可同步课表！', 4500, false);
    return;
  }

  if (!currentCampusData || !currentCampusData.bound) {
    say('正在同步校园教务数据…稍等几秒再看哦～', 3000, false);
    window.petAPI?.refreshCampus();
    return;
  }

  const lessons = currentCampusData.today_lessons || [];
  const exams = currentCampusData.upcoming_exams || [];

  if (lessons.length === 0 && exams.length === 0) {
    say('今天完全没有排课和考试，尽情放松吧！🎉', 4000, false);
    return;
  }

  const parts = [];
  if (exams.length > 0) {
    const e = exams[0];
    parts.push(`⚠️ 近期考试: ${e.course} (${e.date} ${e.start || ''})`);
  }
  if (lessons.length > 0) {
    const summary = lessons.map(l => `${l.start || ''} ${l.course}(${l.room || '待定'})`).join('\n');
    parts.push(`📅 今日课表:\n${summary}`);
  }

  say(parts.join('\n\n'), 6000, false);
}

function updateButtonsUI() {
  if ($customBtn) {
    if (currentConfig && currentConfig.customAppName) {
      $customBtn.textContent = `⭐ ${currentConfig.customAppName}`;
      $customBtn.title = `点击打开 ${currentConfig.customAppName}`;
    } else {
      $customBtn.textContent = '➕ 选软件';
    }
  }

  if ($campusBtn) {
    if (currentConfig && currentConfig.campusToken) {
      $campusBtn.textContent = '📚 今日课表';
      $campusBtn.title = '查看今日全部课程与考试';
    } else {
      $campusBtn.textContent = '🎓 绑教务';
      $campusBtn.title = '绑定校园助手同步课表';
    }
  }
}

// 气泡按钮事件
$bubbleAct.addEventListener('click', async e => {
  const btn = e.target.closest('.bubble-btn');
  if (!btn) return;
  const appType = btn.dataset.app;

  if (appType === 'schedule') {
    if (!currentConfig?.campusToken) {
      if (window.petAPI) {
        await window.petAPI.bindCampus();
        currentConfig = await window.petAPI.getConfig();
        updateButtonsUI();
      }
    } else {
      showScheduleBubble();
    }
    return;
  }

  if (appType === 'custom' && (!currentConfig || !currentConfig.customAppPath)) {
    say('请选择你要快捷启动的软件～ 📁', 4000, false);
    if (window.petAPI) {
      const res = await window.petAPI.chooseCustomApp();
      if (res && res.success) {
        currentConfig = await window.petAPI.getConfig();
        updateButtonsUI();
        doReact('happy', 1500);
        burst('heart', 4);
        say(`已记住：${res.name}！这就帮你打开 🚀`, 2500, false);
        window.petAPI.openApp('custom');
      }
    }
    return;
  }

  if (window.petAPI) {
    const res = await window.petAPI.openApp(appType);
    if (res && res.success) {
      doReact('happy', 1500);
      burst('heart', 4);
      doBounce('hop');
      say(`好嘞！正在为你打开 ${res.name} 🚀`, 2500, false);
    } else {
      doReact('surprised', 1500);
      say(`哎呀：${res.error || '打开失败'}`, 3000, false);
    }
  }
});

// ─────────────────────────────────────────────
// 粒子（花 / 心）
// ─────────────────────────────────────────────
function svgFlower(size = 18) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '-10 -10 20 20');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('aria-hidden', 'true');
  [0, 72, 144, 216, 288].forEach(deg => {
    const el = document.createElementNS(ns, 'ellipse');
    el.setAttribute('cx', '0'); el.setAttribute('cy', '-5');
    el.setAttribute('rx', '3.6'); el.setAttribute('ry', '5');
    el.setAttribute('fill', '#7cc8f2');
    el.setAttribute('stroke', INK); el.setAttribute('stroke-width', '0.8');
    el.setAttribute('transform', `rotate(${deg})`);
    svg.appendChild(el);
  });
  const c = document.createElementNS(ns, 'circle');
  c.setAttribute('r', '3'); c.setAttribute('fill', '#ffd34d');
  c.setAttribute('stroke', INK); c.setAttribute('stroke-width', '0.8');
  svg.appendChild(c);
  return svg;
}

function burst(kind, count) {
  for (let i = 0; i < count; i++) {
    const angle = -Math.PI / 2 + ((i - (count - 1) / 2) / count) * Math.PI * 1.3;
    const dist  = 48 + Math.random() * 28;
    const dx    = Math.cos(angle) * dist;
    const dy    = Math.sin(angle) * dist - 10;
    const rot   = (Math.random() - 0.5) * 240;
    const delay = Math.random() * 90;

    const span = document.createElement('span');
    span.className = 'particle';
    span.style.setProperty('--dx', `${dx}px`);
    span.style.setProperty('--dy', `${dy}px`);
    span.style.setProperty('--rot', `${rot}deg`);
    span.style.animationDelay = `${delay}ms`;

    if (kind === 'flower') {
      span.appendChild(svgFlower());
    } else {
      const h = document.createElement('span');
      h.className = 'heart'; h.textContent = '♥';
      span.appendChild(h);
    }

    $pet.appendChild(span);
    setTimeout(() => span.remove(), 1300);
  }
}

// ─────────────────────────────────────────────
// 跳跃动效
// ─────────────────────────────────────────────
function doBounce(kind) {
  bounceN++;
  const cls = kind + (bounceN % 2 ? 'A' : 'B');
  $motion.className = '';
  $motion.offsetHeight;
  $motion.className = cls;
  setTimeout(() => {
    if ($motion.className === cls) $motion.className = '';
  }, 500);
}

// ─────────────────────────────────────────────
// 睡眠与唤醒
// ─────────────────────────────────────────────
function scheduleSleep() {
  clearTimeout(timers.idle);
  timers.idle = setTimeout(() => {
    if (thinking || $bubble.classList.contains('show')) return;
    asleep = true;
    applyMood();
  }, SLEEP_AFTER_MS);
}

function wake() {
  if (asleep) {
    asleep = false;
    doReact('surprised', 800);
    if (Math.random() < 0.5) say(pick(WAKE_LINES));
  }
  scheduleSleep();
}

function doReact(m, ms) {
  clearTimeout(timers.reaction);
  reaction = m;
  applyMood();
  timers.reaction = setTimeout(() => {
    reaction = null;
    applyMood();
  }, ms);
}

// ─────────────────────────────────────────────
// 眨眼循环
// ─────────────────────────────────────────────
function startBlinkLoop() {
  const loop = () => {
    setTimeout(() => {
      if (!reaction && !asleep) {
        blinking = true;
        updateEyes();
        setTimeout(() => {
          blinking = false;
          updateEyes();
        }, BLINK_MS);
      }
      loop();
    }, 1800 + Math.random() * 2600);
  };
  loop();
}

// ─────────────────────────────────────────────
// 拖拽摆放
// ─────────────────────────────────────────────
$pet.addEventListener('pointerdown', async e => {
  if (e.button !== 0) return;
  wake();
  $pet.setPointerCapture(e.pointerId);
  pressing = true;
  setIgnore(false);
  $pet.classList.add('pressed');

  const winPos = window.petAPI ? await window.petAPI.getPos() : { x: 0, y: 0 };
  pressStart = {
    screenX: e.screenX, screenY: e.screenY,
    winX: winPos.x, winY: winPos.y,
    moved: false, lastX: e.screenX, lastT: performance.now(),
  };
});

$pet.addEventListener('pointermove', e => {
  if (!pressStart) {
    detectStroke(e);
    return;
  }

  const dx = e.screenX - pressStart.screenX;
  const dy = e.screenY - pressStart.screenY;

  if (!pressStart.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;

  if (!pressStart.moved) {
    pressStart.moved = true;
    dragging = true;
    applyMood();
  }

  const now = performance.now();
  const vx  = (e.screenX - pressStart.lastX) / Math.max(now - pressStart.lastT, 1);
  swingVal  = Math.max(-22, Math.min(22, swingVal * 0.6 + vx * 9));
  pressStart.lastX = e.screenX;
  pressStart.lastT = now;

  const nx = pressStart.winX + dx;
  const ny = pressStart.winY + dy;
  if (window.petAPI) window.petAPI.move(nx, ny);

  kickRaf();
});

$pet.addEventListener('pointerup', e => endPress(e, false));
$pet.addEventListener('pointercancel', e => endPress(e, true));

const pokes = [];

function endPress(e, cancelled) {
  const p = pressStart;
  pressStart = null;
  pressing = false;
  $pet.classList.remove('pressed');
  if (!p) return;
  $pet.releasePointerCapture?.(e.pointerId);

  if (p.moved) {
    dragging = false;
    applyMood();
    doBounce('land');
    kickRaf();
  } else if (!cancelled) {
    // 单击戳一戳
    const now = performance.now();
    pokes.push(now);
    const recent = pokes.filter(t => now - t < 1600);
    pokes.length = 0;
    pokes.push(...recent);
    if (pokes.length >= 4) {
      pokes.length = 0;
      doReact('dizzy', 1700);
      say('别戳啦，晕了…', 1800);
    } else {
      doReact('happy', 1000);
      burst('flower', 5);
      doBounce('hop');
      // 戳戳台词池：混合基础台词和校园智能提醒
      let availableLines = [...BASE_POKE_LINES];
      if (currentCampusData && currentCampusData.bubble_lines && currentCampusData.bubble_lines.length > 0) {
        availableLines = [...currentCampusData.bubble_lines, ...availableLines];
      }
      if (Math.random() < 0.8) say(pick(availableLines));
    }
  }
}

// 双击桌宠
$pet.addEventListener('dblclick', () => {
  showAppChooserBubble();
});

// 右键桌宠
$pet.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (window.petAPI) {
    window.petAPI.showContextMenu();
  }
});

// 摸头检测
const stroke = { dir: 0, x: 0, t: 0, flips: [] };

function detectStroke(e) {
  const rect = $pet.getBoundingClientRect();
  if (e.clientY > rect.top + rect.height * 0.5) return;
  const now = performance.now();
  const s   = stroke;
  const dt  = now - s.t;
  if (dt < 30) return;
  const mx    = e.clientX - s.x;
  const speed = Math.abs(mx) / dt;
  const dir   = Math.sign(mx);
  s.flips = s.flips.filter(t => now - t < 1400);
  if (dir && s.dir && dir !== s.dir && speed > 0.05 && speed < 2.5) s.flips.push(now);
  Object.assign(s, { dir: dir || s.dir, x: e.clientX, t: now });
  if (s.flips.length >= 3) {
    s.flips = [];
    wake();
    doReact('petted', 1700);
    burst('heart', 3);
    if (Math.random() < 0.6) say(pick(PET_LINES));
  }
}

// ─────────────────────────────────────────────
// 眼睛与身体跟随鼠标
// ─────────────────────────────────────────────
function aim() {
  const scale = PET_WIDTH / PET_VIEWBOX.w;
  goal.tilt = dragging ? swingVal : 0;
  if (dragging) swingVal *= 0.9;

  if (!pointer) return;

  const petRect = $pet.getBoundingClientRect();
  const dx = pointer.x - (petRect.left + PET_WIDTH / 2);
  const dy = pointer.y - (petRect.top  + PET_HEIGHT * 0.42);
  const dist = Math.hypot(dx, dy) || 1;
  const pull = dragging ? 0 : Math.min(dist * 0.04, 6);
  goal.fx = (dx / dist) * pull;
  goal.fy = (dy / dist) * pull;
  if (!dragging) goal.tilt = Math.max(-1, Math.min(1, dx / 500)) * 4;

  PET_EYES.forEach((eye, i) => {
    if (mood === 'thinking') {
      goal.eyes[i] = { x: eye.thinkX, y: eye.thinkY };
      return;
    }
    const ex = pointer.x - (petRect.left + eye.cx * scale);
    const ey = pointer.y - (petRect.top  + eye.cy * scale);
    const d  = Math.hypot(ex, ey) || 1;
    const reach = Math.min(d / 160, 1);
    goal.eyes[i] = {
      x: (ex / d) * reach * (eye.rx - 22),
      y: (ey / d) * reach * (eye.ry - 24),
    };
  });
}

function lerp(from, to) {
  const next = from + (to - from) * 0.14;
  return Math.abs(to - next) < 0.05 ? to : next;
}

function writeCSS() {
  $body.style.setProperty('--fx',   `${cur.fx.toFixed(2)}px`);
  $body.style.setProperty('--fy',   `${cur.fy.toFixed(2)}px`);
  $body.style.setProperty('--tilt', `${cur.tilt.toFixed(2)}deg`);
  [$gaze0, $gaze1].forEach((g, i) => {
    g.style.transform = `translate(${cur.eyes[i].x.toFixed(1)}px, ${cur.eyes[i].y.toFixed(1)}px)`;
  });
}

function step() {
  rafId = 0;
  aim();
  let moving = false;
  const l = (f, t) => { const n = lerp(f, t); if (n !== t) moving = true; return n; };
  cur.fx   = l(cur.fx,   goal.fx);
  cur.fy   = l(cur.fy,   goal.fy);
  cur.tilt = l(cur.tilt, goal.tilt);
  cur.eyes = cur.eyes.map((p, i) => ({ x: l(p.x, goal.eyes[i].x), y: l(p.y, goal.eyes[i].y) }));
  writeCSS();
  if (moving || dragging) kickRaf();
}

function kickRaf() {
  if (!rafId && !document.hidden) rafId = requestAnimationFrame(step);
}

window.addEventListener('pointermove', e => {
  pointer = { x: e.clientX, y: e.clientY };
  wake();
  kickRaf();
}, { passive: true });

document.addEventListener('visibilitychange', () => {
  if (document.hidden && rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }
});

// ─────────────────────────────────────────────
// 初始化
// ─────────────────────────────────────────────
async function init() {
  applyMood();
  writeCSS();
  startBlinkLoop();
  scheduleSleep();

  if (window.petAPI) {
    currentConfig = await window.petAPI.getConfig();
    currentCampusData = await window.petAPI.getCampusData();
    updateButtonsUI();

    window.petAPI.onConfigChanged(cfg => {
      currentConfig = cfg;
      updateButtonsUI();
    });

    window.petAPI.onCampusDataUpdated(data => {
      currentCampusData = data;
      updateButtonsUI();
      if (data && data.bubble_lines && data.bubble_lines.length > 0) {
        say(data.bubble_lines[0], 5000);
      }
    });

    window.petAPI.onShowAppBubble(() => {
      showAppChooserBubble();
    });

    window.petAPI.onShowScheduleBubble(() => {
      showScheduleBubble();
    });
  }

  setTimeout(() => {
    if (currentCampusData && currentCampusData.bubble_lines && currentCampusData.bubble_lines.length > 0) {
      say(currentCampusData.bubble_lines[0], 5000, true);
    } else {
      showAppChooserBubble();
    }
  }, 1000);
}

init();
