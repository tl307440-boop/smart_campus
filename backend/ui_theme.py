"""智悦校园 UI 主题（方案 A：暗场皮肤，仅视觉层）。"""

import streamlit as st

_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700&display=swap');

:root {
  --zy-bg: #0b1218;
  --zy-bg-elevated: #121a22;
  --zy-surface: #18222c;
  --zy-border: rgba(212, 163, 90, 0.18);
  --zy-text: #e8e4dc;
  --zy-muted: #9aa3ad;
  --zy-accent: #d4a35a;
  --zy-accent-soft: rgba(212, 163, 90, 0.14);
  --zy-glow: rgba(212, 163, 90, 0.08);
  --zy-user-bubble: #243140;
  --zy-assistant-bubble: #152029;
  --zy-danger: #c97b7b;
  --zy-radius: 14px;
  --zy-font-display: "Noto Serif SC", "Songti SC", serif;
  --zy-font-body: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--zy-bg) !important;
  color: var(--zy-text) !important;
  font-family: var(--zy-font-body) !important;
}

[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(900px 420px at 50% -10%, var(--zy-glow), transparent 60%),
    radial-gradient(700px 500px at 100% 100%, rgba(61, 99, 120, 0.12), transparent 55%),
    var(--zy-bg) !important;
}

[data-testid="stHeader"] {
  background: transparent !important;
  height: 0 !important;
  min-height: 0 !important;
}

[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] {
  display: none !important;
  visibility: hidden !important;
}

#MainMenu, footer, header {
  visibility: hidden !important;
}

.block-container {
  padding-top: 1.6rem !important;
  padding-bottom: 3rem !important;
  max-width: 760px !important;
}

h1, h2, h3, .stMarkdown h1, .stMarkdown h2 {
  font-family: var(--zy-font-display) !important;
  color: var(--zy-text) !important;
  letter-spacing: 0.02em;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #101820 0%, #0c1319 100%) !important;
  border-right: 1px solid var(--zy-border) !important;
}

[data-testid="stSidebar"] * {
  color: var(--zy-text) !important;
}

[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaption"] {
  color: var(--zy-muted) !important;
}

[data-testid="stSidebar"] hr {
  border-color: var(--zy-border) !important;
}

.stButton > button {
  border-radius: 999px !important;
  border: 1px solid var(--zy-border) !important;
  background: var(--zy-surface) !important;
  color: var(--zy-text) !important;
  font-family: var(--zy-font-body) !important;
  transition: border-color 0.2s ease, background 0.2s ease, transform 0.15s ease;
}

.stButton > button:hover {
  border-color: rgba(212, 163, 90, 0.45) !important;
  background: var(--zy-accent-soft) !important;
  transform: translateY(-1px);
}

.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: linear-gradient(135deg, #c8954a, #d4a35a) !important;
  color: #1a1208 !important;
  border: none !important;
  font-weight: 600 !important;
}

.stButton > button:focus-visible {
  outline: 2px solid var(--zy-accent) !important;
  outline-offset: 2px !important;
}

[data-testid="stChatMessage"] {
  background: var(--zy-assistant-bubble) !important;
  border: 1px solid var(--zy-border) !important;
  border-radius: var(--zy-radius) !important;
  padding: 0.85rem 1rem !important;
  margin-bottom: 0.65rem !important;
  animation: zy-fade-in 0.35s ease both;
}

[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]),
[data-testid="stChatMessage"]:has(img[alt="user avatar"]) {
  background: var(--zy-user-bubble) !important;
}

[data-testid="stChatInput"] {
  border-top: 1px solid var(--zy-border) !important;
}

[data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] [data-baseweb="textarea"] {
  background: var(--zy-surface) !important;
  color: var(--zy-text) !important;
  border-radius: 18px !important;
}

.stTextInput input, .stSelectbox [data-baseweb="select"] > div,
[data-baseweb="input"] {
  background: var(--zy-surface) !important;
  color: var(--zy-text) !important;
  border-color: var(--zy-border) !important;
}

.stExpander {
  background: var(--zy-bg-elevated) !important;
  border: 1px solid var(--zy-border) !important;
  border-radius: 12px !important;
}

div[data-testid="stAlert"] {
  border-radius: 12px !important;
  border: 1px solid var(--zy-border) !important;
  background: var(--zy-surface) !important;
}

.zy-hero {
  text-align: center;
  margin: 0.2rem 0 1.4rem;
  padding: 1.4rem 1rem 1.2rem;
  border-radius: 20px;
  background:
    radial-gradient(420px 180px at 50% 0%, var(--zy-accent-soft), transparent 70%),
    linear-gradient(180deg, rgba(24, 34, 44, 0.9), rgba(11, 18, 24, 0.2));
  border: 1px solid var(--zy-border);
  animation: zy-fade-in 0.55s ease both;
}

.zy-hero__brand {
  font-family: var(--zy-font-display);
  font-size: clamp(1.8rem, 4vw, 2.35rem);
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--zy-text);
  margin: 0;
}

.zy-hero__accent {
  color: var(--zy-accent);
}

.zy-hero__tagline {
  margin: 0.55rem 0 0;
  color: var(--zy-muted);
  font-size: 0.98rem;
  line-height: 1.55;
}

.zy-section-label {
  font-size: 0.78rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--zy-muted);
  margin: 0.4rem 0 0.8rem;
}

.zy-idle {
  margin-top: 1.2rem;
  padding: 1.1rem 1.2rem;
  border-radius: var(--zy-radius);
  border: 1px dashed rgba(212, 163, 90, 0.28);
  background: rgba(18, 26, 34, 0.75);
  color: var(--zy-muted);
  text-align: center;
  animation: zy-fade-in 0.45s ease both;
}

@keyframes zy-fade-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  *, .zy-hero, .zy-idle, [data-testid="stChatMessage"], .stButton > button {
    animation: none !important;
    transition: none !important;
  }
}
</style>
"""


def apply_theme() -> None:
    """注入暗场主题 CSS。须在 set_page_config 之后调用。"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def render_hero() -> None:
    st.markdown(
        """
<div class="zy-hero">
  <p class="zy-hero__brand">智悦校园 · <span class="zy-hero__accent">小懒</span></p>
  <p class="zy-hero__tagline">校园问答助手。问规章、查资助、解疑惑——像在灯下和同学聊聊。</p>
</div>
""",
        unsafe_allow_html=True,
    )
