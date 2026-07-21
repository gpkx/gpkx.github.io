#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import re
import time
import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests
from playwright.async_api import async_playwright
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps

TARGET_URL = os.getenv("TARGET_URL", "https://gpkx.github.io/")
TV_CHART_URL = os.getenv("TV_CHART_URL", "https://cn.tradingview.com/chart/fxUqvHrk/")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
TV_SESSION_ID = os.getenv("TV_SESSION_ID", "").strip()

TZ = pytz.timezone("Asia/Shanghai")
NOW = datetime.now(TZ)
TODAY_WEEKDAY = NOW.weekday()
IS_SATURDAY = TODAY_WEEKDAY == 5
REPORT_TYPE = "weekly" if IS_SATURDAY else "daily"
TIME_LABEL = "周线收盘" if IS_SATURDAY else "日线"
TV_INTERVAL = "1W" if IS_SATURDAY else "1D"
DATE_STR = NOW.strftime("%m月%d日")
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

VIDEO_W, VIDEO_H = 1080, 1920
VIDEO_FPS = 30
VIDEO_ASPECT = "9:16"

OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)

COVER_TITLE = "本周ETF异动Top4" if IS_SATURDAY else "今日ETF异动Top4"
COVER_SUBTITLE = f"({DATE_STR}-{TIME_LABEL})"
OUTRO_TTS_TEXT = "本内容仅供学习和观察，不构成投资建议。"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

def get_tv_symbol(code):
    return f"SSE:{code}" if code.startswith(("5", "6")) else f"SZSE:{code}"

def parse_pct(val):
    try:
        return float(str(val).replace("%", "").replace("+", "").strip())
    except:
        return 0.0

def clean_for_tts(text):
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = text.replace("*", "").replace("_", "").replace("#", "").replace("`", "")
    return text.strip()

def get_font(size):
    for fp in FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except:
                pass
    return ImageFont.load_default()

def resolve_col_date(day):
    for delta in range(-7, 8):
        cand = NOW + timedelta(days=delta)
        if cand.day == day:
            return cand.date().isoformat()
    return None

def build_etf_payload(etf_list):
    rows = []
    for i, e in enumerate(etf_list, 1):
        rows.append({
            "rank": i,
            "name": e.get("name", ""),
            "code": e.get("code", ""),
            "change": e.get("change", ""),
            "change_value": parse_pct(e.get("change", "0")),
            "tv_symbol": get_tv_symbol(e.get("code", "")),
            "date": e.get("data_date", NOW.date().isoformat()),
        })
    return rows

async def fetch_etf_rows():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": VIDEO_W, "height": VIDEO_H}, accept_downloads=True)

        if TV_SESSION_ID:
            await context.add_cookies([
                {"name": "sessionid", "value": TV_SESSION_ID, "domain": ".tradingview.com", "path": "/"},
                {"name": "sessionid", "value": TV_SESSION_ID, "domain": ".cn.tradingview.com", "path": "/"},
            ])

        page = await context.new_page()
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        row_data = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('tr, .el-table__row')).map(tr => {
                return Array.from(tr.querySelectorAll('td, th')).map(td => td.innerText.trim());
            }).filter(row => row.length >= 7);
        }""")

        header = next((r for r in row_data if any(('周线' in (c or '')) or ('周一' in (c or '')) or ('周日' in (c or '')) for c in r)), [])
        weekly_col_idx = None
        col_dates = {}

        for idx, h in enumerate(header):
            h_str = h or ""
            if "周线" in h_str:
                weekly_col_idx = idx
                continue
            m = re.search(r"(\d{1,2})\s*日", h_str)
            if m:
                col_dates[idx] = resolve_col_date(int(m.group(1)))

        etf_list = []
        for row in row_data:
            if not row:
                continue
            name_cell = row[0]
            m = re.search(r"\b(5\d{5}|1\d{5})\b", name_cell)
            if not m:
                continue
            code = m.group(1)
            name = re.sub(r"\d+", "", name_cell).strip()
            target_val = ""
            target_date = None

            if IS_SATURDAY and weekly_col_idx is not None:
                if weekly_col_idx < len(row) and "%" in row[weekly_col_idx]:
                    target_val = row[weekly_col_idx]
            else:
                for idx in range(len(row) - 1, 0, -1):
                    if idx == weekly_col_idx:
                        continue
                    cell = row[idx] if idx < len(row) else ""
                    if "%" in cell:
                        target_val = cell
                        target_date = col_dates.get(idx)
                        break

            if target_val:
                etf_list.append({
                    "name": name,
                    "code": code,
                    "change": target_val,
                    "data_date": target_date or NOW.date().isoformat(),
                })

        etf_list.sort(key=lambda x: abs(parse_pct(x["change"])), reverse=True)
        await browser.close()
        return etf_list[:4]

def build_ai_prompt(etf_list):
    etf_json = json.dumps(build_etf_payload(etf_list), ensure_ascii=False, indent=2)
    return f"""
你是一个资深财经内容策划 + 自媒体编导 + 合规文案助手。

【账号定位】
- ETF 异动监测与验证记录者。
- 用长期样本展示触发后的走势统计。
- 不做收益承诺，不做喊单，不做确定性判断。

【平台目标】
- 抖音/快手：强钩子、强节奏、强画面感。
- 视频号：稳重、可信、适合分享。
- 公众号：完整复盘、方法说明、长期信任。
- 小红书：搜索、收藏、方法论、图表对照。
- 私域：领取完整名单、历史验证、次日提醒。

【输入】
日期：{DATE_STR}
模式：{REPORT_TYPE}
时间标签：{TIME_LABEL}
触发ETF列表：
{etf_json}

【输出】
严格输出 JSON，字段固定如下：
{{
  "summary": "一句话总结今天市场异动情况，适合视频开头",
  "dy_hook": "抖音/快手短视频钩子，15-25字",
  "dy_script": "适合口播的短视频脚本，60-120字",
  "cover_title": "封面标题，适合竖屏大字",
  "cover_subtitle": "封面副标题，体现日期和日线/周线",
  "xhs_title": "小红书标题，偏搜索和收藏",
  "xhs_article": "小红书正文，300-600字",
  "gzh_title": "公众号标题，偏深度和专业感",
  "gzh_article": "公众号正文，600-1200字",
  "video_intro": "视频前3秒导语，适合TTS",
  "video_outro": "视频结尾统一免责声明，简洁自然",
  "comment_pin": "置顶评论，引导互动",
  "private_traffic_copy": "私域引流文案，引导领取完整名单/历史验证",
  "membership_offer": "会员卖点说明，强调效率、记录、验证、提醒",
  "risk_disclaimer": "合规风险提示",
  "followup_questions": ["今日复盘问题1", "今日复盘问题2", "今日复盘问题3"]
}}
""".strip()

def call_deepseek_director(etf_list):
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是资深数据专家兼矩阵运营主编。严格返回 JSON。"},
            {"role": "user", "content": build_ai_prompt(etf_list)},
        ],
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
    }

    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        json=payload,
        timeout=90,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```json\s*|^```\s*|\s*```$", "", content, flags=re.I)
    return json.loads(content.strip())

def ensure_cover_html(ai_script, etf_list):
    items = []
    for e in etf_list:
        color = "red" if parse_pct(e["change"]) > 0 else "green"
        items.append(f"""
        <div style="display:flex;justify-content:space-between;align-items:center;
        background:rgba(255,255,255,0.86);border-radius:24px;padding:18px 24px;margin:16px 0;">
          <div style="font-size:34px;font-weight:700;color:#0f172a;">{e["name"]}</div>
          <div style="font-size:34px;font-weight:800;color:{color};">{e["change"]}</div>
        </div>
        """)
    body = "\n".join(items)
    return f"""
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body {{
  margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:Alibaba PuHuiTi, Microsoft YaHei, sans-serif;
  background:linear-gradient(135deg,#f8fafc,#e2e8f0);
}}
.wrap {{
  width:86%;text-align:center;
}}
.title {{
  font-size:62px;font-weight:800;color:#0f172a;margin-bottom:16px;
}}
.sub {{
  font-size:34px;color:#475569;margin-bottom:28px;
}}
.card {{
  padding:24px;border-radius:32px;background:rgba(255,255,255,0.06);
}}
</style></head>
<body>
<div class="wrap">
  <div class="title">{ai_script.get("cover_title", COVER_TITLE)}</div>
  <div class="sub">{ai_script.get("cover_subtitle", COVER_SUBTITLE)}</div>
  <div class="card">
    {body}
  </div>
</div>
</body></html>
""".strip()

async def safe_generate_tts(text, filename, retries=3):
    text = clean_for_tts(text)
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+5%")
            await communicate.save(filename)
            return True
        except Exception:
            if attempt < retries - 1:
                await asyncio.sleep(3)
            else:
                raise

def save_review_bundle(ai_script, etf_list):
    payload = {
        "meta": {
            "date": NOW.isoformat(),
            "report_type": REPORT_TYPE,
            "time_label": TIME_LABEL,
        },
        "etf_list": etf_list,
        "ai_script": ai_script,
    }
    with open(OUT_DIR / f"review_bundle_{FILE_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

async def main():
    etf_list = await fetch_etf_rows()
    if not etf_list:
        etf_list = [{"name": "暂无触发", "code": "000000", "change": "0%", "data_date": NOW.date().isoformat()}]

    ai_script = call_deepseek_director(etf_list)
    save_review_bundle(ai_script, etf_list)

    with open(OUT_DIR / f"ai_script_{FILE_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(ai_script, f, ensure_ascii=False, indent=2)

    cover_html = ensure_cover_html(ai_script, etf_list)
    with open(OUT_DIR / f"cover_{FILE_SUFFIX}.html", "w", encoding="utf-8") as f:
        f.write(cover_html)

    with open(OUT_DIR / f"publish_copy_{FILE_SUFFIX}.txt", "w", encoding="utf-8") as f:
        f.write(
            f"{ai_script.get('summary','')}\n\n"
            f"{ai_script.get('dy_hook','')}\n\n"
            f"{ai_script.get('private_traffic_copy','')}\n\n"
            f"{ai_script.get('membership_offer','')}\n\n"
            f"{ai_script.get('risk_disclaimer','')}"
        )

if __name__ == "__main__":
    asyncio.run(main())
