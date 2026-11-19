# -*- coding: utf-8 -*-
"""
main.py —— 每日复盘技能主入口
=============================
用法:
  python main.py premarket     # 盘前操作预期
  python main.py postmarket    # 盘后复盘
  python main.py evening       # 晚间美股+消息汇总
  python main.py all           # 三段全跑(测试用, 会推3条)
  python main.py premarket --no-push   # 只生成不推送(本地预览)

流程: 取数分析 -> 渲染四大指数图 -> 上传图片得外链 -> 生成报告 -> Server酱 推送到微信

环境变量:
  SERVERCHAN_SENDKEY  主推送(Server酱, 免费/免实名, 每天5条), sctxxx 或 sctpxxx 开头
  PUSHPLUS_TOKEN      可选备份推送(PushPlus, 实名需付费), 留空则不启用
  GITHUB_REPO         必填(要图片时), 形如 username/repo, 用作图床
  GITHUB_TOKEN        可选, 用于把图片提交到图床仓库(Actions自动提供)
  IMAGE_BASE_URL      可选, 直接指定图片外链前缀(优先级高于自动git)

默认渠道 Server酱: 微信扫码关注服务号拿 SendKey, 免费, 无需实名/付费,
desp 支持 Markdown(含表格+图片), 无需每日手动激活。详见 部署指南.html。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import core
import render_charts
import report

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "output")
DATA = os.path.join(ROOT, "data")

SESSION_LABEL = {
    "premarket": "盘前操作预期",
    "postmarket": "盘后复盘",
    "evening": "晚间美股与消息汇总",
}


def _now_cn() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 图片外链: 提交到 GitHub 图床, 返回 {文件名: https外链}
# ---------------------------------------------------------------------------
def publish_images(image_paths: list[str]) -> dict[str, str]:
    os.makedirs(OUT, exist_ok=True)
    repo = os.environ.get("GITHUB_REPO", "")       # username/repo
    token = os.environ.get("GITHUB_TOKEN", "")
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    names = [os.path.basename(p) for p in image_paths]

    if not repo:
        print("[img] 未配置 GITHUB_REPO, 报告将不含图片")
        return {}

    # 1) 把图片提交到仓库 output/ (Actions 里已 checkout 且已配好 git 身份)
    for p in image_paths:
        try:
            subprocess.run(["git", "add", "-f", p], check=True, capture_output=True, text=True)
        except Exception as e:
            print(f"[img] git add 失败 {os.path.basename(p)}: {e}")
    sha = ""
    try:
        subprocess.run(["git", "commit", "-m", f"charts {_now_cn()}"],
                       capture_output=True, text=True)
        # 取本次提交 SHA, 用作 CDN 版本号, 保证每天的图片都能刷新、不被缓存
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        sha = r.stdout.strip()[:12]
        push = subprocess.run(["git", "push"], capture_output=True, text=True)
        if push.returncode != 0:
            print("[img] git push 失败:", push.stderr[:300])
    except Exception as e:
        print("[img] git commit/push 异常:", e)

    # 2) 拼外链
    #    - 自定义前缀优先
    #    - 否则 jsDelivr CDN(国内快) + SHA 版本号(绕过缓存)
    #    - SHA 取不到时回退 raw
    if base:
        return {n: f"{base}/{n}" for n in names}
    owner, reponame = repo.split("/", 1)
    if sha:
        return {n: f"https://cdn.jsdelivr.net/gh/{owner}/{reponame}@{sha}/output/{n}"
                for n in names}
    return {n: f"https://raw.githubusercontent.com/{owner}/{reponame}/main/output/{n}"
            for n in names}


# ---------------------------------------------------------------------------
# PushPlus 推送
# ---------------------------------------------------------------------------
def _md_to_html(md: str) -> str:
    """极简 markdown -> html (PushPlus html 模板)。避免引入第三方依赖。"""
    import html as _html
    lines = md.splitlines()
    out = []
    in_table = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # 分隔行
            if not in_table:
                out.append("<table>"); in_table = True
            tag = "th" if not out or "<table>" in out[-2:] else "td"
            out.append("<tr>" + "".join(f"<{tag}>{_html.escape(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            out.append("</table>"); in_table = False
        if s.startswith("# "):
            out.append(f"<h2>{_html.escape(s[2:])}</h2>")
        elif s.startswith("## "):
            out.append(f"<h3>{_html.escape(s[3:])}</h3>")
        elif s.startswith("> "):
            out.append(f"<blockquote style='color:#888;font-size:13px'>{_html.escape(s[2:])}</blockquote>")
        elif s.startswith("- "):
            out.append(f"<p style='margin:3px 0'>• {_html.escape(s[2:])}</p>")
        elif s.startswith("---"):
            out.append("<hr/>")
        elif s == "":
            out.append("<br/>")
        else:
            out.append(f"<p style='margin:3px 0'>{_html.escape(s)}</p>")
    if in_table:
        out.append("</table>")
    return "".join(out)


def _md_to_txt(md: str) -> str:
    """markdown -> 干净的纯文本 (微信ClawBot渠道只支持 txt 模板)。"""
    import re
    lines = []
    for ln in md.splitlines():
        s = ln.rstrip()
        if s.startswith("|"):
            # 表格行: 转成"列1: 值1 | 列2: 值2"的可读形式
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            lines.append("  " + " ｜ ".join(cells))
            continue
        if s.startswith("---"):
            lines.append("────────────")
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)       # 标题符号
        s = s.replace("**", "").replace("__", "")  # 加粗
        s = re.sub(r"^>\s*", "", s)            # 引用
        s = re.sub(r"^-\s*", "• ", s)          # 列表
        lines.append(s)
    return "\n".join(lines)


def _http_post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    return json.loads(resp)


def _push_serverchan(title: str, markdown: str, image_urls: dict[str, str]) -> bool:
    """Server酱(免费/免实名): desp 支持 Markdown(含表格+图片), 每天5条。"""
    key = os.environ.get("SERVERCHAN_SENDKEY", "")
    if not key:
        return False
    # SendKey 两种格式: sct 开头走 sctapi; sctp{uid}t... 走 push.ft07.com
    if key.startswith("sctp"):
        import re as _re
        m = _re.match(r"sctp(\d+)t", key)
        uid = m.group(1) if m else ""
        url = f"https://{uid}.push.ft07.com/send/{key}.send" if uid else ""
    else:
        url = f"https://sctapi.ftqq.com/{key}.send"
    if not url:
        print("[push] SendKey 格式无法识别")
        return False

    order = ["chart_上证指数.png", "chart_深证成指.png", "chart_创业板指.png", "chart_科创50.png"]
    desp = markdown
    links = [fn for fn in order if fn in image_urls]
    if links:
        desp += "\n\n## 四大指数技术分析图\n"
        desp += "图片加载稍慢请稍候;点图可看大图,或点下方链接在新窗口打开原图。\n"
        for fn in links:
            label = fn.replace("chart_", "").replace(".png", "")
            url_i = image_urls[fn]
            desp += f"\n**{label}**  [👉 看大图]({url_i})\n\n![]({url_i})\n"
    # Server酱 title 不能含换行
    safe_title = title.replace("\n", " ").replace("\r", " ")
    try:
        result = _http_post_json(url, {"title": safe_title, "desp": desp})
        ok = result.get("code") == 0
        print(f"[push] Server酱 {'成功' if ok else '失败'}: {result.get('message', result)}")
        return ok
    except Exception as e:
        print(f"[push] Server酱异常: {type(e).__name__}: {e}")
        return False


def _push_pushplus(title: str, markdown: str, image_urls: dict[str, str]) -> bool:
    """PushPlus 可选备份(实名需付费)。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if not token:
        return False
    order = ["chart_上证指数.png", "chart_深证成指.png", "chart_创业板指.png", "chart_科创50.png"]
    body_html = _md_to_html(markdown)
    imgs = ""
    for fn in order:
        url = image_urls.get(fn)
        if url:
            imgs += f"<p style='text-align:center'><img src='{url}' style='width:100%;max-width:600px'/></p>"
    if imgs:
        body_html += "<h3>四大指数技术分析图</h3>" + imgs
    try:
        result = _http_post_json(
            "https://www.pushplus.plus/send",
            {"token": token, "title": title, "content": body_html, "template": "html"},
        )
        ok = result.get("code") == 200
        print(f"[push] PushPlus {'成功' if ok else '失败'}: {result.get('msg', result)}")
        return ok
    except Exception as e:
        print(f"[push] PushPlus异常: {type(e).__name__}: {e}")
        return False


def push_to_wechat(title: str, markdown: str, image_urls: dict[str, str]) -> bool:
    """主推送: Server酱(免费)优先, PushPlus 作可选备份。"""
    ok = _push_serverchan(title, markdown, image_urls)
    if ok:
        return True
    # Server酱未配置或失败时, 尝试 PushPlus 备份
    if os.environ.get("PUSHPLUS_TOKEN", ""):
        print("[push] 尝试 PushPlus 备份通道...")
        return _push_pushplus(title, markdown, image_urls)
    if not os.environ.get("SERVERCHAN_SENDKEY", ""):
        print("[push] 未配置 SERVERCHAN_SENDKEY, 跳过推送")
    return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_session(session: str, no_push: bool = False) -> dict:
    print(f"\n{'='*50}\n[{_now_cn()}] 开始: {SESSION_LABEL[session]}\n{'='*50}")
    bundle = core.analyze_all(limit=400)
    sectors = core.fetch_sector_flow()
    us = core.fetch_us_markets() if session == "evening" else None

    # 保存分析结果
    os.makedirs(DATA, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    with open(os.path.join(DATA, f"analysis_{session}_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump({"bundle": bundle, "sectors": sectors, "us": us}, f, ensure_ascii=False, indent=2)

    # 渲染图片 (三段都带图)
    images = render_charts.render_all(bundle, OUT)
    image_urls = publish_images(images)

    # 生成报告
    md = report.build(session, bundle, sectors, us)
    md_path = os.path.join(OUT, f"report_{session}_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[main] 报告已保存: {md_path}")

    pushed = False
    if not no_push:
        pushed = push_to_wechat(f"{SESSION_LABEL[session]} · {_now_cn()}", md, image_urls)

    return {"session": session, "report": md_path, "images": images, "pushed": pushed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", choices=["premarket", "postmarket", "evening", "all"])
    ap.add_argument("--no-push", action="store_true", help="只生成不推送")
    args = ap.parse_args()

    sessions = list(SESSION_LABEL) if args.session == "all" else [args.session]
    results = []
    for s in sessions:
        results.append(run_session(s, args.no_push))
    print("\n[main] 完成:", json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
