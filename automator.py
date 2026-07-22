import os
import sys
import json
import asyncio
import subprocess
import re
import time
import shutil
from datetime import datetime, timedelta

import pytz
import requests
from PIL import Image, ImageOps, ImageDraw, ImageFont
from playwright.async_api import async_playwright
import edge_tts

TARGET_URL = "https://gpkx.github.io/"
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/"
TZ = pytz.timezone("Asia/Shanghai")
NOW = datetime.now(TZ)
TODAY_WEEKDAY = NOW.weekday()
IS_SATURDAY = TODAY_WEEKDAY == 5

if IS_SATURDAY:
    TIME_LABEL = "周线收盘"
    REPORT_TYPE = "weekly"
    TV_INTERVAL = "1W"
    TARGET_COL_IDX = -1
else:
    TIME_LABEL = "日线"
    REPORT_TYPE = "daily"
    TV_INTERVAL = "1D"
    TARGET_COL_IDX = TODAY_WEEKDAY + 1

DATE_STR = NOW.strftime("%m月%d日")
COVER_TITLE = "本周ETF涨跌幅Top4" if IS_SATURDAY else "今日ETF涨跌幅Top4"
COVER_SUBTITLE = f"({DATE_STR}-{TIME_LABEL})"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")
OUTRO_TTS_TEXT = "涨跌幅数据由特定指标自动生成，本内容不构成投资建议！"

VIDEO_W, VIDEO_H = 1080, 1920
VIDEO_FPS = 30

import math as _math
def _reduce_ratio(w, h):
    g = _math.gcd(w, h)
    return f"{w // g}:{h // g}"

VIDEO_ASPECT = _reduce_ratio(VIDEO_W, VIDEO_H)
_SUB_FONT_SIZE = max(6, round(14 * (1080 / VIDEO_H)))
_SUB_MAX_CHARS = max(10, int(40 * (VIDEO_W / 1920)))


def get_tv_symbol(code):
    if code.startswith(("5", "6")):
        return f"SSE:{code}"
    return f"SZSE:{code}"


def parse_pct_to_float(val_str):
    try:
        return float(val_str.replace("%", "").replace("+", ""))
    except:
        return 0.0


def _resolve_col_date(day):
    for delta in range(-7, 8):
        cand = NOW + timedelta(days=delta)
        if cand.day == day:
            return cand.date().isoformat()
    return None


def format_quant_voice(val_str):
    try:
        val = float(val_str.replace("%", "").replace("+", ""))
        if val > 0:
            return f"ATR涨幅为{abs(val)}%"
        elif val < 0:
            return f"ATR跌幅为{abs(val)}%"
        return "ATR 处于零轴震荡区"
    except:
        return "暂无有效读数"


def get_audio_duration(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.strip())


async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+5%")
            await communicate.save(filename)
            return True
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(3)
            else:
                raise Exception(f"TTS 失败: {e}")


def clean_for_tts(text):
    if not text:
        return ""
    if isinstance(text, dict):
        text = "，".join([str(v) for v in text.values() if isinstance(v, str)])
    elif not isinstance(text, str):
        text = str(text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace("*", "").replace("_", "").replace("#", "").replace("`", "")
    text = re.sub(r"(?i)\betf\b", " ETF ", text)
    text = re.sub(r"(?i)\batr\b", " ATR ", text)
    text = re.sub(r"(?i)\ba股\b", " A股 ", text)
    return text.strip()


def create_srt(text, duration, filename):
    def format_time(seconds):
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        ms = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"

    start_time = "00:00:00,000"
    end_time = format_time(duration)
    clean_text = text.replace(" ETF ", "ETF").replace(" ATR ", "ATR")
    lines = [clean_text[i:i + _SUB_MAX_CHARS] for i in range(0, len(clean_text), _SUB_MAX_CHARS)]
    text_block = "\n".join(lines)
    srt_content = f"1\n{start_time} --> {end_time}\n{text_block}\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(srt_content)


def get_subtitle_filter(srt_file):
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace("\\", "\\\\").replace(":", "\\:")
        return (
            f"subtitles={srt_path}:force_style="
            f"'FontName=Alibaba PuHuiTi,FontSize={_SUB_FONT_SIZE},"
            f"PrimaryColour=&H00000000,Outline=0,Shadow=0,MarginV=30,Alignment=2'"
        )
    return ""


def _sar_filter():
    return f"scale={VIDEO_W}:{VIDEO_H},setsar=1/1"


def _fit_to_canvas(src, size=(VIDEO_W, VIDEO_H), color=(255, 255, 255)):
    return ImageOps.pad(src.convert("RGB"), size, method=Image.Resampling.LANCZOS, color=color)


def _prepare_chart_image_file(img_path):
    src = Image.open(img_path).convert("RGB")
    _fit_to_canvas(src, (VIDEO_W, VIDEO_H)).save(img_path)


def create_zoom_video(img_path, output_video, duration, fps=VIDEO_FPS, zoom_type="main", srt_file=None):
    frames_dir = f"temp_frames_{os.path.basename(img_path).split('.')[0]}"
    if os.path.exists(frames_dir):
        shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    src = Image.open(img_path).convert("RGB")
    base = _fit_to_canvas(src, (VIDEO_W, VIDEO_H))
    W, H = VIDEO_W, VIDEO_H
    total_frames = max(1, int(duration * fps))

    if zoom_type == "tv":
        LATEST_KL_X = 0.72 * W
        LATEST_KL_Y = 0.50 * H
        START_ZOOM = 1.0
        END_ZOOM = 1.5

    for i in range(total_frames):
        if zoom_type == "tv":
            t = i / max(total_frames - 1, 1)
            zoom = START_ZOOM + (END_ZOOM - START_ZOOM) * t
            cw = max(1, int(W / zoom))
            ch = max(1, int(H / zoom))
            cx = int(LATEST_KL_X - cw / 2)
            cy = int(LATEST_KL_Y - ch / 2)
            cx = max(0, min(cx, W - cw))
            cy = max(0, min(cy, H - ch))
        else:
            cx, cy, cw, ch = 0, 0, W, H

        box = (cx, cy, cx + cw, cy + ch)
        frame = base.crop(box).resize((W, H), Image.Resampling.LANCZOS)
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=92)

    vf_filters = [_sar_filter()]
    sub_filter = get_subtitle_filter(srt_file)
    if sub_filter:
        vf_filters.append(sub_filter)

    cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", f"{frames_dir}/frame_%04d.jpg"]
    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(frames_dir)


def create_static_video(img_path, output_video, duration, fps=VIDEO_FPS, srt_file=None):
    vf_filters = [
        f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2,setsar=1/1"
    ]
    sub_filter = get_subtitle_filter(srt_file)
    if sub_filter:
        vf_filters.append(sub_filter)

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration)]
    cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p", output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _load_cjk_font(size):
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return None


def add_watermark_to_chart(img_path, text):
    try:
        img = Image.open(img_path).convert("RGBA")
        txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)
        target_w = img.width * 0.3
        max_h = img.height * 0.06
        lo, hi, best = 40, 6000, 40

        while lo <= hi:
            mid = (lo + hi) // 2
            f = _load_cjk_font(mid)
            if f is None:
                break
            bbox = f.getbbox(text)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if tw <= target_w and th <= max_h:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        font = _load_cjk_font(best) or ImageFont.load_default()
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        x = (img.width - text_w) / 2
        y = int(img.height * 0.13)
        draw.text((x, y), text, font=font, fill=(0, 0, 0, 128))
        out = Image.alpha_composite(img, txt_layer).convert("RGB")
        out.save(img_path)
    except Exception as e:
        print(f"⚠️ 图表添加水印失败: {e}")


def _build_ai_prompt(etf_list, time_label, report_type):
    if not etf_list:
        return f"""
你是一位资深A股财经自媒体总编导，负责把同一份ETF数据改写成不同平台可直接发布的内容。
今天【{DATE_STR}{time_label}】没有任何ETF触发阈值。

【总规则】
1. 只基于输入事实写作，不得补充外部未提供的数据、板块归属、行情背景或未来判断。
2. 绝不输出投资建议、买卖建议、仓位建议、止损建议、目标价、收益承诺、预测性语言。
3. 绝不使用夸大、极限词、恐吓式表达，不得编造“爆发、暴雷、起飞、崩盘”等结论。
4. 文风要像真人财经编辑，不要模板腔、口号腔、机器腔。
5. 输出必须严格为 JSON，且只输出 JSON，不要加解释，不要加 Markdown 代码块。

【本次任务】
今日没有ETF触发异动阈值。请输出适合不同平台发布的“无异动版”内容，语气客观、克制、专业。

【输出字段】
- "video_intro"
- "xhs_title"
- "xhs_article"
- "xhs_tags"
- "gzh_title"
- "gzh_article"
- "cover_html"

【无异动内容要求】
1. 不能硬凑“板块主线”或“资金偏好”。
2. 只能写“今日盘面整体平稳、未见明显异动”等保守表述。
3. 如果没有数据，不要虚构排名，不要编造ETF名称。
4. 封面也要保持简洁，不要出现错误导向信息。
"""
    return f"""
你是一位资深A股财经自媒体总编导，负责将同一份ETF数据改写成可直接发布到不同平台的内容。
今天【{DATE_STR}{time_label}】有以下ETF触发涨跌幅阈值：

【原始数据】
{json.dumps(etf_list, ensure_ascii=False, indent=2)}

【总规则】
1. 只基于输入数据写作，不得补充未提供的事实。
2. 不得预测后市，不得给出买卖、持仓、仓位、止损、抄底、追高等操作建议。
3. 不得使用“稳赚、暴富、必涨、必跌、抄底、逃顶、上车、梭哈”等表达。
4. 不得虚构板块归属；若无法从ETF名称直接判断，只能做中性描述，不要编造行业。
5. 允许表达客观强弱、涨跌幅度、排名、盘面结构，但只能是事实归纳。
6. 输出必须严格为 JSON，且只输出 JSON，不要加解释，不要加 Markdown 代码块。

【写作总目标】
同一份数据，输出四种不同用途的内容：
- 短视频口播：短、顺、能播。
- 小红书：强钩子、快阅读、适合收藏。
- 微信公众号：结构化复盘、专业克制。
- 封面：一眼看懂榜单主题和Top4数据。

【短视频口播要求】
- 字数20-30字。
- 必须包含“今天有X只ETF触发涨跌幅阈值”“前四名”或同义表达。
- 语气自然、像真人主播，不要书面化。
- 不要用英文夹杂，除 ETF 外尽量纯中文。

【etf_narratives要求】
- 必须输出与输入数量一致的数组。
- 每条必须严格对应同序ETF。
- 每条 15-35字。
- 每条都要先说ETF名称，再说涨跌幅，再给一句客观状态描述。
- 只能做事实归纳，不得出现建议、预测、情绪化煽动。

【小红书要求】
- 标题：12-18字，强钩子，但克制，最多2个emoji。
- 正文：180-260字。
- 结构必须是：
  1) 开头一句先给结论；
  2) 中间用分点列出4只ETF的数据与简短观察；
  3) 结尾加1句收束，可带3-5个真实相关Tag。
- 语气要更生活化、更有阅读节奏，但不能夸张、不能造势。
- 允许少量emoji，但不要堆砌。

【公众号要求】
- 标题：18-24字，专业、概括、克制。
- 正文：350-700字。
- 结构建议：
  1) 开头一段概述今日榜单特征；
  2) 中间分段逐条解释4只ETF的表现；
  3) 末尾总结盘面结构，但不能预测后市。
- 语言要像编辑写复盘，不要像带货文案。

【封面HTML要求】
- 1080x1920 竖版。
- 风格：现代金融风、浅色高级渐变背景、卡片式排版。
- 标题使用【{COVER_TITLE}】。
- 副标题使用【{COVER_SUBTITLE}】。
- 副标题下方必须展示Top4 ETF名称和涨跌幅。
- 涨跌幅染色规则：
  - 上涨：红色字体。
  - 下跌：绿色字体。
- 页面内容必须居中，整体简洁高级，适合手机首屏展示。
- 字体必须使用 'Alibaba PuHuiTi', 'Microsoft YaHei', sans-serif。

【输出字段】
- "video_intro"
- "etf_narratives"
- "xhs_title"
- "xhs_article"
- "xhs_tags"
- "gzh_title"
- "gzh_article"
- "cover_html"

【额外质量要求】
1. 标题不要重复用同一套句式。
2. 不要让所有平台文风完全一样。
3. 不要把“ETF”以外的术语强行英文化。
4. 如果信息不足，宁可写得短，也不要编造。
"""


def call_ai_director(etf_list, time_label, report_type):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！")
        sys.exit(1)

    url = "https://api.deepseek.com/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    prompt = _build_ai_prompt(etf_list, time_label, report_type)
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一位客观的财经数据观察员。擅长数据归纳与新媒体排版，但严禁输出任何主观投资建议。严格返回 JSON。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.25
    }

    last_err = None
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            raw_text = response.json()["choices"][0]["message"]["content"]
            clean_text = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw_text, flags=re.IGNORECASE)
            data = json.loads(clean_text.strip())
            for k in ["video_intro", "xhs_title", "xhs_article", "gzh_title", "gzh_article", "cover_html"]:
                data.setdefault(k, "")
            data.setdefault("xhs_tags", [])
            data.setdefault("etf_narratives", [])
            if not isinstance(data["xhs_tags"], list):
                data["xhs_tags"] = []
            if not isinstance(data["etf_narratives"], list):
                data["etf_narratives"] = []
            return data
        except Exception as e:
            last_err = e
            time.sleep(2)

    raise Exception(f"AI 生成失败: {last_err}")


def send_telegram(text, video_path=None, photos=None):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return

    tg_host = "https://api.telegram.org/bot"
    try:
        requests.post(f"{tg_host}{bot_token}/sendMessage", data={"chat_id": chat_id, "text": text}).raise_for_status()
        if video_path and os.path.exists(video_path):
            with open(video_path, "rb") as vf:
                requests.post(f"{tg_host}{bot_token}/sendVideo", data={"chat_id": chat_id}, files={"video": vf}, timeout=120).raise_for_status()
        if photos:
            for i in range(0, len(photos), 10):
                chunk, media_group, files = photos[i:i+10], [], {}
                for idx, img in enumerate(chunk):
                    if os.path.exists(img):
                        files[f"f{idx}"] = open(img, "rb")
                        media_group.append({"type": "photo", "media": f"attach://f{idx}"})
                if media_group:
                    requests.post(
                        f"{tg_host}{bot_token}/sendMediaGroup",
                        data={"chat_id": chat_id, "media": json.dumps(media_group)},
                        files=files,
                        timeout=60
                    ).raise_for_status()
                for f in files.values():
                    f.close()
    except Exception as e:
        print(f"🛑 推送至 Telegram 失败: {e}")
        sys.exit(1)


def mux_final_video(temp_v, temp_a, bgm_path, final_video):
    common_v_enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), "-aspect", VIDEO_ASPECT, "-movflags", "+faststart"]
    audio_enc = ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    if bgm_path and os.path.exists(bgm_path):
        cmd = [
            "ffmpeg", "-y", "-i", temp_v, "-i", temp_a, "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            f"[0:v]scale={VIDEO_W}:{VIDEO_H},setsar=1/1[v];"
            f"[1:a]volume=2.0[a1];[2:a]volume=0.15[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "[v]", "-map", "[a]",
        ] + common_v_enc + audio_enc + [final_video]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", temp_v, "-i", temp_a,
            "-vf", f"scale={VIDEO_W}:{VIDEO_H},setsar=1/1",
            "-map", "0:v:0", "-map", "1:a:0",
        ] + common_v_enc + audio_enc + [final_video]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    print(f"🚀 启动自媒体矩阵引擎 | 当前模式: {REPORT_TYPE} | {NOW}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": VIDEO_W, "height": VIDEO_H}, accept_downloads=True)

        tv_session = os.getenv("TV_SESSION_ID", "").strip()
        if tv_session:
            print("🔑 检测到 TV_SESSION_ID，正在注入授权 Cookie...")
            await context.add_cookies([
                {"name": "sessionid", "value": tv_session, "domain": ".tradingview.com", "path": "/"},
                {"name": "sessionid", "value": tv_session, "domain": ".cn.tradingview.com", "path": "/"}
            ])

        page = await context.new_page()
        print("🔍 正在提取核心数据...")
        etf_list = []

        def pick_pct_from_row(row, preferred_idx=None):
            candidates = []
            if preferred_idx is not None and preferred_idx < len(row):
                cell = row[preferred_idx]
                if isinstance(cell, str) and "%" in cell:
                    candidates.append(cell)

            for cell in row:
                if isinstance(cell, str) and "%" in cell:
                    candidates.append(cell)

            for cell in candidates:
                m = re.search(r"[-+]?\d+(?:\.\d+)?%", cell)
                if m:
                    return m.group(0)
            return ""

        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            row_data = await page.evaluate("""
() => {
  const tables = Array.from(document.querySelectorAll('table, .el-table, .el-table__body-wrapper, .table, .ant-table'));
  const rows = [];

  const pushRows = (root) => {
    Array.from(root.querySelectorAll('tr, .el-table__row')).forEach(tr => {
      const cells = Array.from(tr.querySelectorAll('th, td')).map(td => (td.innerText || td.textContent || '').trim());
      if (cells.length >= 3) rows.push(cells);
    });
  };

  if (tables.length) {
    tables.forEach(pushRows);
  } else {
    Array.from(document.querySelectorAll('tr, .el-table__row')).forEach(tr => {
      const cells = Array.from(tr.querySelectorAll('th, td')).map(td => (td.innerText || td.textContent || '').trim());
      if (cells.length >= 3) rows.push(cells);
    });
  }

  return rows.filter(r => r.some(c => c && c.length > 0));
}
""")

            if not row_data:
                raise Exception("页面未抓到任何表格行数据")

            header = []
            for r in row_data[:8]:
                joined = " ".join([c or "" for c in r])
                if re.search(r"周|日|月", joined):
                    header = r
                    break

            weekday_text_map = {
                0: ["周一", "星期一", "周1", "Mon"],
                1: ["周二", "星期二", "周2", "Tue"],
                2: ["周三", "星期三", "周3", "Wed"],
                3: ["周四", "星期四", "周4", "Thu"],
                4: ["周五", "星期五", "周5", "Fri"],
                5: ["周六", "星期六", "周6", "Sat"],
                6: ["周日", "星期日", "周7", "Sun"],
            }

            today_week_keys = weekday_text_map.get(TODAY_WEEKDAY, [])
            today_day_keys = [
                str(NOW.day),
                f"{NOW.day}日",
                NOW.strftime("%m-%d"),
                NOW.strftime("%-m-%-d") if os.name != "nt" else f"{NOW.month}-{NOW.day}",
            ]

            preferred_col_idx = None
            if header:
                for idx, h in enumerate(header):
                    h_str = (h or "").strip()
                    if IS_SATURDAY and any(k in h_str for k in ["周线", "周末", "W"]):
                        preferred_col_idx = idx
                        break
                    if any(wk in h_str for wk in today_week_keys) and any(dk in h_str for dk in today_day_keys):
                        preferred_col_idx = idx
                        break
                if preferred_col_idx is None:
                    for idx, h in enumerate(header):
                        h_str = (h or "").strip()
                        if any(wk in h_str for wk in today_week_keys):
                            preferred_col_idx = idx
                            break
                if preferred_col_idx is None:
                    for idx, h in enumerate(header):
                        h_str = (h or "").strip()
                        if any(dk in h_str for dk in today_day_keys):
                            preferred_col_idx = idx
                            break

            if preferred_col_idx is None:
                preferred_col_idx = TARGET_COL_IDX

            print(f"✅ 表头识别完成，优先列索引: {preferred_col_idx}")

            def extract_code_and_name(cell):
                if not cell:
                    return None, None
                code_match = re.search(r"\b(5\d{5}|1\d{5})\b", cell)
                if not code_match:
                    return None, None
                code = code_match.group(1)
                name = re.sub(r"\d+", "", cell).strip()
                return code, name

            for row in row_data:
                if not row:
                    continue

                name_cell = row[0] if len(row) > 0 else ""
                code, name = extract_code_and_name(name_cell)
                if not code:
                    continue

                target_val = ""

                if IS_SATURDAY:
                    target_val = pick_pct_from_row(row, preferred_col_idx)
                else:
                    target_val = pick_pct_from_row(row, preferred_col_idx)

                if not target_val:
                    continue

                target_date = None
                if not IS_SATURDAY and preferred_col_idx is not None and preferred_col_idx < len(row):
                    target_date = _resolve_col_date(NOW.day)

                etf_list.append({
                    "name": name,
                    "code": code,
                    "change": target_val,
                    "data_date": target_date
                })

            dedup = {}
            for item in etf_list:
                key = item["code"]
                if key not in dedup:
                    dedup[key] = item
                else:
                    old = dedup[key]
                    if abs(parse_pct_to_float(item["change"])) > abs(parse_pct_to_float(old["change"])):
                        dedup[key] = item

            etf_list = list(dedup.values())
            etf_list.sort(key=lambda x: abs(parse_pct_to_float(x["change"])), reverse=True)

            if etf_list:
                cf_url = os.getenv("CF_WORKER_URL", "").strip()
                cf_token = os.getenv("CF_API_TOKEN", "").strip()
                if cf_url and cf_token:
                    cf_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cf_token}"}

                    if not IS_SATURDAY:
                        today_iso = NOW.date().isoformat()
                        using_stale = all(e.get("data_date") and e["data_date"] != today_iso for e in etf_list)
                        if using_stale:
                            try:
                                requests.delete(f"{cf_url}?date={today_iso}", headers=cf_headers, timeout=30)
                                print(f"🧹 已清理今天({today_iso})的残留旧数据")
                            except Exception as e:
                                print(f"⚠️ 清理今天数据失败: {e}")

                    data_list = []
                    for etf in etf_list:
                        item = {"etf_code": etf["code"], "etf_name": etf["name"]}
                        if etf.get("data_date"):
                            item["date"] = etf["data_date"]
                        if IS_SATURDAY:
                            item["week_status"] = etf["change"]
                        else:
                            item["day_status"] = etf["change"]
                        data_list.append(item)

                    try:
                        cf_response = requests.post(cf_url, json=data_list, headers=cf_headers, timeout=30)
                        print(f"☁️ Cloudflare 同步结果: {cf_response.text}")
                    except Exception as e:
                        print(f"⚠️ 同步到 Cloudflare 失败: {e}")

                etf_list = etf_list[:4]
                print(f"✅ 成功提取 ETF 数量: {len(etf_list)}")
                for x in etf_list:
                    print(f"   - {x['name']} {x['change']}")
            else:
                print("⚠️ 未提取到 ETF 阈值数据，后续将进入无数据分支")

        except Exception as e:
            print(f"提取数据发生异常: {e}")

        if not etf_list:
            ai_script = call_ai_director([], TIME_LABEL, REPORT_TYPE)
            tg_msg = (
                f"📝 【小红书版】\n💡 {ai_script.get('xhs_title', '')}\n\n{ai_script.get('xhs_article', '')}"
                f"\n\n====================\n\n📝 【微信公众号版】\n💡 {ai_script.get('gzh_title', '')}\n\n{ai_script.get('gzh_article', '')}"
            )
            send_telegram(tg_msg)
            await browser.close()
            sys.exit(0)

        print("🎭 正在生成剧本并渲染封面 (含 Top4 数据)...")
        ai_script = call_ai_director(etf_list, TIME_LABEL, REPORT_TYPE)

        await page.set_content(ai_script.get("cover_html", ""))
        await page.wait_for_timeout(2000)
        await page.screenshot(path="cover_image.png")

        outro_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { background: linear-gradient(135deg, #f8fafc, #e2e8f0); display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Alibaba PuHuiTi', 'Microsoft YaHei'; height: 100vh; margin: 0; text-align: center; }
            .hook-box { background: rgba(255, 255, 255, 0.9); padding: 80px 80px; border-radius: 40px; box-shadow: 0 10px 40px rgba(0,0,0,0.05); margin-bottom: 40px; width: 75%; }
            .hook-title { color: #1e293b; font-size: 65px; font-weight: bold; margin-bottom: 40px; }
            .hook-text { font-size: 50px; line-height: 1.8; color: #475569; }
            .highlight { background: #3b82f6; color: white; padding: 15px 30px; border-radius: 20px; font-weight: bold; display: inline-block; margin-top: 30px;}
            .divider { width: 70%; border-top: 4px dashed #cbd5e1; margin: 50px 0; }
            .disclaimer-box { width: 80%; }
            .disclaimer-title { color: #64748b; font-size: 55px; margin-bottom: 30px; font-weight: bold; letter-spacing: 8px; }
            .disclaimer-text { font-size: 40px; line-height: 1.8; color: #94a3b8; }
        </style></head><body>
            <div class="hook-box">
                <div class="hook-title">每日4只外，还有40+</div>
                <div class="hook-text">
                    完整ETF阈值名单<br>
                    <span class="highlight">关注+评论[名单]领取</span>
                </div>
            </div>
            <div class="divider"></div>
            <div class="disclaimer-box">
                <div class="disclaimer-title">免责声明</div>
                <div class="disclaimer-text">
                    数据均由特定指标生成<br>
                    不代表真实涨跌幅<br>
                    本内容不构成投资建议<br>
                    市场有风险 · 投资需谨慎
                </div>
            </div>
        </body></html>
        """
        await page.set_content(outro_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="outro.png")

        print("🌐 正在使用快捷键原生下载 TV 图表...")
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf["code"])
            tv_int = "W" if TV_INTERVAL == "1W" else TV_INTERVAL
            await page.goto(f"{TV_CHART_URL.rstrip('/')}/?symbol={symbol}&interval={tv_int}", wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            await page.wait_for_timeout(1500)

            img_output = f"ss_etf_{i}.png"
            try:
                async with page.expect_download(timeout=15000) as download_info:
                    await page.keyboard.press("Control+Alt+s")
                download = await download_info.value
                await download.save_as(img_output)
                print(f"  ✓ 成功下载 ETF_{i} 原始图表")
            except Exception as e:
                print(f"  ⚠️ 原生下载失败，触发后备截图方案: {e}")
                await page.screenshot(path=img_output)

            _prepare_chart_image_file(img_output)
            add_watermark_to_chart(img_output, f"{etf['name']} {etf['code']}")

        await browser.close()

    print("🎵 正在生成字幕、合成配音与视频序列...")
    video_segments = []
    audio_segments = []

    active_intro = clean_for_tts(ai_script.get("video_intro", ""))
    await safe_generate_tts(active_intro, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    create_srt(active_intro, dur_intro, "sub_intro.srt")
    create_static_video("cover_image.png", "seg_cover.mp4", dur_intro, srt_file="sub_intro.srt")
    video_segments.append("seg_cover.mp4")
    audio_segments.append("audio_intro.mp3")

    ai_narratives = ai_script.get("etf_narratives", [])
    for i, etf in enumerate(etf_list):
        if i < len(ai_narratives):
            etf_text = clean_for_tts(ai_narratives[i])
        else:
            etf_text = f"最后看{etf['name']}的客观走势，{format_quant_voice(etf['change'])}。"

        audio_name = f"seg_audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, audio_name)
        dur_etf = get_audio_duration(audio_name)
        audio_segments.append(audio_name)

        srt_name = f"sub_etf_{i}.srt"
        create_srt(etf_text, dur_etf, srt_name)

        video_name = f"seg_video_etf_{i}.mp4"
        create_zoom_video(f"ss_etf_{i}.png", video_name, dur_etf, zoom_type="tv", srt_file=srt_name)
        video_segments.append(video_name)

    await safe_generate_tts(OUTRO_TTS_TEXT, "seg_audio_outro.mp3")
    dur_outro = get_audio_duration("seg_audio_outro.mp3")
    create_srt(OUTRO_TTS_TEXT, dur_outro, "sub_outro.srt")
    audio_segments.append("seg_audio_outro.mp3")
    create_static_video("outro.png", "seg_video_outro.mp4", dur_outro, srt_file="sub_outro.srt")
    video_segments.append("seg_video_outro.mp4")

    print("🎬 正在无缝拼装带字幕的宽屏音视频序列...")
    with open("list_v.txt", "w") as f:
        f.writelines([f"file '{v}'\n" for v in video_segments])
    with open("list_a.txt", "w") as f:
        f.writelines([f"file '{a}'\n" for a in audio_segments])

    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_v.txt", "-c:v", "copy", "temp_v.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_a.txt", "-c:a", "copy", "temp_a.mp3"], check=True)

    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    mux_final_video("temp_v.mp4", "temp_a.mp3", "bgm.mp3", final_video)

    for tmp in ["temp_v.mp4", "temp_a.mp3"] + video_segments:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("✈️ 正在推送到 Telegram 接收端...")
    tg_msg = (
        f"📝 【小红书版】\n💡 {ai_script.get('xhs_title', '')}\n\n{ai_script.get('xhs_article', '')}\n\n"
        f"====================\n\n"
        f"📝 【微信公众号版】\n💡 {ai_script.get('gzh_title', '')}\n\n{ai_script.get('gzh_article', '')}\n\n"
        f"--- 🎬 视频文案备份 ---\n{ai_script.get('video_intro', '')}"
    )

    img_list = ["cover_image.png", "outro.png"] + [f"ss_etf_{i}.png" for i in range(len(etf_list))]
    send_telegram(tg_msg, video_path=final_video, photos=img_list)
    print("✅ 全部工作流执行完毕！")


if __name__ == "__main__":
    asyncio.run(main())
