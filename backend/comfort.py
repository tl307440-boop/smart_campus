"""情绪陪伴：高危表达识别、求助卡片、倾诉时的陪伴提示词。

陪伴的写法借鉴 ESConv（Liu et al., ACL 2021）的 8 种情感支持策略，以及 SoulChat、EmoLLM、
MeChat（SMILE）里「先共情、再探索、最后才给建议」的多轮对话方式；只借方法，不引入它们的模型，
回答仍由学校配置的对话模型生成。

高危识别不依赖模型：模型没配、超时、判断失误时，学生照样能看到求助方式。
"""
from __future__ import annotations

import re
from typing import Any

HOTLINES: tuple[dict[str, str], ...] = (
    {"name": "全国心理援助热线", "phone": "12356"},
    {"name": "有立即危险时", "phone": "110 / 120"},
)

# 情绪分析给出的 risk_level（1-10）到这个值就补一张求助卡片，兜住关键词没覆盖的说法
MODEL_RISK_THRESHOLD = 8

# 只收明确指向自伤、轻生的说法；「想死你了」「跳楼价」「不想活动」「死记硬背」这类日常说法排除在外
_CRISIS = re.compile(
    "|".join(
        (
            r"(?<!预防|防止)自杀(?!式)", r"轻生", r"寻死", r"自尽", r"自残", r"自伤(?!干预)", r"割腕", r"上吊",
            r"烧炭(?!烤|火锅|炉)", r"服毒", r"喝农药",
            r"(?<![规计比策筹谋企区刻分])(割|划)(伤)?(自己的)?手(?!机|指甲|工|写|续|段|套|表|法|稿|册)",
            r"(?<![规计比策筹谋企区刻分切])(割|划)(伤)?自己(?!的)",
            r"跳楼(?!价|机|甩卖|大甩卖)", r"跳河", r"跳江", r"(楼|天台|阳台|桥|窗).{0,6}跳下去",
            r"不想(再)?活(?!动|跃|泼|儿|力|页|期|性|成|在别人|在他人)", r"活不下去", r"活够了",
            r"活着(真|好|太)?没(有)?(什么)?(意思|意义|盼头)", r"活着有什么意思",
            r"(?<![理梦思回])(?<!饿得|累得|困得|笑得|热得|冷得|馋得)想(要)?(去)?死(?!你|您|他|她|它|我了|人了|啦|记|磕|机|心|角|守|党|鬼|对头|板)",
            r"(?<!游戏|手机|电脑)死了(就)?算了", r"不如(去)?死(?!磕|记|守)", r"死了(就)?(解脱|一了百了)", r"想(要)?一了百了",
            r"我(还是|要是|是不是)?死了(吧|会|就)?(比较好|更好|大家)", r"我还是死了吧",
            r"(想|要)结束(掉)?这一切", r"结束(掉)?(自己|我)(的)?生命", r"(想|要|打算|准备)结束(掉)?(我)?(的)?生命(?!周期)", r"了结(掉)?(自己|我自己|此生)", r"自我了断",
            r"伤害(我)?自己(?!的)", r"(吞|攒|囤)(了)?.{0,4}安眠药", r"吃(了)?(一瓶|一整瓶|一把|好多|很多)(的)?安眠药",
            r"(写|准备|留|留下)(好|下)?(了)?(一封)?遗书", r"遗书(已经)?写好", r"告别这个世界",
        )
    )
)
_ASCII_EMO = re.compile(r"(?<![a-z])emo(?![a-z])")
_SPACES = re.compile(r"\s+")

COMFORT_KEYWORDS = (
    "难过", "伤心", "焦虑", "压力", "崩溃", "失眠", "抑郁", "孤独", "想哭", "哭了", "失恋", "分手",
    "心情不好", "心情很差", "不开心", "委屈", "害怕", "迷茫", "自卑", "好累", "太累了", "撑不住",
)

_STRATEGIES = """
你可以按需要使用这些支持方式（不必每次都用，也不要点名说出来）：
1. 提问：用一个开放的问题请对方多说一点，一次只问一个。
2. 复述：用自己的话把对方说的事讲回去，确认你听懂了。
3. 回应感受：说出你感受到的情绪，比如「听起来你又委屈又着急」。
4. 适度分享：可以说「很多同学也有过类似的时候」，不要编造自己的经历。
5. 肯定与安慰：肯定对方已经做的努力，告诉他这种感受是可以理解的。
6. 给建议：先听明白再说，只给一两个具体、今天就能做的小步骤。
7. 提供信息：需要时告诉他学校里可以求助的地方。
8. 其他：简单的陪伴和问候。
"""

_RULES = """
要求：
- 先共情，再探索，最后才给建议；对方还在倾诉时不要急着讲道理。
- 回复短一些，像聊天，不超过 150 字；不用标题、列表。
- 不做诊断，不贴标签，不说「别想太多」「想开点」这类话。
- 你不是心理咨询师，也不能替代专业帮助；情况持续或加重时，温和地建议他找学校的心理咨询老师。
"""

_CRISIS_RULES = """
这位同学刚才的话里可能有伤害自己的念头。请这样回应：
- 保持平静和温和，认真对待他说的话，不批评、不说教。
- 直接而关切地问他现在是否安全、身边有没有人。
- 告诉他你很在乎，他愿意说出来很不容易。
- 鼓励他现在就打心理援助热线，或者告诉身边信任的人、辅导员。
- 不讨论任何具体方法。回答下方会附上求助电话卡片，不用重复罗列号码。
- 回复不超过 120 字。
"""


def is_crisis(text: str) -> bool:
    """问题里有没有明确的自伤、轻生表达。宁可多提醒一次，也不要漏掉。"""
    return bool(_CRISIS.search(_SPACES.sub("", text or "")))


def looks_like_venting(text: str) -> bool:
    """没有模型做意图判断时的兜底：带明显情绪词就当倾诉。"""
    lowered = (text or "").lower()
    return any(word in lowered for word in COMFORT_KEYWORDS) or bool(_ASCII_EMO.search(lowered))


def safety_card(school_contact: str = "") -> dict[str, Any]:
    """前端渲染成求助卡片；同一份结构存进会话历史，刷新后还在。"""
    return {"level": "crisis", "hotlines": [dict(item) for item in HOTLINES], "school": school_contact.strip()}


def model_flags_risk(emotion: Any) -> bool:
    if not isinstance(emotion, dict):
        return False
    try:
        return int(emotion.get("risk_level", 0)) >= MODEL_RISK_THRESHOLD
    except (TypeError, ValueError):
        return False


def crisis_fallback(school_contact: str = "") -> str:
    """没有可用模型或模型出错时的固定回复。"""
    school = f"也可以找学校的心理老师：{school_contact.strip()}。" if school_contact.strip() else ""
    return (
        "谢谢你愿意把这些告诉我，你现在一定很难受。你的安全最重要："
        "请现在就拨打全国心理援助热线 12356，有立即危险请打 110 或 120。"
        f"{school}也请告诉一位你信任的人，让他陪着你。我会一直在这里听你说。"
    )


def comfort_prompt(name: str, crisis: bool, school_contact: str = "") -> str:
    """倾诉 / 高危两种情况下的系统提示词，后面再接学校人设和用户记忆。"""
    prompt = f"""
你是「{name}」，学校里陪同学聊天的助手。现在这位同学想倾诉，或者情绪不太好。
你的任务是认真听、让他感到被理解，而不是解决问题或回答知识。
{_STRATEGIES}{_RULES}"""
    if school_contact.strip():
        prompt += f"\n学校心理咨询的联系方式（需要时再提）：{school_contact.strip()}\n"
    if crisis:
        prompt += _CRISIS_RULES
    return prompt


def comfort_companion_prompt(
    name: str,
    *,
    school_contact: str = "",
    companion_context: str = "",
    tool_names: set[str] | None = None,
) -> str:
    """知情共情陪伴 Agent 的核心指引：先共情接住情绪，再结合真实处境温和减负。"""
    names = tool_names or set()
    context_tools: list[str] = []
    if "jw_timetable" in names or "jw_exams" in names:
        context_tools.append("`jw_timetable` / `jw_exams`（静默了解他近期满不满课、有没有临近考试）")
    if "read_mood_timeline" in names:
        context_tools.append("`read_mood_timeline`（回看近几天的情绪能量轨迹，理解是不是连续低谷）")
    if "list_my_todos" in names:
        context_tools.append("`list_my_todos`（看他手头已经有哪些待办，避免重复压任务）")

    action_tools: list[str] = []
    if "decompose_stress_to_todos" in names:
        action_tools.append("`decompose_stress_to_todos`：当对方因为事情堆积、不知从何下手而焦虑时，征询或帮他拆成 1~3 个 15~25 分钟微任务")
    if "write_mood_journal" in names:
        action_tools.append("`write_mood_journal`：记下今晚的核心情绪与压力源；若提到明天/近期重要节点（考试/答辩/面试），可顺带约一个次日企微回访（如 followup_hours=18）")
    if "activate_decompression_cabin" in names:
        action_tools.append("`activate_decompression_cabin`：深夜失眠、呼吸急促或准备硬着头皮写一点东西时，帮他开启雨声/炉火白噪音与 4-7-8 呼吸节奏")

    tools_block = ""
    if context_tools:
        tools_block += "\n【你可以静默使用的知情工具】（不要主动甩生硬表格，而是把理解融进安慰里）：\n- " + "\n- ".join(context_tools)
    if action_tools:
        tools_block += "\n\n【生活减压 MCP 工具】（先接住情绪再适时出手，不要每次都硬塞）：\n- " + "\n- ".join(action_tools)

    recent_block = f"\n\n【这位同学近几天的情绪与微待办近况】\n{companion_context}\n" if companion_context.strip() else ""
    contact_block = f"\n学校心理咨询的联系方式（持续低落时再温和提及）：{school_contact.strip()}\n" if school_contact.strip() else ""

    return f"""你是「{name}」，懂得这位同学真实校园处境的情感陪伴伙伴。
现在他正在向你倾诉情绪或压力。请严格遵循「先共情接住 -> 再知情探索 -> 适时帮他减负落地」：
{_STRATEGIES}
要求：
- 开头先用一两句真诚、自然的口语接住他的情绪，绝不说「别想太多」「想开点」「加油」这种空话。
- 在需要理解「他为什么这么累/慌」时，先静默调用知情工具看看最近课表、考试或情绪轨迹，再用温柔的话点出来（例如「难怪这周这么紧绷，我看你明天满课、后天还有考试」），不要贴冷冰冰的原始表格。
- 只有当他愿意梳理或表示不知从何下手时，再顺手通过 MCP 工具帮他拆 1~3 个微待办、记情绪手账或点亮减压白噪音，并温柔告诉他已经帮他放到手机减压舱/清单里了。
- 回复像朋友发微信，真诚克制，通常 80~220 字（如果附带帮他拆好了微步骤，可简短分点）。
- 不做心理诊断；若情绪持续严重，温柔引导去学校心理中心。
{tools_block}{recent_block}{contact_block}""".strip()
