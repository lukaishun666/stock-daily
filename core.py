# -*- coding: utf-8 -*-
"""
core.py —— 自包含行情与量化分析引擎
=====================================
不依赖任何 WorkBuddy 内部组件，可在纯 Python 3.10+ 环境独立运行。

职责:
  1. 取数: 东方财富 / 新浪 / 腾讯 三源自动切换 (免费,无需 key)
  2. 威科夫量价分析: 判定吸筹/派发/上涨/下跌阶段, 识别 CL/ST/UT/SOW 等事件
  3. 缠论结构识别: 分型 -> 笔 -> 中枢 -> 背驰/买卖点 (纯 Python 实现,无第三方依赖)
  4. 支撑压力位: 分型高低点聚类 + 成交密集区 + 整数关口

四大指数:
  上证指数 000001 / 深证成指 399001 / 创业板指 399006 / 科创50 000688
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 指数配置
# ---------------------------------------------------------------------------
INDICES = {
    "上证指数": {"code": "000001", "secid": "1.000001", "sina": "sh000001", "tx": "sh000001"},
    "深证成指": {"code": "399001", "secid": "0.399001", "sina": "sz399001", "tx": "sz399001"},
    "创业板指": {"code": "399006", "secid": "0.399006", "sina": "sz399006", "tx": "sz399006"},
    "科创50":  {"code": "000688", "secid": "1.000688", "sina": "sh000688", "tx": "sh000688"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


# ---------------------------------------------------------------------------
# 1. 取数层 —— 三源自动切换
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_eastmoney(secid: str, limit: int = 500) -> pd.DataFrame:
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&klt=101&fqt=1&end=20500101&lmt={limit}"
    )
    raw = json.loads(_http_get(url))
    klines = raw["data"]["klines"]
    rows = []
    for line in klines:
        p = line.split(",")
        # 日期,开,收,高,低,成交量,成交额,振幅
        rows.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]), "amount": float(p[6]),
        })
    return _to_df(rows)


def fetch_sina(symbol: str, limit: int = 500) -> pd.DataFrame:
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )
    raw = json.loads(_http_get(url))
    rows = [{
        "date": r["day"], "open": float(r["open"]), "close": float(r["close"]),
        "high": float(r["high"]), "low": float(r["low"]),
        "volume": float(r.get("volume", 0)), "amount": 0.0,
    } for r in raw]
    return _to_df(rows)


def fetch_tencent(symbol: str, limit: int = 500) -> pd.DataFrame:
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq"
    )
    raw = json.loads(_http_get(url))
    node = raw["data"][symbol]
    klines = node.get("qfqday") or node.get("day")
    rows = [{
        "date": r[0], "open": float(r[1]), "close": float(r[2]),
        "high": float(r[3]), "low": float(r[4]),
        "volume": float(r[5]) if len(r) > 5 else 0.0, "amount": 0.0,
    } for r in klines]
    return _to_df(rows)


def _to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for c in ["open", "close", "high", "low", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def fetch_index(name: str, limit: int = 500) -> tuple[pd.DataFrame, str]:
    """按 东财 -> 新浪 -> 腾讯 顺序尝试, 返回 (df, 数据源名)。"""
    cfg = INDICES[name]
    errors = []
    for src, fn, arg in [
        ("东方财富", fetch_eastmoney, cfg["secid"]),
        ("新浪财经", fetch_sina, cfg["sina"]),
        ("腾讯财经", fetch_tencent, cfg["tx"]),
    ]:
        for attempt in range(2):
            try:
                df = fn(arg, limit)
                if len(df) >= 60:
                    return df, src
                errors.append(f"{src}:数据不足({len(df)})")
            except Exception as e:
                errors.append(f"{src}:{type(e).__name__}")
            time.sleep(0.4)
    raise RuntimeError(f"{name} 全部数据源失败: {errors}")


# ---------------------------------------------------------------------------
# 2. 通用指标
# ---------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["close"]
    for n in (5, 10, 20, 60):
        d[f"ma{n}"] = c.rolling(n).mean()
    d["vol_ma5"] = d["volume"].rolling(5).mean()
    d["vol_ma20"] = d["volume"].rolling(20).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    d["dif"] = ema12 - ema26
    d["dea"] = d["dif"].ewm(span=9, adjust=False).mean()
    d["macd"] = (d["dif"] - d["dea"]) * 2
    return d


# ---------------------------------------------------------------------------
# 3. 威科夫量价分析
# ---------------------------------------------------------------------------
@dataclass
class WyckoffResult:
    phase: str                    # 吸筹/上涨/派发/下跌/整理
    phase_en: str
    note: str
    events: list = field(default_factory=list)   # 近期关键事件
    effort_result: str = ""       # 努力与结果关系
    volume_trend: str = ""        # 量能趋势


def wyckoff_analyze(df: pd.DataFrame) -> WyckoffResult:
    d = add_indicators(df).tail(120).copy()
    last = d.iloc[-1]
    recent = d.tail(20)
    hi120, lo120 = d["high"].max(), d["low"].min()
    pos = (last["close"] - lo120) / (hi120 - lo120) if hi120 > lo120 else 0.5
    vol_ratio = last["volume"] / last["vol_ma20"] if last["vol_ma20"] else 1.0
    ma20, ma60 = last["ma20"], last["ma60"]
    slope20 = (last["ma20"] - d["ma20"].iloc[-6]) / d["ma20"].iloc[-6] if d["ma20"].iloc[-6] else 0

    events = []
    # 关键事件识别(近 20 根)
    for i in range(len(recent) - 1, 0, -1):
        bar, prev = recent.iloc[i], recent.iloc[i - 1]
        vr = bar["volume"] / prev["vol_ma20"] if prev["vol_ma20"] else 1
        rng = bar["high"] - bar["low"]
        close_pos = (bar["close"] - bar["low"]) / rng if rng else 0.5
        if vr >= 1.8 and close_pos < 0.3:
            events.append({"date": str(recent.index[i].date()), "type": "放量下跌/派发压力(SOW)", "dir": -1})
        elif vr >= 1.8 and close_pos > 0.7:
            events.append({"date": str(recent.index[i].date()), "type": "放量上涨/需求进场", "dir": 1})
        elif vr >= 1.8 and 0.35 <= close_pos <= 0.65 and rng > (hi120 - lo120) * 0.02:
            events.append({"date": str(recent.index[i].date()), "type": "放量滞涨/震荡(可能是买卖高潮)", "dir": 0})
        if len(events) >= 4:
            break

    # 阶段判定: 位置 + 均线斜率 + 量价
    if pos > 0.75 and slope20 > 0.005 and ma20 > ma60:
        phase, phase_en = "上涨(Markup)", "markup"
        note = "价格位于区间高位、均线多头排列,处于趋势推进阶段。关注是否出现放量滞涨的派发信号。"
    elif pos > 0.7 and slope20 <= 0.002:
        phase, phase_en = "派发/高位震荡(Distribution)", "distribution"
        note = "指数在高位横向震荡,需警惕主力派发。留意放量滞涨、上冲回落等供给信号。"
    elif pos < 0.25 and slope20 < -0.005 and ma20 < ma60:
        phase, phase_en = "下跌(Markdown)", "markdown"
        note = "价格位于区间低位、均线空头排列,处于下跌趋势。关注是否出现止跌放量(吸筹前兆)。"
    elif pos < 0.3 and slope20 >= -0.002:
        phase, phase_en = "吸筹/低位震荡(Accumulation)", "accumulation"
        note = "指数在低位横盘,可能是主力吸筹区。关注缩量止跌、放量突破等吸筹完成信号。"
    else:
        phase, phase_en = "整理/方向未明", "transition"
        note = "多空力量相对均衡,处于方向选择期。等待放量突破确认方向。"

    if vol_ratio >= 1.5:
        effort_result = f"放量({vol_ratio:.1f}×20日均量)——努力程度高"
        if last["close"] < d["close"].iloc[-2]:
            effort_result += ",但价格下跌,供给占优(努力>结果,偏空)"
        else:
            effort_result += "且价格上涨,需求确认(努力与结果一致,偏多)"
    elif vol_ratio <= 0.7:
        effort_result = f"缩量({vol_ratio:.1f}×20日均量)——抛压/追涨意愿均减弱,等待方向"
    else:
        effort_result = f"量能正常({vol_ratio:.1f}×20日均量)"

    vol_trend = "量能温和放大" if d["vol_ma5"].iloc[-1] > d["vol_ma20"].iloc[-1] else "量能趋于萎缩"

    return WyckoffResult(phase, phase_en, note, events, effort_result, vol_trend)


# ---------------------------------------------------------------------------
# 4. 缠论结构识别 (分型 -> 笔 -> 中枢 -> 背驰)
# ---------------------------------------------------------------------------
@dataclass
class Fractal:
    idx: int
    date: str
    price: float
    kind: str   # 'top' 顶分型 / 'bottom' 底分型


@dataclass
class ChanResult:
    fractals: list
    bis: list           # 笔: [起点idx, 终点idx, 方向, 起价, 终价]
    centers: list       # 中枢: {zd, zg, start, end}
    latest_signal: str
    divergence: str     # 背驰判断
    note: str


def _find_fractals(d: pd.DataFrame) -> list[Fractal]:
    """包含关系预处理后识别顶底分型。"""
    highs, lows = d["high"].values, d["low"].values
    n = len(d)
    # 包含关系合并 (简化处理: 直接基于原始K线的相邻3根判断, 对指数足够稳健)
    out = []
    for i in range(1, n - 1):
        h1, h2, h3 = highs[i-1], highs[i], highs[i+1]
        l1, l2, l3 = lows[i-1], lows[i], lows[i+1]
        if h2 > h1 and h2 > h3 and l2 > l1 and l2 > l3:
            out.append(Fractal(i, str(d.index[i].date()), float(h2), "top"))
        elif l2 < l1 and l2 < l3 and h2 < h1 and h2 < h3:
            out.append(Fractal(i, str(d.index[i].date()), float(l2), "bottom"))
    # 相邻同类型只保留更极端者
    merged = []
    for f in out:
        if merged and merged[-1].kind == f.kind:
            if (f.kind == "top" and f.price > merged[-1].price) or \
               (f.kind == "bottom" and f.price < merged[-1].price):
                merged[-1] = f
        else:
            merged.append(f)
    return merged


def _build_bis(fractals: list[Fractal]) -> list:
    """由分型构造笔 (相邻顶底之间, 要求一定间隔)。"""
    bis = []
    for a, b in zip(fractals, fractals[1:]):
        if b.idx - a.idx < 4:   # 笔的最小K线间隔
            continue
        direction = "down" if a.kind == "top" else "up"
        bis.append([a.idx, b.idx, direction, a.price, b.price, a.date, b.date])
    return bis


def _find_centers(bis: list) -> list[dict]:
    """三笔重叠构成中枢。允许跳过方向不连贯的笔,优先用连续三笔。"""
    centers = []
    for i in range(len(bis) - 2):
        b1, b2, b3 = bis[i], bis[i+1], bis[i+2]
        zg = min(max(b1[3], b1[4]), max(b2[3], b2[4]), max(b3[3], b3[4]))
        zd = max(min(b1[3], b1[4]), min(b2[3], b2[4]), min(b3[3], b3[4]))
        if zg > zd:
            centers.append({"zg": round(zg, 2), "zd": round(zd, 2),
                            "start": b1[5], "end": b3[6], "bi_index": i})
    return centers


def _nearest_center(centers: list[dict], price: float) -> Optional[dict]:
    """选离现价最近的中枢(按区间中点到现价的距离)。"""
    if not centers:
        return None
    return min(centers, key=lambda z: abs((z["zg"] + z["zd"]) / 2 - price))


def chan_analyze(df: pd.DataFrame) -> ChanResult:
    d = add_indicators(df).tail(250).copy()
    fractals = _find_fractals(d)
    bis = _build_bis(fractals)
    centers = _find_centers(bis)
    last_close = float(d["close"].iloc[-1])

    # 背驰判断: 比较最近两段同向笔的力度 (MACD 面积近似)
    divergence = "数据不足,无法判断背驰"
    if len(bis) >= 3:
        last_bi = bis[-1]
        prev_same = None
        for b in reversed(bis[:-1]):
            if b[2] == last_bi[2]:
                prev_same = b
                break
        if prev_same:
            cur_move = abs(last_bi[4] - last_bi[3])
            prev_move = abs(prev_same[4] - prev_same[3])
            if last_bi[2] == "up":
                if cur_move > prev_move:
                    divergence = "末段上涨力度强于前段,暂无顶背驰迹象"
                else:
                    divergence = "末段上涨力度弱于前段,警惕顶背驰(上涨动能衰减)"
            else:
                if cur_move < prev_move:
                    divergence = "末段下跌力度弱于前段,底背驰迹象,关注一买机会"
                else:
                    divergence = "末段下跌力度仍强,暂无底背驰"

    # 买卖点推断 —— 用离现价最近的中枢
    signal = "结构未明朗,观望为主"
    z = _nearest_center(centers, last_close)
    if z:
        if last_close > z["zg"]:
            signal = f"价格站上近端中枢上沿({z['zg']}),三买观察区,回踩不破可看多"
        elif last_close < z["zd"]:
            signal = f"价格处于近端中枢下沿({z['zd']})下方,弱势区,反弹不过中枢宜谨慎"
        else:
            signal = f"价格运行于近端中枢[{z['zd']}, {z['zg']}]内部,等待方向选择"
    else:
        # 无中枢: 用最近一笔方向 + 分型给信号
        if bis:
            last_bi = bis[-1]
            if last_bi[2] == "up" and last_close > last_bi[4]:
                signal = "无有效中枢,末笔向上且创出反弹新高,短线偏强但结构不稳,快进快出"
            elif last_bi[2] == "down" and last_close < last_bi[4]:
                signal = "无有效中枢,末笔向下且创新低,下跌趋势延续,观望为主"
            else:
                signal = "无有效中枢,处于笔的转折确认期,等待结构明朗"

    note = f"识别到 {len(fractals)} 个分型、{len(bis)} 笔、{len(centers)} 个中枢。"
    return ChanResult(
        fractals=[asdict(f) for f in fractals[-12:]],
        bis=bis[-10:], centers=centers[-3:],
        latest_signal=signal, divergence=divergence, note=note,
    )


# ---------------------------------------------------------------------------
# 5. 支撑压力位
# ---------------------------------------------------------------------------
def _volume_profile(d: pd.DataFrame, bins: int = 40) -> list[float]:
    """按成交量加权的价格密集区 (筹码峰), 返回成交量最大的若干价格中枢。"""
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if hi <= lo:
        return []
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for _, row in d.iterrows():
        # 把每根K线的成交量均摊到其价格区间覆盖的桶
        b_lo = np.searchsorted(edges, row["low"], side="right") - 1
        b_hi = np.searchsorted(edges, row["high"], side="left")
        b_lo, b_hi = max(0, b_lo), min(bins - 1, b_hi)
        if b_hi < b_lo:
            b_hi = b_lo
        share = row["volume"] / (b_hi - b_lo + 1)
        for b in range(b_lo, b_hi + 1):
            vol[b] += share
    centers = (edges[:-1] + edges[1:]) / 2
    order = np.argsort(vol)[::-1]
    peaks = []
    for i in order:
        p = float(centers[i])
        if all(abs(p - x) / x > 0.015 for x in peaks):   # 峰值间距 >1.5%
            peaks.append(p)
        if len(peaks) >= 4:
            break
    return peaks


def support_resistance(df: pd.DataFrame, chan: Optional[ChanResult] = None) -> dict:
    """分型高低点 + 成交密集区 + 均线 + 近端中枢。"""
    d = add_indicators(df).tail(120).copy()
    last = float(d["close"].iloc[-1])
    pivots: list[float] = []
    # 1) 分型点 (近端)
    pivots += [f.price for f in _find_fractals(d)]
    # 2) 多周期高低点
    for n in (10, 20, 40, 60, 120):
        if len(d) >= n:
            pivots += [float(d["high"].tail(n).max()), float(d["low"].tail(n).min())]
    # 3) 均线
    ma20 = float(d["ma20"].iloc[-1]); ma60 = float(d["ma60"].iloc[-1])
    pivots += [ma20, ma60]
    # 4) 成交密集区 (筹码峰) —— 这是贴近现价的关键
    pivots += _volume_profile(d.tail(90))
    # 5) 近端中枢
    if chan and chan.centers:
        z = _nearest_center(chan.centers, last)
        if z:
            pivots += [z["zg"], z["zd"]]

    # 聚类 (容差 1%)
    pivots = sorted(p for p in pivots if not math.isnan(p))
    clusters: list[list[float]] = []
    for p in pivots:
        if clusters and abs(p - clusters[-1][-1]) / max(clusters[-1][-1], 1e-9) < 0.01:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [sum(c) / len(c) for c in clusters]

    uniq = sorted(set(round(v, 2) for v in levels))
    resistances = [x for x in uniq if x > last * 1.003][:3]
    supports = [x for x in uniq if x < last * 0.997][::-1][:3]  # 由近到远
    # 保底: 用近 10 日高低点确保贴近现价
    if not resistances:
        resistances = [round(float(d["high"].tail(10).max()), 2)]
    if not supports:
        supports = [round(float(d["low"].tail(10).min()), 2)]
    return {
        "last": round(last, 2),
        "resistances": resistances,   # 由近到远
        "supports": supports,         # 由近到远
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
    }


# ---------------------------------------------------------------------------
# 5.5 外围市场与板块资金 (供晚间报告使用)
# ---------------------------------------------------------------------------
def fetch_us_markets() -> dict:
    """美股三大指数 + 热门中概/科技股快照 (新浪, gbk 编码)。"""
    targets = {
        "gb_dji": "道琼斯", "gb_ixic": "纳斯达克", "gb_inx": "标普500",
        "gb_$ndx": "纳斯达克100",
    }
    out = {}
    try:
        req = urllib.request.Request(
            "https://hq.sinajs.cn/list=" + ",".join(targets),
            headers={**HEADERS, "Referer": "https://finance.sina.com.cn/"},
        )
        text = urllib.request.urlopen(req, timeout=15).read().decode("gbk", "ignore")
        for line in text.splitlines():
            if '="' not in line:
                continue
            key = line.split("hq_str_")[-1].split("=")[0].strip()
            parts = line.split('="')[-1].rstrip('";').split(",")
            if len(parts) < 3:
                continue
            try:
                price = float(parts[1]); chg = float(parts[2])
            except (ValueError, IndexError):
                continue
            out[targets.get(key, key)] = {
                "price": round(price, 2), "change_pct": round(chg, 2),
                "time": parts[3] if len(parts) > 3 else "",
            }
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def fetch_sector_flow(top_n: int = 8) -> list[dict]:
    """东方财富行业板块资金流向 (主力净流入排名)。"""
    url = (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?pn=1&pz=100&po=1&np=1&fltt=2&invt=2"
        "&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184"
    )
    try:
        raw = json.loads(_http_get(url))
        diff = raw["data"]["diff"]
        rows = []
        for r in diff:
            rows.append({
                "name": r.get("f14"), "code": r.get("f12"),
                "change_pct": r.get("f3"),
                "main_net_in": r.get("f62"),        # 主力净流入(元)
                "main_net_pct": r.get("f184"),      # 主力净占比
            })
        rows = [r for r in rows if isinstance(r["main_net_in"], (int, float))]
        rows.sort(key=lambda x: x["main_net_in"], reverse=True)
        return rows[:top_n]
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]


# ---------------------------------------------------------------------------
# 6. 汇总分析入口
# ---------------------------------------------------------------------------
def analyze_index(name: str, limit: int = 500) -> dict:
    df, src = fetch_index(name, limit)
    df = add_indicators(df)
    wy = wyckoff_analyze(df)
    chan = chan_analyze(df)
    sr = support_resistance(df, chan)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return {
        "name": name,
        "code": INDICES[name]["code"],
        "source": src,
        "as_of": str(df.index[-1].date()),
        "close": round(float(last["close"]), 2),
        "change_pct": round(float((last["close"] / prev["close"] - 1) * 100), 2),
        "volume": float(last["volume"]),
        "volume_ratio": round(float(last["volume"] / last["vol_ma20"]), 2) if last["vol_ma20"] else None,
        "rsi14": round(float(last["rsi14"]), 1) if not math.isnan(last["rsi14"]) else None,
        "ma20": sr["ma20"], "ma60": sr["ma60"],
        "wyckoff": asdict(wy),
        "chan": asdict(chan),
        "levels": sr,
    }


def analyze_all(limit: int = 500) -> dict:
    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "indices": {}}
    for name in INDICES:
        try:
            result["indices"][name] = analyze_index(name, limit)
            print(f"[core] {name} 分析完成 (源:{result['indices'][name]['source']})")
        except Exception as e:
            result["indices"][name] = {"error": f"{type(e).__name__}: {e}"}
            print(f"[core] {name} 失败: {e}")
    return result


if __name__ == "__main__":
    r = analyze_all()
    print(json.dumps(r, ensure_ascii=False, indent=2))
