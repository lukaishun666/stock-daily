# -*- coding: utf-8 -*-
"""
render_charts.py —— 四大指数技术分析图渲染
=============================================
每个指数生成一张图,包含:
  - K线 (蜡烛图) + 成交量副图
  - MA5/MA20/MA60 均线
  - 压力位(红虚线)/支撑位(绿虚线)标注 + 价格标签
  - 缠论近端中枢 (半透明色块)
  - 威科夫阶段 / 缠论信号 / 背驰 文字框
  - 中文字体自适应 (本机 YaHei / 服务器 Noto CJK)
"""
from __future__ import annotations

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
import matplotlib.font_manager as fm

import core

# -------- 中文字体自适应 --------
def _setup_font():
    candidates = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Noto Sans CJK JP",
        "WenQuanYi Zen Hei", "PingFang SC", "Arial Unicode MS",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for c in candidates:
        if c in available:
            plt.rcParams["font.sans-serif"] = [c, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return c
    # 服务器未装字体时尝试直接加载常见路径
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]:
        if os.path.exists(path):
            fm.fontManager.addfont(path)
            name = fm.FontProperties(fname=path).get_name()
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return "DejaVu Sans (中文字体缺失)"

FONT = _setup_font()

RED, GREEN = "#e64545", "#2eb35a"      # A股习惯: 涨红跌绿
UP_FILL, DOWN_FILL = RED, GREEN
RES_COLOR, SUP_COLOR = "#d62728", "#1a9850"
ZONE_COLOR = "#7b8cde"


def _fmt_vol(x, pos):
    if x >= 1e8:
        return f"{x/1e8:.1f}亿"
    if x >= 1e4:
        return f"{x/1e4:.0f}万"
    return f"{x:.0f}"


def render_index_chart(name: str, analysis: dict, out_dir: str, days: int = 90) -> str:
    """渲染单个指数图, 返回图片路径。"""
    df, _ = core.fetch_index(name, limit=500)
    df = core.add_indicators(df).tail(days).copy()
    levels = analysis["levels"]
    chan = analysis["chan"]
    wy = analysis["wyckoff"]

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(13, 8), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1]},
    )
    fig.patch.set_facecolor("#fbfcfe")
    ax.set_facecolor("#fbfcfe")
    axv.set_facecolor("#fbfcfe")

    x = range(len(df))
    # K线
    for i, (_, row) in enumerate(df.iterrows()):
        color = UP_FILL if row["close"] >= row["open"] else DOWN_FILL
        ax.plot([i, i], [row["low"], row["high"]], color=color, lw=1.0, zorder=2)
        y0, y1 = row["open"], row["close"]
        ax.add_patch(Rectangle(
            (i - 0.32, min(y0, y1)), 0.64, max(abs(y1 - y0), (df["high"].max()-df["high"].min())*0.002),
            facecolor=color if row["close"] >= row["open"] else color,
            edgecolor=color, alpha=0.9, zorder=3,
        ))
    # 均线
    for ma, col in [("ma5", "#f39c12"), ("ma20", "#2980b9"), ("ma60", "#8e44ad")]:
        if ma in df and df[ma].notna().any():
            ax.plot(x, df[ma], color=col, lw=1.3, label=ma.upper(), zorder=4)

    # 缠论近端中枢
    if chan.get("centers"):
        z = chan["centers"][-1]
        # 中枢按价格区间画横向色带
        ax.axhspan(z["zd"], z["zg"], color=ZONE_COLOR, alpha=0.12, zorder=1)
        ax.axhline(z["zg"], color=ZONE_COLOR, lw=0.9, ls=":", alpha=0.7, zorder=1)
        ax.axhline(z["zd"], color=ZONE_COLOR, lw=0.9, ls=":", alpha=0.7, zorder=1)
        ax.text(len(df) - 1, z["zg"], f" 缠论中枢上沿 {z['zg']}",
                color=ZONE_COLOR, fontsize=8, va="bottom", ha="right")

    # 压力位 (红虚线)
    for j, r in enumerate(levels["resistances"]):
        ax.axhline(r, color=RES_COLOR, lw=1.2, ls="--", alpha=0.85, zorder=5)
        ax.text(len(df) - 1, r, f" 压力{j+1} {r}", color=RES_COLOR,
                fontsize=9, va="center", ha="right", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=RES_COLOR, alpha=0.85))
    # 支撑位 (绿虚线)
    for j, s in enumerate(levels["supports"]):
        ax.axhline(s, color=SUP_COLOR, lw=1.2, ls="--", alpha=0.85, zorder=5)
        ax.text(len(df) - 1, s, f" 支撑{j+1} {s}", color=SUP_COLOR,
                fontsize=9, va="center", ha="right", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=SUP_COLOR, alpha=0.85))

    last_close = levels["last"]
    ax.axhline(last_close, color="#333333", lw=0.8, alpha=0.5, zorder=5)
    ax.text(0, last_close, f" 现价 {last_close}", color="#333333", fontsize=9,
            va="center", ha="left", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="#fffbe6", ec="#999", alpha=0.9))

    # 标题与信息框
    chg = analysis["change_pct"]
    chg_color = RED if chg >= 0 else GREEN
    title = f"{name} ({analysis['code']})  收盘 {last_close}  "
    title += f"{'+' if chg>=0 else ''}{chg}%  [{analysis['as_of']}]"
    ax.set_title(title, fontsize=13, fontweight="bold", color="#2c3e50", pad=10)
    info = (
        f"威科夫阶段: {wy['phase']}\n"
        f"缠论: {chan['latest_signal']}\n"
        f"背驰: {chan['divergence']}"
    )
    ax.text(0.01, 0.97, info, transform=ax.transAxes, fontsize=8.5, va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.4", fc="#f4f7fb", ec="#c9d4e3", alpha=0.95), zorder=6)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25, ls=":")
    ax.set_ylabel("指数点位", fontsize=9)

    # 成交量副图
    for i, (_, row) in enumerate(df.iterrows()):
        color = UP_FILL if row["close"] >= row["open"] else DOWN_FILL
        axv.bar(i, row["volume"], color=color, alpha=0.55, width=0.7)
    axv.plot(x, df["vol_ma5"], color="#f39c12", lw=1.0, label="VOL-MA5")
    axv.plot(x, df["vol_ma20"], color="#2980b9", lw=1.0, label="VOL-MA20")
    axv.yaxis.set_major_formatter(FuncFormatter(_fmt_vol))
    axv.grid(alpha=0.25, ls=":")
    axv.set_ylabel("成交量", fontsize=9)

    # X轴日期刻度
    tick_step = max(len(df) // 6, 1)
    ticks = list(range(0, len(df), tick_step))
    axv.set_xticks(ticks)
    axv.set_xticklabels([df.index[i].strftime("%m-%d") for i in ticks], fontsize=8, rotation=0)

    footer = f"数据源: {analysis['source']} | 量价/缠论为量化模型输出, 仅供参考, 不构成投资建议 | 字体: {FONT}"
    fig.text(0.01, 0.01, footer, fontsize=7, color="#888")
    fig.tight_layout(rect=[0, 0.03, 1, 1])

    os.makedirs(out_dir, exist_ok=True)
    safe = name.replace(" ", "")
    path = os.path.join(out_dir, f"chart_{safe}.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def render_all(analysis_bundle: dict, out_dir: str) -> list[str]:
    paths = []
    for name, a in analysis_bundle["indices"].items():
        if "error" in a:
            print(f"[render] 跳过 {name}: {a['error']}")
            continue
        try:
            p = render_index_chart(name, a, out_dir)
            paths.append(p)
            print(f"[render] {name} -> {p}")
        except Exception as e:
            print(f"[render] {name} 失败: {type(e).__name__}: {e}")
    return paths


if __name__ == "__main__":
    import json
    bundle = core.analyze_all(limit=300)
    ps = render_all(bundle, os.path.join(os.path.dirname(__file__), "output"))
    print("生成图片:", ps)
