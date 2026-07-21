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
from playwright.async_api import async_playwright
import edge_tts
import requests
from PIL import Image, ImageOps, ImageDraw, ImageFont 

# ==========================================
# 1. 基础配置
# ==========================================
TARGET_URL = "https://gpkx.github.io/" 
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/" 
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)

TODAY_WEEKDAY = NOW.weekday()  
IS_SATURDAY = TODAY_WEEKDAY == 5

if IS_SATURDAY:
    TIME_LABEL = "周线收盘"
    REPORT_TYPE = "weekly"
    TV_INTERVAL = "1W"
    TARGET_COL_IDX = -1 
else:
    TIME_LABEL = "日线收盘"
    REPORT_TYPE = "daily"
    TV_INTERVAL = "1D"
    TARGET_COL_IDX = TODAY_WEEKDAY + 1  

DATE_STR = NOW.strftime("%m月%d日")
COVER_TITLE = "本周ETF异动Top4" if IS_SATURDAY else "今日ETF异动Top4"
COVER_SUBTITLE = f"({DATE_STR}-{TIME_LABEL})"

FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

PRIVATE_HOOK = "关注我，每日更新异动ETF，欢迎评论！" 
OUTRO_TEXT = "本内容不构成投资建议;市场有风险，投资需谨慎。"

# 视频统一规格（手机竖屏模式）
VIDEO_W, VIDEO_H = 1080, 1920
VIDEO_FPS = 30

# 由宽高自动推导显示比例（竖屏 1080:1920 → 9:16），避免硬编码导致预览比例失真
import math as _math
def _reduce_ratio(w, h):
    g = _math.gcd(w, h)
    return f"{w // g}:{h // g}"
VIDEO_ASPECT = _reduce_ratio(VIDEO_W, VIDEO_H)

# 字幕字号与每行字数按视频尺寸缩放，避免竖屏溢出。
# 参考基准：横屏 1920x1080 下 FontSize=12、每行40字 刚好不溢出。
# 字幕实际渲染大小 ∝ FontSize × 视频高度，故字号按高度反比缩放；每行字数按宽度缩放。
_SUB_FONT_SIZE = max(6, round(12 * (1080 / VIDEO_H)))
_SUB_MAX_CHARS = max(10, int(40 * (VIDEO_W / 1920)))

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def parse_pct_to_float(val_str):
    try:
        return float(val_str.replace('%', '').replace('+', ''))
    except:
        return 0.0

def _resolve_col_date(day):
    """根据表头里的“日”（如 20），解析出它所属的 YYYY-MM-DD。
    在今天前后 ±7 天范围内查找 day-of-month 匹配的日期，自动处理跨月。"""
    for delta in range(-7, 8):
        cand = NOW + timedelta(days=delta)
        if cand.day == day:
            return cand.date().isoformat()
    return None

def format_quant_voice(val_str):
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return f"ATR拉升了{abs(val)}%"
        elif val < 0: return f"ATR回撤了{abs(val)}%"
        return "ATR 处于零轴震荡区"
    except:
        return "暂无有效读数"

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.strip())

async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+5%")
            await communicate.save(filename)
            return True
        except Exception as e:
            if attempt < retries - 1: await asyncio.sleep(3) 
            else: raise Exception(f"TTS 失败: {e}")

def clean_for_tts(text):
    if not text: return ""
    if isinstance(text, dict): text = "，".join([str(v) for v in text.values() if isinstance(v, str)])
    elif not isinstance(text, str): text = str(text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace('*', '').replace('_', '').replace('#', '').replace('`', '')
    text = re.sub(r'(?i)\betf\b', ' ETF ', text)
    text = re.sub(r'(?i)\batr\b', ' ATR ', text)
    text = re.sub(r'(?i)\ba股\b', ' A股 ', text)
    return text.strip()

def create_srt(text, duration, filename):
    def format_time(seconds):
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        ms = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    
    start_time = "00:00:00,000"
    end_time = format_time(duration)
    clean_text = text.replace(' ETF ', 'ETF').replace(' ATR ', 'ATR')
    
    max_chars_per_line = _SUB_MAX_CHARS  # 按视频宽度缩放，避免竖屏每行字数过多溢出
    lines = [clean_text[i:i+max_chars_per_line] for i in range(0, len(clean_text), max_chars_per_line)]
    text_block = "\n".join(lines)
    
    srt_content = f"1\n{start_time} --> {end_time}\n{text_block}\n"
    with open(filename, "w", encoding="utf-8") as f: f.write(srt_content)

def get_subtitle_filter(srt_file):
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace('\\', '\\\\').replace(':', '\\:')
        return f"subtitles={srt_path}:force_style='FontName=Alibaba PuHuiTi,FontSize={_SUB_FONT_SIZE},PrimaryColour=&H00000000,Outline=0,Shadow=0,MarginV=30,Alignment=2'"
    return ""

# 统一的视频元数据滤镜：强制 1920x1080 / SAR 1:1，避免 Telegram 预览图比例与封面不一致
def _sar_filter():
    return f"scale={VIDEO_W}:{VIDEO_H},setsar=1/1"

# ------------------------------------------
# 图像变换工具
# ------------------------------------------
def _fit_to_canvas(src, size=(VIDEO_W, VIDEO_H), color=(255, 255, 255)):
    """等比放入 1080x1920 竖屏画布（letterbox），保证所有图表比例统一。"""
    return ImageOps.pad(src.convert('RGB'), size, method=Image.Resampling.LANCZOS, color=color)

def _prepare_chart_image_file(img_path):
    """预处理图表：等比放入 1080x1920 竖屏画布，原地覆盖保存。"""
    src = Image.open(img_path).convert('RGB')
    _fit_to_canvas(src, (VIDEO_W, VIDEO_H)).save(img_path)

def create_zoom_video(img_path, output_video, duration, fps=VIDEO_FPS, zoom_type='main', srt_file=None):
    frames_dir = f"temp_frames_{os.path.basename(img_path).split('.')[0]}"
    if os.path.exists(frames_dir): shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    src = Image.open(img_path).convert('RGB')
    # 图表已在 main 中预处理（竖屏 letterbox + 加水印），这里只做等比放入画布
    base = _fit_to_canvas(src, (VIDEO_W, VIDEO_H))

    W, H = VIDEO_W, VIDEO_H
    total_frames = max(1, int(duration * fps))

    if zoom_type == 'tv':
        # 需求3：拉近放大始终以最新 K 线为中心（TradingView 右侧为价格刻度，最新 K 线约在 0.72 处）
        LATEST_KL_X = 0.72 * W
        LATEST_KL_Y = 0.50 * H
        START_ZOOM = 1.0
        END_ZOOM = 1.3   # 需求3 修订：只放大 30%

    for i in range(total_frames):
        if zoom_type == 'tv':
            t = i / max(total_frames - 1, 1)
            zoom = START_ZOOM + (END_ZOOM - START_ZOOM) * t
            cw = max(1, int(W / zoom))
            ch = max(1, int(H / zoom))
            # 以最新 K 线为中心
            cx = int(LATEST_KL_X - cw / 2)
            cy = int(LATEST_KL_Y - ch / 2)
            # 边界约束，避免越界
            cx = max(0, min(cx, W - cw))
            cy = max(0, min(cy, H - ch))
        else:
            cx, cy, cw, ch = 0, 0, W, H

        box = (cx, cy, cx + cw, cy + ch)
        frame = base.crop(box).resize((W, H), Image.Resampling.LANCZOS)
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=92)

    # 强制帧序列像素为 1920x1080、SAR 1:1
    vf_filters = [_sar_filter()]
    sub_filter = get_subtitle_filter(srt_file)
    if sub_filter: vf_filters.append(sub_filter)

    cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", f"{frames_dir}/frame_%04d.jpg"]
    if vf_filters: cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(frames_dir)

def create_static_video(img_path, output_video, duration, fps=VIDEO_FPS, srt_file=None):
    vf_filters = [f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
                  f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2,setsar=1/1"]
    sub_filter = get_subtitle_filter(srt_file)
    if sub_filter: vf_filters.append(sub_filter)

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration)]
    cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p", output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _load_cjk_font(size):
    """依次尝试 Windows / macOS / Linux 常见中文字体，避免掉到默认小字体。"""
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
    """
    需求2 修订：水印缩小到原来的 1/4，不要描边，黑色字体，透明度 50%。
    """
    try:
        img = Image.open(img_path).convert("RGBA")
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)

        target_w = img.width * 0.3   # 约为原来的 1/4（原 88% → 30%）
        max_h = img.height * 0.06      # 高度同步缩到约 1/4

        # 二分查找最大可用字号（同时受宽度与高度约束）
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

        font = _load_cjk_font(best)
        if font is None:
            font = ImageFont.load_default()

        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        x = (img.width - text_w) / 2
        y = int(img.height * 0.13)   # 顶部偏下，避开 30% 拉近裁切区域

        # 黑色字体 + 50% 透明度（alpha=128），不加描边
        draw.text((x, y), text, font=font, fill=(0, 0, 0, 128))

        out = Image.alpha_composite(img, txt_layer).convert("RGB")
        out.save(img_path)
    except Exception as e:
        print(f"  ⚠️ 图表添加水印失败: {e}")

# ==========================================
# 2. AI 中枢逻辑 
# ==========================================
def call_ai_director(etf_list, time_label, report_type):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！")
        sys.exit(1)

    url = "https://api.deepseek.com/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    if report_type == "weekly":
        prompt_context = f"今天【{DATE_STR}】是周末。请对本周主力资金发生异动的前四名 ETF 进行周线级别的大局观分析，做深度复盘。不要局限于一天，要聊这周资金的整体倾向。"
    else:
        prompt_context = f"今天【{DATE_STR}{time_label}】有以下 ETF 触发主力资金异动。请做精准的短线点评。"

    if not etf_list:
        prompt = f"""
        你现在是一位有10年实战经验、语气亲和且专业的A股资深数据分析专家。{prompt_context}
        但是今天没有任何ETF触发我们的异动阈值。请生成通报文章，强调交易纪律：“宁缺毋滥”。
        【输出要求】返回 JSON：
        - "xhs_title": 小红书爆款标题
        - "xhs_article": 小红书正文
        - "gzh_title": 微信公众号标题
        - "gzh_article": 微信公众号正文
        """
    else:
        prompt = f"""
        你现在是一位有10年实战经验、语气亲和且专业的A股资深数据分析专家。{prompt_context}
        
        【核心异动数据（已按涨跌幅降序提取前四名）】：
        {json.dumps(etf_list, ensure_ascii=False, indent=2)}

        🚨 【最高指令】客观真实，只基于上方数据解读，不说假话！

        【输出要求】严格返回JSON，包含：
        - "video_intro": 短视频开场口播（20-30字，打招呼+抛出结论）。🚨极端重要：必须全部使用纯中文生成！绝对禁止出现整段英文！仅在提到ETF这三个字时，写成大写的 ETF。
        - "etf_narratives": 【数组】包含{len(etf_list)}句短评，严格对应传入的ETF！纯中文口语化解说。
        - "xhs_title": 小红书爆款标题。
        - "xhs_article": 小红书正文。
        - "gzh_title": 公众号标题。
        - "gzh_article": 公众号正文。
        - "cover_html": HTML5+CSS。1080x1920（手机竖屏）。要求：现代金融风，浅色高级渐变背景，适配竖屏布局（内容纵向排列）。标题【{COVER_TITLE}】，副标题【{COVER_SUBTITLE}】。🚨必须在副标题下方，用醒目、美观的卡片或列表，把以上 4只ETF 的名称和涨跌幅数据排版渲染出来！涨跌幅染色规则（A股惯例，必须严格遵守）：上涨（正数、带+号）必须用红色字体，下跌（负数、带-号）必须用绿色字体；请根据每只 ETF 涨跌幅的正负号逐一染色。所有字体设置为 'Alibaba PuHuiTi', 'Microsoft YaHei', sans-serif。居中对齐。
        """

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是资深数据专家兼矩阵运营主编。严格返回 JSON。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4 
    }

    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            raw_text = response.json()['choices'][0]['message']['content']
            clean_text = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw_text, flags=re.IGNORECASE)
            return json.loads(clean_text.strip())
        except Exception as e:
            time.sleep(3)
    sys.exit(1)

def send_telegram(text, video_path=None, photos=None):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not bot_token or not chat_id:
        return
        
    tg_host = "https://api.telegram.org/bot"
    try:
        requests.post(f"{tg_host}{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': text}).raise_for_status()
        if video_path and os.path.exists(video_path):
            with open(video_path, 'rb') as vf:
                requests.post(f"{tg_host}{bot_token}/sendVideo", data={'chat_id': chat_id}, files={'video': vf}, timeout=120).raise_for_status()
        if photos:
            for i in range(0, len(photos), 10):
                chunk, media_group, files = photos[i:i+10], [], {}
                for idx, img in enumerate(chunk):
                    if os.path.exists(img):
                        files[f"f{idx}"] = open(img, "rb")
                        media_group.append({"type": "photo", "media": f"attach://f{idx}"})
                if media_group:
                    requests.post(f"{tg_host}{bot_token}/sendMediaGroup", data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60).raise_for_status()
                for f in files.values(): f.close()
    except Exception as e:
        print(f"🛑 推送至 Telegram 失败: {e}")
        sys.exit(1)

# ==========================================
# 3. 最终合成：统一重编码，修复 Telegram 预览比例问题
# ==========================================
def mux_final_video(temp_v, temp_a, bgm_path, final_video):
    """
    需求1：生成的视频默认宽度与封面宽度不一致（Telegram 预览比例失真）。
    根因：concat 用 -c:v copy 会原样保留各片段的 SAR/DAR 元数据。
    修复：最终统一重编码，强制 scale={VIDEO_W}x{VIDEO_H}, setsar=1/1, aspect={VIDEO_ASPECT},
          pix_fmt=yuv420p, +faststart，保证预览与封面完全一致。
    """
    common_v_enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS),
                    "-aspect", VIDEO_ASPECT, "-movflags", "+faststart"]
    audio_enc = ["-c:a", "aac", "-b:a", "192k", "-shortest"]

    if bgm_path and os.path.exists(bgm_path):
        cmd = [
            "ffmpeg", "-y",
            "-i", temp_v,
            "-i", temp_a,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            (f"[0:v]scale={VIDEO_W}:{VIDEO_H},setsar=1/1[v];"
             "[1:a]volume=2.0[a1];[2:a]volume=0.15[a2];"
             "[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"),
            "-map", "[v]", "-map", "[a]",
        ] + common_v_enc + audio_enc + [final_video]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", temp_v,
            "-i", temp_a,
            "-vf", f"scale={VIDEO_W}:{VIDEO_H},setsar=1/1",
            "-map", "0:v:0", "-map", "1:a:0",
        ] + common_v_enc + audio_enc + [final_video]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def main():
    print(f"🚀 启动自媒体矩阵引擎 | 当前模式: {REPORT_TYPE} | {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': VIDEO_W, 'height': VIDEO_H}, accept_downloads=True)
        
        tv_session = os.getenv('TV_SESSION_ID', '').strip()
        if tv_session:
            print("🔑 检测到 TV_SESSION_ID，正在注入授权 Cookie...")
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])

        page = await context.new_page()

        print("🔍 正在提取核心数据...")
        etf_list = []
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            row_data = await page.evaluate('''() => {
                return Array.from(document.querySelectorAll('tr, .el-table__row')).map(tr => {
                    return Array.from(tr.querySelectorAll('td, th')).map(td => td.innerText.trim());
                }).filter(row => row.length >= 7); 
            }''')

            # 解析表头：建立“日列 -> 日期”映射，并识别“周线”列
            header = next((r for r in row_data if any(('周线' in (c or '')) or ('周一' in (c or '')) or ('周日' in (c or '')) for c in r)), [])
            weekly_col_idx = None
            col_dates = {}
            for idx, h in enumerate(header):
                h_str = h or ''
                if '周线' in h_str:
                    weekly_col_idx = idx
                    continue
                m = re.search(r'(\d{1,2})\s*日', h_str)
                if m:
                    col_dates[idx] = _resolve_col_date(int(m.group(1)))

            for row in row_data:
                name_cell = row[0]
                code_match = re.search(r'\b(5\d{5}|1\d{5})\b', name_cell)
                if not code_match:
                    continue
                code = code_match.group(1)
                name = re.sub(r'\d+', '', name_cell).strip()
                target_val = ''
                target_date = None
                if IS_SATURDAY and weekly_col_idx is not None:
                    # 周线模式：取周线列
                    if weekly_col_idx < len(row) and '%' in row[weekly_col_idx]:
                        target_val = row[weekly_col_idx]
                        target_date = None  # 周线用今天(周六)日期，worker fallback
                else:
                    # 日线模式：只在“日列”里从右往左找最近有数据的列（排除周线列）。
                    # 这样今天还没开盘时自动取最近交易日数据，且不会把周线误当作日线写进今天。
                    for idx in range(len(row) - 1, 0, -1):
                        if idx == weekly_col_idx:
                            continue
                        cell = row[idx] if idx < len(row) else ''
                        if '%' in cell:
                            target_val = cell
                            target_date = col_dates.get(idx)
                            break
                if target_val:
                    etf_list.append({"name": name, "code": code, "change": target_val, "data_date": target_date})
            
            etf_list.sort(key=lambda x: abs(parse_pct_to_float(x['change'])), reverse=True)
            
            if etf_list:
                cf_url = os.getenv("CF_WORKER_URL", "").strip()
                cf_token = os.getenv("CF_API_TOKEN", "").strip()
                
                if cf_url and cf_token:
                    cf_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cf_token}"}
                    # 若今天列空（未开盘）、用的是旧交易日的数据，先清掉今天可能残留的脏数据，
                    # 避免网页把旧数据当成“今天”展示。
                    if not IS_SATURDAY and etf_list:
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
                        # 关键：用数据所属的真实日期入库，避免把旧数据写成“今天”
                        if etf.get("data_date"):
                            item["date"] = etf["data_date"]
                        if IS_SATURDAY: item["week_status"] = etf["change"]
                        else: item["day_status"] = etf["change"]
                        data_list.append(item)
                    
                    try:
                        cf_response = requests.post(cf_url, json=data_list, headers=cf_headers, timeout=30)
                        print(f"☁️ Cloudflare 同步结果: {cf_response.text}")
                    except Exception as e:
                        print(f"⚠️ 同步到 Cloudflare 失败: {e}")

            etf_list = etf_list[:4]
        except Exception as e:
            print(f"提取数据发生异常: {e}")

        if not etf_list:
            ai_script = call_ai_director([], TIME_LABEL, REPORT_TYPE)
            tg_msg = f"📝 【小红书版】\n💡 {ai_script.get('xhs_title', '')}\n\n{ai_script.get('xhs_article', '')}\n\n====================\n\n📝 【微信公众号版】\n💡 {ai_script.get('gzh_title', '')}\n\n{ai_script.get('gzh_article', '')}"
            send_telegram(tg_msg)
            await browser.close()
            sys.exit(0)

        print("🎭 正在生成剧本并渲染封面 (含 Top4 数据)...")
        ai_script = call_ai_director(etf_list, TIME_LABEL, REPORT_TYPE)
        
        await page.set_content(ai_script.get('cover_html', '<html></html>'))
        await page.wait_for_timeout(2000) 
        await page.screenshot(path="cover_image.png")

        hook_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { background: linear-gradient(135deg, #f8fafc, #e2e8f0); display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Alibaba PuHuiTi', 'Microsoft YaHei'; height: 100vh; margin: 0; text-align: center; }
            .card { background: rgba(255, 255, 255, 0.8); padding: 80px 100px; border-radius: 40px; box-shadow: 0 10px 40px rgba(0,0,0,0.05); }
            h1 { color: #1e293b; font-size: 70px; margin-bottom: 50px; font-weight: bold; }
            p { font-size: 45px; line-height: 2.2; color: #475569; }
            .highlight { background: #3b82f6; color: white; padding: 15px 35px; border-radius: 20px; font-weight: bold;}
        </style></head><body>
            <div class="card">
                <h1>获取每日主力监控图表</h1>
                <p>完整主力资金异动数据单<br><br><span class="highlight">欢迎在评论区交流探讨</span><br><br>把握市场核心资金动向</p>
            </div>
        </body></html>
        """
        await page.set_content(hook_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="hook.png")

        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { background: #f8fafc; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Alibaba PuHuiTi', 'Microsoft YaHei'; height: 100vh; margin: 0; padding: 0 40px; text-align: center; border: 30px solid #f1f5f9; box-sizing: border-box; }
            h1 { color: #0f172a; font-size: 75px; margin-bottom: 60px; font-weight: bold; letter-spacing: 10px; }
            p { font-size: 40px; line-height: 2; color: #475569; }
            .footer { margin-top: 80px; font-size: 30px; color: #94a3b8; border-top: 2px solid #e2e8f0; padding-top: 40px; width: 60%; }
        </style></head><body>
            <h1>免责声明</h1>
            <p>本视频内所有数据、图表及指标读数<br>均基于AI大数据模型客观记录生成<br><br>不代表标的真实涨跌幅<br>亦不构成任何投资建议</p>
            <div class="footer">市场有风险 · 投资需谨慎</div>
        </body></html>
        """
        await page.set_content(disclaimer_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="disclaimer.png")

        print("🌐 正在使用快捷键原生下载 TV 图表...")
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            tv_int = "W" if TV_INTERVAL == "1W" else TV_INTERVAL
            await page.goto(f"{TV_CHART_URL.rstrip('/')}/?symbol={symbol}&interval={tv_int}", wait_until="domcontentloaded")
            
            await page.wait_for_timeout(6000)
            # 不再按 Shift+ArrowRight：该键会把图表向右(未来方向)平移，
            # 导致最新 K 线被推向左侧、右侧出现大段空白。让图表以保存布局的默认位置加载，
            # 最新 K 线自然停在最右侧。
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
            
            # 需求4：先裁掉顶部 logo/邮箱那一行，再加水印
            _prepare_chart_image_file(img_output)
            add_watermark_to_chart(img_output, f"{etf['name']} {etf['code']}")
            
        await browser.close()

    print("🎵 正在生成字幕、合成配音与视频序列...")
    video_segments = []
    audio_segments = []

    active_intro = clean_for_tts(ai_script.get('video_intro', ''))
    await safe_generate_tts(active_intro, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    create_srt(active_intro, dur_intro, "sub_intro.srt")
    create_static_video("cover_image.png", "seg_cover.mp4", dur_intro, srt_file="sub_intro.srt")
    video_segments.append("seg_cover.mp4")
    audio_segments.append("audio_intro.mp3")

    ai_narratives = ai_script.get('etf_narratives', [])
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
        create_zoom_video(f"ss_etf_{i}.png", video_name, dur_etf, zoom_type='tv', srt_file=srt_name)
        video_segments.append(video_name)

    await safe_generate_tts(PRIVATE_HOOK, "seg_audio_hook.mp3")
    dur_hook = get_audio_duration("seg_audio_hook.mp3")
    create_srt(PRIVATE_HOOK, dur_hook, "sub_hook.srt")
    audio_segments.append("seg_audio_hook.mp3")
    create_static_video("hook.png", "seg_video_hook.mp4", dur_hook, srt_file="sub_hook.srt")
    video_segments.append("seg_video_hook.mp4")

    await safe_generate_tts(OUTRO_TEXT, "seg_audio_outro.mp3")
    dur_outro = get_audio_duration("seg_audio_outro.mp3")
    create_srt(OUTRO_TEXT, dur_outro, "sub_outro.srt")
    audio_segments.append("seg_audio_outro.mp3")
    create_static_video("disclaimer.png", "seg_video_outro.mp4", dur_outro, srt_file="sub_outro.srt")
    video_segments.append("seg_video_outro.mp4")

    print("🎬 正在无缝拼装带字幕的宽屏音视频序列...")
    with open("list_v.txt", "w") as f: f.writelines([f"file '{v}'\n" for v in video_segments])
    with open("list_a.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_segments])
    
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_v.txt", "-c:v", "copy", "temp_v.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_a.txt", "-c:a", "copy", "temp_a.mp3"], check=True)

    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    mux_final_video("temp_v.mp4", "temp_a.mp3", "bgm.mp3", final_video)
        
    for tmp in ["temp_v.mp4", "temp_a.mp3"] + video_segments:
        if os.path.exists(tmp): os.remove(tmp)
        
    print("✈️ 正在推送到 Telegram 接收端...")
    tg_msg = f"📝 【小红书版】\n💡 {ai_script.get('xhs_title', '')}\n\n{ai_script.get('xhs_article', '')}\n\n====================\n\n📝 【微信公众号版】\n💡 {ai_script.get('gzh_title', '')}\n\n{ai_script.get('gzh_article', '')}\n\n--- 🎬 视频文案备份 ---\n{ai_script.get('video_intro', '')}"
    
    img_list = ["cover_image.png", "hook.png", "disclaimer.png"] + [f"ss_etf_{i}.png" for i in range(len(etf_list))]
    send_telegram(tg_msg, video_path=final_video, photos=img_list)
    print("✅ 全部工作流执行完毕！")

if __name__ == "__main__":
    asyncio.run(main())
