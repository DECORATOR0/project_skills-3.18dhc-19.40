from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math


OUT_DIR = Path(
    "/data/xsy/project_skills-3.18dhc-19.40/实验设计与迭代（26.4.8AI尝试整理）/飞书报告维护"
)
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

W = 2200
H = 1320

BG = "#F7F8FC"
TEXT = "#1F2937"
MUTED = "#667085"
DARK = "#0F172A"
BORDER = "#D8E1EE"
BLUE = "#326BFF"
BLUE_LIGHT = "#EAF1FF"
GREEN = "#169B62"
GREEN_LIGHT = "#EAF7F1"
ORANGE = "#F08C2E"
ORANGE_LIGHT = "#FFF2E5"
PURPLE = "#7A5AF8"
PURPLE_LIGHT = "#F2EEFF"
RED = "#D9534F"
RED_LIGHT = "#FDEEEE"
GRAY_LIGHT = "#EEF3FA"
WHITE = "#FFFFFF"


_FONT_CACHE = {}


def get_font(size: int):
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = ImageFont.truetype(FONT_PATH, size)
    return _FONT_CACHE[size]


def text_size(font, text: str):
    return font.getsize(text)


def wrap_text(text: str, font, max_width: int):
    lines = []
    for para in str(text).split("\n"):
        if not para:
            lines.append("")
            continue
        current = ""
        for ch in para:
            trial = current + ch
            if not current or text_size(font, trial)[0] <= max_width:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines


def fit_wrapped_text(text: str, max_width: int, max_height: int, start: int, min_size: int, line_gap: int):
    for size in range(start, min_size - 1, -1):
        font = get_font(size)
        lines = wrap_text(text, font, max_width)
        line_h = text_size(font, "测")[1]
        total_h = len(lines) * line_h + max(0, len(lines) - 1) * line_gap
        if total_h <= max_height:
            return font, lines, line_h
    font = get_font(min_size)
    lines = wrap_text(text, font, max_width)
    line_h = text_size(font, "测")[1]
    return font, lines, line_h


def rect(draw, box, fill, outline=BORDER, width=2):
    draw.rectangle(box, fill=fill, outline=outline, width=width)


def draw_lines(draw, x: int, y: int, lines, font, fill=TEXT, line_gap: int = 6):
    line_h = text_size(font, "测")[1]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h + line_gap
    return y


def centered_text(draw, center, text: str, font, fill=TEXT):
    w, h = text_size(font, text)
    draw.text((center[0] - w / 2, center[1] - h / 2), text, font=font, fill=fill)


def box(draw, box_rect, title: str, body: str, fill=WHITE, outline=BORDER):
    rect(draw, box_rect, fill, outline=outline, width=2)
    x1, y1, x2, y2 = box_rect
    inner_w = x2 - x1 - 36
    inner_h = y2 - y1 - 28

    title_font, title_lines, title_h = fit_wrapped_text(
        title, inner_w, min(96, inner_h // 3), start=30, min_size=22, line_gap=4
    )
    body_font, body_lines, body_h = fit_wrapped_text(
        body, inner_w, inner_h - len(title_lines) * title_h - 30, start=20, min_size=15, line_gap=5
    )

    y = y1 + 16
    y = draw_lines(draw, x1 + 18, y, title_lines, title_font, fill=DARK, line_gap=4)
    y += 8
    draw_lines(draw, x1 + 18, y, body_lines, body_font, fill=MUTED, line_gap=5)


def badge(draw, rect_box, text, fill_color):
    rect(draw, rect_box, fill_color, outline=fill_color, width=1)
    centered_text(draw, ((rect_box[0] + rect_box[2]) // 2, (rect_box[1] + rect_box[3]) // 2), text, get_font(24), WHITE)


def arrow(draw, start, end, color=BLUE, width=5, head=14):
    x1, y1 = start
    x2, y2 = end
    draw.line((x1, y1, x2, y2), fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    left = (
        x2 - head * math.cos(ang) + head * 0.6 * math.sin(ang),
        y2 - head * math.sin(ang) - head * 0.6 * math.cos(ang),
    )
    right = (
        x2 - head * math.cos(ang) - head * 0.6 * math.sin(ang),
        y2 - head * math.sin(ang) + head * 0.6 * math.cos(ang),
    )
    draw.polygon([end, left, right], fill=color)


def poly_arrow(draw, points, color=BLUE, width=5, head=14):
    for i in range(len(points) - 2):
        draw.line((*points[i], *points[i + 1]), fill=color, width=width)
    arrow(draw, points[-2], points[-1], color=color, width=width, head=head)


def title_block(draw, title: str, subtitle: str):
    centered_text(draw, (W // 2, 46), title, get_font(42), DARK)
    subtitle_font = get_font(20)
    subtitle_lines = wrap_text(subtitle, subtitle_font, W - 240)
    draw_lines(draw, 100, 84, subtitle_lines, subtitle_font, fill=MUTED, line_gap=4)


def canvas():
    return Image.new("RGB", (W, H), BG)


def render_01():
    img = canvas()
    d = ImageDraw.Draw(img)
    title_block(
        d,
        "当前 19.40 主线架构",
        "训练侧是“单题单条 skill 的闭环”，评测侧是“skill-guided executor”，并与 Earth-Agent baseline 做同口径对照。",
    )

    badge(d, (90, 140, 720, 190), "训练侧：单题单条 skill 闭环", BLUE)
    badge(d, (790, 140, 1360, 190), "聚合侧：同一份 cluster assignment 产出 flat / tree", GREEN)
    badge(d, (1430, 140, 2110, 190), "评测侧：skill-guided executor 与 Earth-Agent baseline", ORANGE)

    # Training lane
    box(
        d,
        (90, 240, 430, 360),
        "Earth-Bench / formal60 任务",
        "训练、评测和对照都围绕同一批题展开。",
        fill=BLUE_LIGHT,
        outline=BLUE,
    )
    box(
        d,
        (90, 420, 430, 650),
        "单题单条 skill 做题闭环",
        "用当前 skill 做题。\n失败：critic 判别，actor 整改。\n成功：保留该 skill。",
        fill=BLUE_LIGHT,
        outline=BLUE,
    )
    box(
        d,
        (490, 440, 730, 560),
        "经验回放池",
        "同任务跨轮次经验积累。\n供 actor 整改时参考。",
        fill=PURPLE_LIGHT,
        outline=PURPLE,
    )
    box(
        d,
        (170, 760, 500, 860),
        "retained skills",
        "成功轮次保留。\nformal60 merged：47 / 60 retained。",
        fill=GREEN_LIGHT,
        outline=GREEN,
    )

    arrow(d, (260, 360), (260, 430), color=BLUE)
    poly_arrow(d, [(490, 500), (460, 500), (430, 500)], color=PURPLE)
    arrow(d, (260, 650), (260, 760), color=GREEN)

    # Aggregation lane
    box(
        d,
        (840, 330, 1320, 490),
        "自动聚类",
        "对 retained task-local skills 做统一聚类。\n当前固定为 6 个 cluster。",
        fill=GREEN_LIGHT,
        outline=GREEN,
    )
    box(
        d,
        (820, 640, 1060, 790),
        "flat aggregated skills",
        "单层 skill。\n和 tree 共用同一份聚类结果。",
        fill=GREEN_LIGHT,
        outline=GREEN,
    )
    box(
        d,
        (1100, 640, 1340, 790),
        "tree aggregated skills",
        "二层 skill / mode。\n类内还能继续细到某个 mode。",
        fill=GREEN_LIGHT,
        outline=GREEN,
    )

    arrow(d, (500, 810), (840, 410), color=GREEN)
    arrow(d, (1080, 490), (940, 640), color=GREEN)
    arrow(d, (1080, 490), (1220, 640), color=GREEN)

    # Evaluation lane: our method
    box(
        d,
        (1450, 230, 1810, 360),
        "router",
        "按 name + description 路由。\ntree 还会进一步细到某个 mode。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )
    box(
        d,
        (1450, 420, 1810, 600),
        "skill 给 executor 的两类信息",
        "1. 小工具包络\n2. 高层 guidance",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )
    box(
        d,
        (1450, 660, 1810, 900),
        "executor 逐步做题",
        "每步要么选一个工具去用，\n要么停止。\n再根据结果决定是否继续。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )
    box(
        d,
        (1450, 960, 1810, 1090),
        "当前方法的评测优势",
        "先缩到较小工具集，避免超长上下文。\n再用抽象 guidance 帮助选工具和推进。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )

    poly_arrow(d, [(1340, 715), (1400, 715), (1400, 295), (1450, 295)], color=ORANGE)
    arrow(d, (1630, 360), (1630, 420), color=ORANGE)
    arrow(d, (1630, 600), (1630, 660), color=ORANGE)
    arrow(d, (1630, 900), (1630, 960), color=ORANGE)

    # Earth-Agent baseline
    badge(d, (1860, 250, 2090, 292), "Earth-Agent baseline", PURPLE)
    box(
        d,
        (1860, 350, 2090, 500),
        "题目直接进 executor",
        "不先用 skill 缩小工具集。\n也没有前置抽象 guidance。",
        fill=WHITE,
        outline=PURPLE,
    )
    box(
        d,
        (1860, 590, 2090, 840),
        "直接 executor 做题",
        "后半段范式和我们类似。\n但要直接面对更大的工具空间，\n也没有 skill 给出的高层指导。",
        fill=WHITE,
        outline=PURPLE,
    )
    arrow(d, (1975, 500), (1975, 590), color=PURPLE)

    # Bottom notes
    rect(d, (90, 1140, 2110, 1230), GRAY_LIGHT, outline=BORDER, width=2)
    note_boxes = [
        ((140, 1165, 305, 1205), "训练侧", BLUE, "本质是单题单条 skill 的做题-整改闭环"),
        ((620, 1165, 785, 1205), "聚合侧", GREEN, "flat / tree 共用同一份 cluster assignment"),
        ((1100, 1165, 1265, 1205), "评测侧", ORANGE, "先用 skill 缩小工具包络，再给 executor 抽象 guidance"),
        ((1580, 1165, 1745, 1205), "基线", PURPLE, "Earth-Agent 直接让 executor 面对更大的工具空间"),
    ]
    for badge_rect, badge_text, color, note_text in note_boxes:
        badge(d, badge_rect, badge_text, color)
        font, lines, _ = fit_wrapped_text(note_text, 320, 60, start=17, min_size=14, line_gap=3)
        draw_lines(d, badge_rect[2] + 16, 1168, lines, font, fill=TEXT, line_gap=3)

    img.save(OUT_DIR / "01_当前19.40主线架构.png")


def render_02():
    img = canvas()
    d = ImageDraw.Draw(img)
    title_block(
        d,
        "下游 skill 消费范式变化",
        "当前最关键的收口，是把“planner 先定完整工具链”的旧后半段，转成“single direct executor 在 skill guidance 下逐步决策”的范式。",
    )

    rect(d, (90, 160, 940, 1180), RED_LIGHT, outline=RED, width=3)
    rect(d, (1260, 160, 2110, 1180), GREEN_LIGHT, outline=GREEN, width=3)
    badge(d, (140, 190, 520, 240), "旧范式：staged / planner-heavy", RED)
    badge(d, (1270, 190, 1620, 240), "当前范式：single executor", GREEN)

    left_boxes = [
        ((170, 300, 860, 395), "question + 题面信息", "infer_question_profile + shortlist_tools"),
        ((170, 455, 860, 550), "route_skill / family guidance", "先落入旧 family 与 shortlist 约束"),
        ((170, 610, 860, 725), "planner 生成完整 tool_sequence", "在 shortlist 内先给出一整条工具链。"),
        ((170, 785, 860, 900), "parameter worker 逐步填参数", "各阶段 handoff 较多。\n参数阶段容易掉质量。"),
        ((170, 960, 860, 1075), "executor + answer selector", "最后再执行和收答案。\nclosing 依赖后半段链路稳定性。"),
    ]
    for box_rect, title, body in left_boxes:
        box(d, box_rect, title, body, fill=WHITE, outline=RED)
    for a, b in zip(left_boxes, left_boxes[1:]):
        cx = (a[0][0] + a[0][2]) // 2
        arrow(d, (cx, a[0][3]), (cx, b[0][1]), color=RED)

    right_boxes = [
        ((1310, 300, 2000, 395), "question + router", "router 只看 skill name + description"),
        ((1310, 470, 2000, 585), "load SKILL.md + allowed-tools", "skill guidance 和 executor 硬边界同时到位。"),
        ((1310, 660, 2000, 815), "single direct executor 逐步决策", "在同一份上下文里自己选下一步工具并执行。"),
        ((1310, 900, 2000, 1015), "直接收口到最终答案", "减少后半段 handoff。\n训练侧也更容易对齐这种消费方式。"),
    ]
    for box_rect, title, body in right_boxes:
        box(d, box_rect, title, body, fill=WHITE, outline=GREEN)
    for a, b in zip(right_boxes, right_boxes[1:]):
        cx = (a[0][0] + a[0][2]) // 2
        arrow(d, (cx, a[0][3]), (cx, b[0][1]), color=GREEN)

    # Middle compare card
    rect(d, (955, 360, 1245, 880), BLUE_LIGHT, outline=BLUE, width=3)
    centered_text(d, (1100, 390), "关键对比", get_font(28), DARK)
    centered_text(d, (1100, 425), "旧范式 -> 当前范式", get_font(18), MUTED)
    metrics = [
        ("ACC", "0.3508 -> 0.4476"),
        ("tool_any_order", "0.5927 -> 0.7145"),
        ("tool_in_order", "0.4748 -> 0.6555"),
        ("tool_exact_match", "0.4357 -> 0.4125"),
    ]
    y = 465
    for name, val in metrics:
        rect(d, (990, y, 1210, y + 88), WHITE, outline=BORDER, width=2)
        centered_text(d, (1100, y + 24), name, get_font(18), BLUE)
        centered_text(d, (1100, y + 58), val, get_font(16), TEXT)
        y += 108
    box(
        d,
        (970, 780, 1230, 860),
        "当前结论",
        "默认收口到\nsingle direct executor。",
        fill=WHITE,
        outline=BLUE,
    )

    # Bottom callouts
    box(
        d,
        (150, 1105, 860, 1240),
        "旧范式主要问题",
        "planner 直接先把工具链定死，这对小模型更难。\n从 ACC、tool_any_order、tool_in_order 看，整体执行质量都更弱。",
        fill=WHITE,
        outline=RED,
    )
    box(
        d,
        (1310, 1105, 2020, 1240),
        "当前范式主要收益",
        "benchmark 实分更高，参数质量更好。\n后续 task-local 单条 skill 训练也更容易对齐这种消费形态。",
        fill=WHITE,
        outline=GREEN,
    )

    img.save(OUT_DIR / "02_下游skill消费范式变化.png")


def render_03():
    img = canvas()
    d = ImageDraw.Draw(img)
    title_block(
        d,
        "实验主线与关键结论",
        "这几轮实验的价值在于：每一段都回答了一个更具体的问题，因此最终结论可以被拆解、复核和继承。",
    )

    stage_boxes = [
        ((90, 190, 520, 620), PURPLE, PURPLE_LIGHT, "阶段一：6 ~ 7.7 历史主线", "关键结果\n- direct-executor 44.76\n- 拆分式后半段 35.08\n\n主要发现\n- 先确认单 executor 下游更稳\n- 旧线里的流程图、错误分型和对照结论今天仍然有效"),
        ((620, 190, 1050, 620), BLUE, BLUE_LIGHT, "阶段二：v8 formal60 首轮完整实跑", "关键结果\n- retained 47 / 60\n- flat 15 / 60\n- tree 18 / 60\n\n主要发现\n- 当前主线闭环已经跑通\n- 问题转向聚合后执行质量"),
        ((1150, 190, 1580, 620), GREEN, GREEN_LIGHT, "阶段三：token-budget executor 重跑", "关键结果\n- 40960 total：flat 25 / 60，tree 25 / 60\n- 32768 total：flat 24 / 60，tree 25 / 60\n\n主要发现\n- memory / budget 是关键工程变量\n- flat 的能力在新口径下被释放出来了不少"),
        ((1680, 190, 2110, 620), ORANGE, ORANGE_LIGHT, "阶段四：Earth-Agent / no-skill 对照", "关键结果\n- Earth-Agent origin 12~13 / 60\n- no-skill / all-tools 14 / 60\n\n主要发现\n- skill library 仍然有价值\n- 开放工具空间不会自然替代 skill prior"),
    ]
    for box_rect, color, fill, title, body in stage_boxes:
        box(d, box_rect, title, body, fill=fill, outline=color)
    for a, b in zip(stage_boxes, stage_boxes[1:]):
        arrow(d, (a[0][2], 405), (b[0][0], 405), color=BLUE)

    rect(d, (90, 720, 2110, 1220), WHITE, outline=BORDER, width=3)
    centered_text(d, (1100, 760), "几个专题观察怎样反过来支持当前判断", get_font(34), DARK)

    obs_boxes = [
        ((150, 830, 1010, 980), BLUE, BLUE_LIGHT, "专题 1：contract 收口", "shortlist 提前指定、family 提前分桶会让 skill 作用和系统先验混在一起。\nv8 的核心价值之一，就是先把这个问题拆干净。"),
        ((1190, 830, 2050, 980), PURPLE, PURPLE_LIGHT, "专题 2：actor / critic 控制流", "critic 每轮先跑，actor 只在失败轮次介入；成功轮次直接短路。\n因此后续调优重点应放在失败后的 skill 修正质量与 retained 逻辑。"),
        ((150, 1030, 1010, 1180), GREEN, GREEN_LIGHT, "专题 3：executor token / memory", "system + user 永远优先保留，skill 一旦过肥就会挤压历史。\n扩窗有帮助，但不会自然消灭 prompt inflation。"),
        ((1190, 1030, 2050, 1180), ORANGE, ORANGE_LIGHT, "专题 4：origin 对照给出的提醒", "原生单 agent 可以把大部分题跑起来，但 final answer closing 很弱。\n成功跑完、参数 grounded、最终答对必须分开看。"),
    ]
    for box_rect, color, fill, title, body in obs_boxes:
        box(d, box_rect, title, body, fill=fill, outline=color)

    img.save(OUT_DIR / "03_实验主线与关键结论.png")


def render_04():
    img = canvas()
    d = ImageDraw.Draw(img)
    title_block(
        d,
        "flat skill 与 tree skill 结构对比",
        "当前保留 tree skill 的核心原因，不是单纯增加层数，而是在较低路由成本下，争取给出更细的工具侧重点和更有针对性的高层 guidance。",
    )

    rect(d, (90, 170, 920, 1180), BLUE_LIGHT, outline=BLUE, width=3)
    rect(d, (1280, 170, 2110, 1180), GREEN_LIGHT, outline=GREEN, width=3)
    badge(d, (140, 200, 330, 250), "flat skill", BLUE)
    badge(d, (1270, 200, 1460, 250), "tree skill", GREEN)

    # flat side
    box(d, (170, 320, 840, 430), "一次路由到某条 skill", "router 直接从候选 skill 中选中一条。", fill=WHITE, outline=BLUE)
    box(d, (170, 500, 840, 690), "单层 guidance 一次性给全", "工具侧重点、步骤偏好、停止条件都写在同一条 skill 里。\n优点是结构简单，消费链路短。", fill=WHITE, outline=BLUE)
    box(d, (170, 760, 840, 920), "潜在问题", "如果后续 skill 数量开得很多，单次全局路由的准确性会成为问题。\n不同题型的 guidance 也更容易混在同一条 skill 里。", fill=WHITE, outline=BLUE)
    box(d, (170, 990, 840, 1130), "当前对 flat 的理解", "1. memory 修复后已经有明显竞争力。\n2. 适合做统一 guidance。\n3. 后续重点在 skill 注入体积和 guidance 质量。", fill=WHITE, outline=BLUE)
    arrow(d, (505, 430), (505, 500), color=BLUE)
    arrow(d, (505, 690), (505, 760), color=BLUE)
    arrow(d, (505, 920), (505, 990), color=BLUE)

    # center route schematic
    rect(d, (930, 320, 1260, 980), WHITE, outline=PURPLE, width=3)
    centered_text(d, (1095, 355), "路由示意", get_font(28), DARK)
    rect(d, (1000, 410, 1190, 470), BLUE_LIGHT, outline=BLUE, width=2)
    centered_text(d, (1095, 440), "题目", get_font(22), DARK)
    arrow(d, (1095, 470), (1095, 540), color=BLUE)
    rect(d, (1000, 540, 1190, 610), BLUE_LIGHT, outline=BLUE, width=2)
    centered_text(d, (1095, 575), "flat skill", get_font(22), DARK)
    arrow(d, (1095, 610), (1095, 680), color=BLUE)
    rect(d, (980, 680, 1210, 750), BLUE_LIGHT, outline=BLUE, width=2)
    centered_text(d, (1095, 715), "统一 guidance", get_font(20), DARK)
    centered_text(d, (1095, 805), "vs", get_font(30), PURPLE)
    font, lines, _ = fit_wrapped_text("tree = 题目 -> 大类 -> mode -> 更细 guidance", 250, 120, start=18, min_size=15, line_gap=4)
    draw_lines(d, 970, 855, lines, font, fill=TEXT, line_gap=4)

    # tree side
    box(d, (1350, 300, 2020, 410), "第一次粗路由：先落到一个大类", "例如先在约 6 个较粗的大类里判断题目属于哪种模态 / 主任务。", fill=WHITE, outline=GREEN)
    box(d, (1350, 470, 2020, 600), "第二次细匹配：在类内选 mode", "再在该类内部按题目侧重点匹配子类型。\n相当于再做一次更细的低成本路由。", fill=WHITE, outline=GREEN)
    box(d, (1350, 660, 2020, 840), "mode 级 guidance 更细", "可以更有针对性地强调工具侧重点、高层执行范式、关键判断点。\n把不同子任务的差异写进 skill 内部结构。", fill=WHITE, outline=GREEN)
    box(d, (1350, 900, 2020, 1090), "tree 的实际意义", "它本质上也在缓解“多 skill 路由会越来越难”这个问题。\n先粗后细，总体开销较小，但理论上能得到更细 guidance。", fill=WHITE, outline=GREEN)
    arrow(d, (1665, 410), (1665, 470), color=GREEN)
    arrow(d, (1665, 600), (1665, 660), color=GREEN)
    arrow(d, (1665, 840), (1665, 900), color=GREEN)

    rect(d, (90, 1210, 2110, 1270), GRAY_LIGHT, outline=BORDER, width=2)
    font, lines, _ = fit_wrapped_text(
        "当前判断：flat 更简单直接；tree 更适合在模态清晰的任务里做“先粗后细”的 guidance 组织。现在 tree 效果还不显著，但值得继续调 mode 设计、类内匹配和 guidance 质量。",
        1940,
        40,
        start=18,
        min_size=16,
        line_gap=4,
    )
    draw_lines(d, 120, 1225, lines, font, fill=TEXT, line_gap=4)

    img.save(OUT_DIR / "04_flat_tree_skill结构对比.png")


def render_05():
    img = canvas()
    d = ImageDraw.Draw(img)
    title_block(
        d,
        "aggregated skill 字段约束与下游消费",
        "这张图只保留当前 aggregated skill 自身的字段约束，以及 router、executor、trace 分别消费哪些部分。",
    )

    badge(d, (90, 145, 650, 195), "当前字段约束", BLUE)
    badge(d, (760, 145, 1450, 195), "aggregated skill bundle", GREEN)
    badge(d, (1580, 145, 2110, 195), "下游消费", ORANGE)

    # Left: constraints only
    box(
        d,
        (100, 250, 620, 405),
        "共同 frontmatter",
        "固定包含\nname / description / allowed-tools /\ncompatibility / metadata。",
        fill=BLUE_LIGHT,
        outline=BLUE,
    )
    box(
        d,
        (100, 465, 620, 660),
        "body 结构约束",
        "flat：High-Level Execution Guidance /\nCommon Defaults / Global Guardrails。\n\ntree：Execution Profile Index /\nGlobal Execution Rules / Global Guardrails /\nReference Usage。",
        fill=BLUE_LIGHT,
        outline=BLUE,
    )
    box(
        d,
        (100, 720, 620, 860),
        "tree 额外资源",
        "tree 允许补一个\nreferences/EXECUTION_GUIDANCE.md。\n供 mode 选定后继续细化 guidance。",
        fill=BLUE_LIGHT,
        outline=BLUE,
    )
    box(
        d,
        (100, 920, 620, 1090),
        "当前收口原则",
        "metadata 先只做溯源。\n旧 family / domain / route_signals /\nquestion-range 这类强先验不再放回聚合产物。",
        fill=WHITE,
        outline=BLUE,
    )

    # Middle: bundle view
    rect(d, (720, 235, 1480, 1120), WHITE, outline=GREEN, width=3)
    centered_text(d, (1100, 275), "当前下游真正接触到的 skill", get_font(30), DARK)

    field_boxes = [
        ((780, 340, 1115, 450), "name", "公开 skill 名。\nrouter 的候选标签。"),
        ((1130, 340, 1420, 450), "description", "路由描述。\n写清任务锚点和题型形状。"),
        ((780, 495, 1115, 610), "allowed-tools", "工具包络。\nexecutor 的硬工具边界。"),
        ((1130, 495, 1420, 610), "compatibility", "flat / tree 版本标记。\n当前主要做口径管理。"),
        ((780, 655, 1420, 815), "SKILL.md 主体", "主要高层 guidance 都在这里。\n包括执行骨架、步骤顺序、guardrails、helper contract。"),
        ((780, 860, 1115, 990), "references / scripts", "共享 helper / 参考说明。\n正文明确引用后再调用。"),
        ((1130, 860, 1420, 990), "metadata", "schema_version、skill_shape、source_*、generated_at。\n当前主要服务溯源和统计。"),
    ]
    for box_rect, title, body in field_boxes:
        box(d, box_rect, title, body, fill=GREEN_LIGHT, outline=GREEN)

    box(
        d,
        (780, 1030, 1420, 1090),
        "一句话理解",
        "router 主要吃 routing surface，executor 主要吃工具包络和 guidance。",
        fill=WHITE,
        outline=GREEN,
    )

    # Right: consumers
    box(
        d,
        (1600, 290, 2080, 425),
        "router 消费",
        "只看 name + description。\n不需要读完整 SKILL.md。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )
    box(
        d,
        (1600, 500, 2080, 760),
        "executor 消费",
        "allowed-tools = 硬边界。\n完整 SKILL.md = 高层 guidance。\n\ntree 选完 mode 后，还会继续读\nreferences/EXECUTION_GUIDANCE.md。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )
    box(
        d,
        (1600, 840, 2080, 980),
        "trace / 统计 / 复盘",
        "主要看 metadata + compatibility。\n用于实验记录、版本追踪和横向比较。",
        fill=ORANGE_LIGHT,
        outline=ORANGE,
    )

    poly_arrow(d, [(1420, 395), (1510, 395), (1510, 360), (1600, 360)], color=ORANGE)
    poly_arrow(d, [(1115, 552), (1510, 552), (1510, 630), (1600, 630)], color=ORANGE)
    poly_arrow(d, [(1420, 735), (1510, 735), (1510, 630), (1600, 630)], color=ORANGE)
    poly_arrow(d, [(1115, 925), (1510, 925), (1510, 630), (1600, 630)], color=ORANGE)
    poly_arrow(d, [(1420, 925), (1510, 925), (1510, 910), (1600, 910)], color=ORANGE)

    rect(d, (90, 1170, 2110, 1250), GRAY_LIGHT, outline=BORDER, width=2)
    font, lines, _ = fit_wrapped_text(
        "后续可尝试的优化点：字段集合不必长期完全人工固定。可以让强模型先分析目标下游最需要什么，再为不同 consumer 生成更友好的字段组织；当前更稳妥的前提，是先保住 name + description + allowed-tools + executable guidance 这组最小稳定核心。",
        1940,
        56,
        start=18,
        min_size=15,
        line_gap=4,
    )
    draw_lines(d, 120, 1188, lines, font, fill=TEXT, line_gap=4)

    img.save(OUT_DIR / "05_聚合skill字段与消费关系.png")


def main():
    render_01()
    render_02()
    render_03()
    render_04()
    render_05()


if __name__ == "__main__":
    main()
