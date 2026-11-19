# -*- coding: utf-8 -*-
"""
report.py —— 三段式复盘报告生成
=================================
  session=premarket  盘前操作预期 (09:00)
  session=postmarket 盘后复盘 (15:30)
  session=evening    晚间美股+消息汇总 (22:45)

方法论: 威科夫量价 (阶段/供需/努力结果) + 缠论 (分型/笔/中枢/背驰/买卖点)
板块: 按当日主力净流入动态轮换, 不固定标的
"""
from __future__ import annotations

from datetime import datetime

import core

DISCLAIMER = (
    "> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。"
    "市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，"
    "必要时咨询持牌专业机构。过往表现不预示未来收益。"
)


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else ''}{x}%"


def _color_pct(x: float) -> str:
    """markdown 里用箭头标注涨跌方向 (推送端可再上色)。"""
    arrow = "▲" if x >= 0 else "▼"
    return f"{arrow} {abs(x):.2f}%"


def _index_table(bundle: dict) -> str:
    lines = ["| 指数 | 收盘 | 涨跌 | 威科夫阶段 | 缠论信号 | 支撑 | 压力 |",
             "|---|---|---|---|---|---|---|"]
    for name, a in bundle["indices"].items():
        if "error" in a:
            lines.append(f"| {name} | 数据缺失 | - | - | - | - | - |")
            continue
        lv = a["levels"]
        sup = "/".join(str(s) for s in lv["supports"][:2]) or "-"
        res = "/".join(str(r) for r in lv["resistances"][:2]) or "-"
        lines.append(
            f"| {name} | {a['close']} | {_color_pct(a['change_pct'])} | "
            f"{a['wyckoff']['phase'].split('(')[0]} | {a['chan']['latest_signal'][:14]}… | "
            f"{sup} | {res} |"
        )
    return "\n".join(lines)


def _sector_block(sectors: list[dict]) -> str:
    lines = ["| 板块 | 涨跌幅 | 主力净流入(亿) | 净占比 |",
             "|---|---|---|---|"]
    for s in sectors[:6]:
        if "error" in s:
            return "  - 板块资金数据暂不可用"
        net = s["main_net_in"] / 1e8 if isinstance(s["main_net_in"], (int, float)) else 0
        pct = s["main_net_pct"]
        pct_s = f"{pct}%" if isinstance(pct, (int, float)) else "-"
        lines.append(f"| {s['name']} | {_color_pct(s['change_pct'] or 0)} | {net:.1f} | {pct_s} |")
    return "\n".join(lines)


def _cross_index_view(bundle: dict) -> str:
    """综合四大指数给出一句话市场判断 + 分歧点。"""
    valid = [a for a in bundle["indices"].values() if "error" not in a]
    if not valid:
        return "四大指数数据均不可用,暂无法给出综合判断。"
    chgs = [a["change_pct"] for a in valid]
    up = sum(1 for c in chgs if c > 0)
    avg = sum(chgs) / len(chgs)
    strongest = max(valid, key=lambda a: a["change_pct"])
    weakest = min(valid, key=lambda a: a["change_pct"])
    tone = "普涨" if up == len(valid) else ("普跌" if up == 0 else "分化")
    return (
        f"四大指数 **{tone}**(均涨跌 {avg:+.2f}%,{up}/{len(valid)} 收红)。"
        f"领涨:{strongest['name']} {_signed(strongest['change_pct'])};"
        f"最弱:{weakest['name']} {_signed(weakest['change_pct'])}。"
    )


def _fmt_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def build_premarket(bundle: dict, sectors: list[dict]) -> str:
    """盘前操作预期。"""
    sec = _cross_index_view(bundle)
    # 上一交易日强弱板块
    top = [s["name"] for s in sectors[:3] if "error" not in s]
    hot = "、".join(top) if top else "暂无明确主线"
    parts = [
        f"# 盘前操作预期 · {_fmt_time()}",
        "",
        "## 一、隔夜与昨日回顾",
        sec,
        f"近期资金主线集中在 **{hot}**,盘前留意其能否延续(资金是否隔夜留存)。",
        "",
        "## 二、四大指数技术面 (基于最近交易日收盘)",
        _index_table(bundle),
        "",
        "## 三、威科夫 & 缠论视角",
    ]
    for name, a in bundle["indices"].items():
        if "error" in a:
            continue
        wy, ch = a["wyckoff"], a["chan"]
        parts.append(
            f"- **{name}**: 威科夫[{wy['phase']}]——{wy['effort_result']}。"
            f"缠论:{ch['latest_signal']};{ch['divergence']}。"
        )
    parts += [
        "",
        "## 四、今日操作预期 (条件→应对)",
        "- 若指数高开且守住前压力位转支撑 → 持股/顺势,关注放量确认;",
        "- 若低开跌破近端支撑且反抽不过 → 减仓观望,不抢反弹;",
        "- 若平开震荡于中枢内部 → 轻仓参与主线板块轮动,快进快出。",
        "- 资金主线盯 **主力净流入居前板块**,盘中确认量能再跟随。",
        "",
        "## 五、关键点位速览",
    ]
    for name, a in bundle["indices"].items():
        if "error" in a:
            continue
        lv = a["levels"]
        parts.append(
            f"- {name}: 支撑 {'/'.join(map(str, lv['supports'][:2]))} | "
            f"压力 {'/'.join(map(str, lv['resistances'][:2]))} | "
            f"MA20 {lv['ma20']} / MA60 {lv['ma60']}"
        )
    parts += ["", "---", DISCLAIMER]
    return "\n".join(parts)


def build_postmarket(bundle: dict, sectors: list[dict]) -> str:
    """盘后复盘。"""
    sec = _cross_index_view(bundle)
    parts = [
        f"# 盘后复盘 · {_fmt_time()}",
        "",
        "## 一、今日市场概览",
        sec,
        "",
        "## 二、四大指数收盘技术面",
        _index_table(bundle),
        "",
        "## 三、威科夫复盘 (量价与主力行为)",
    ]
    for name, a in bundle["indices"].items():
        if "error" in a:
            continue
        wy = a["wyckoff"]
        ev = "、".join(e["type"] for e in wy.get("events", [])[:2]) or "无显著量价事件"
        parts.append(
            f"- **{name}**: 阶段[{wy['phase']}],{wy['note']} "
            f"近端事件:{ev};{wy['effort_result']};{wy['volume_trend']}。"
        )
    parts += [
        "",
        "## 四、缠论结构复盘",
    ]
    for name, a in bundle["indices"].items():
        if "error" in a:
            continue
        ch = a["chan"]
        parts.append(f"- **{name}**: {ch['note']} {ch['latest_signal']}。背驰:{ch['divergence']}。")
    parts += [
        "",
        "## 五、资金流向 (主力净流入居前板块)",
        _sector_block(sectors),
        "",
        "- 资金连续流入的板块视为当前主线,关注其持续性;单日脉冲需防高位派发。",
        "",
        "## 六、明日展望",
        "- 站上压力且放量 → 趋势延续;跌破支撑 → 转弱,控制仓位;",
        "- 中枢内部震荡 → 继续板块轮动思路。",
        "",
        "---", DISCLAIMER,
    ]
    return "\n".join(parts)


def build_evening(bundle: dict, sectors: list[dict], us: dict) -> str:
    """晚间美股 + 消息汇总。"""
    sec = _cross_index_view(bundle)
    # 美股
    us_lines = ["| 指数 | 点位 | 涨跌 |", "|---|---|---|"]
    if "error" in us or not us:
        us_lines.append("| 美股数据 | 暂不可用 | - |")
    else:
        for k, v in us.items():
            if k == "error":
                continue
            us_lines.append(f"| {k} | {v['price']} | {_color_pct(v['change_pct'])} |")
    parts = [
        f"# 晚间复盘 · 美股与消息汇总 · {_fmt_time()}",
        "",
        "## 一、美股盘面 (隔夜/盘中)",
        "\n".join(us_lines),
        "",
        "> 美股方向与科技股情绪会传导至次日 A 股开盘,尤其是半导体/AI 相关板块。",
        "",
        "## 二、A 股今日收盘回顾",
        sec,
        _index_table(bundle),
        "",
        "## 三、资金主线 (今日主力净流入居前板块)",
        _sector_block(sectors),
        "(板块按资金流入动态轮换,以上为当日实时排名,非固定标的)",
        "",
        "## 四、消息面提示",
        "- 本报告为量化数据驱动,**消息/舆情需人工复核**:盘后请关注证监会/交易所公告、"
        "央行与宏观数据、行业政策、龙头公司公告及夜间外盘突发事件;",
        "- 若美股科技股大跌或出现重大利空,次日 A 股相关板块可能低开,盘前预期需相应下修。",
        "",
        "## 五、联动研判 (条件化)",
        "- 美股上涨 + A股资金净流入延续 → 次日偏向高开高走,关注主线;",
        "- 美股下跌但 A股资金逆势流入 → 关注低开后的资金承接力度;",
        "- 美股大跌 + A股资金流出 → 防守为主,降低仓位。",
        "",
        "---", DISCLAIMER,
    ]
    return "\n".join(parts)


def build(session: str, bundle: dict, sectors: list[dict] | None = None, us: dict | None = None) -> str:
    sectors = sectors if sectors is not None else core.fetch_sector_flow()
    if session == "premarket":
        return build_premarket(bundle, sectors)
    if session == "postmarket":
        return build_postmarket(bundle, sectors)
    if session == "evening":
        us = us if us is not None else core.fetch_us_markets()
        return build_evening(bundle, sectors, us)
    raise ValueError(f"unknown session: {session}")


if __name__ == "__main__":
    b = core.analyze_all(limit=300)
    s = core.fetch_sector_flow()
    for sess in ("premarket", "postmarket", "evening"):
        print("\n\n==========", sess, "==========\n")
        print(build(sess, b, s, core.fetch_us_markets()))
