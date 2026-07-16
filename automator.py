import os
import sys
import json
import asyncio
import subprocess
import random
import re
import time
import shutil
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests
from PIL import Image

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/" 
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/" 
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午盘" if IS_MIDDAY else "收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")
OUTRO_TEXT = "本内容不构成投资建议。"

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def format_quant_voice(val_str):
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return f"A T R涨了{abs(val)}%"
        elif val < 0: return f"A T R跌了{abs(val)}%"
        return "A T R在零轴附近"
    except:
        return "无有效读数"

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.strip())

async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            await edge_tts.Communicate(text, "zh-CN-YunxiNeural").save(filename)
            return True
        except Exception as e:
            if attempt < retries - 1: await asyncio.sleep(3) 
            else: raise Exception(f"TTS 失败: {e}")

def clean_for_tts(text):
    if not text: return ""
    if isinstance(text, dict):
        text = "，".join([str(v) for v in text.values() if isinstance(v, str)])
    elif not isinstance(text, str):
        text = str(text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace('*', '').replace('_', '').replace('#', '').replace('`', '')
    text = re.sub(r'(?i)\betf\b', ' E T F ', text)
    text = re.sub(r'(?i)\batr\b', ' A T R ', text)
    text = re.sub(r'(?i)\ba股\b', ' A 股 ', text)
    return text.strip()

# ==========================================
# 🔥 PIL 物理运镜渲染引擎 (彻底告别静止BUG)
# ==========================================
def create_zoom_video(img_path, output_video, duration, fps=30, zoom_type='main'):
    frames_dir = f"temp_frames_{os.path.basename(img_path).split('.')[0]}"
    if os.path.exists(frames_dir): shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    img = Image.open(img_path).convert('RGB')
    w, h = img.size # 预期 1920x1080
    total_frames = int(duration * fps)

    for i in range(total_frames):
        if zoom_type == 'main':
            # 【三段式】：前60帧(2秒)向上方居中平滑放大到2倍，随后极速向下扫视
            if i <= 60:
                progress = i / 60.0
                ease = 1 - (1 - progress)**3 # 平滑缓动曲线
                zoom = 1.0 + ease * 1.0 # 1.0 放大到 2.0
                cw, ch = int(w/zoom), int(h/zoom)
                cx = int((w - cw) / 2) # 水平绝对居中
                cy = 0 # 镜头死死顶住上方表头
            else:
                zoom = 2.0
                cw, ch = int(w/2.0), int(h/2.0)
                cx = int((w - cw) / 2)
                pan_progress = (i - 60) / (total_frames - 60)
                cy = int((h - ch) * pan_progress) # 极速向底部拉动
        else:
            # 【呼吸感】：TV图表缓慢向中心推进 (1.0 放大到 1.1)
            progress = i / total_frames
            zoom = 1.0 + 0.1 * progress
            cw, ch = int(w/zoom), int(h/zoom)
            cx, cy = int((w - cw) / 2), int((h - ch) / 2)

        box = (cx, cy, cx + cw, cy + ch)
        frame = img.crop(box).resize((w, h), Image.Resampling.LANCZOS)
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=90)

    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps), "-i", f"{frames_dir}/frame_%04d.jpg",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), output_video
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(frames_dir)

def create_static_video(img_path, output_video, duration, fps=30):
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration),
        "-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p", output_video
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==========================================
# 🔥 核心：AI 智能中枢 (封杀暗黑，强制居中)
# ==========================================
def call_ai_director(etf_list, time_label):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！请检查 Github Secrets。")
        sys.exit(1)

    prompt = f"""
    你现在是顶级的A股量化交易专家兼爆款自媒体视觉设计师。
    
    【今日核心触发数据（绝对事实，不可篡改）】：
    {json.dumps(etf_list, ensure_ascii=False, indent=2)}

    🚨 【最高指令：极简客观与绝对服从】 🚨
    1. 你的分析必须 100% 依赖上方 JSON 里的 ETF 名称和 `change`（ATR异动指标）数值。
    2. 严禁编造5日均线、MACD等垃圾指标，严禁对未来走势瞎猜。语言必须极其精炼，少即是多！

    【输出要求】：必须返回合法的 JSON，精确包含以下 5 个字段：
    - "video_intro": 视频开场白。只需1到2句（20-30字），极简概括。英文写 E T F、A T R。
    - "etf_narratives": 🚨 必须是一个【纯字符串数组】。针对单只ETF只需两三句客观讲解数据，绝对不瞎猜！
    - "social_title": 爆款推文标题（20字内，带emoji）。
    - "social_body": 排版精美的推文正文。多用emoji，客观复盘。文末引流：想白嫖量化信号，评论区见。
    - "cover_html": 这是一段完整的 HTML5+CSS 代码字符串。
         * 尺寸：适配 1920x1080 电脑宽屏。
         * 🚨 审美死命令：必须使用【明亮、干净、通透】的浅色系背景（如白、浅金、天蓝渐变），坚决弃用暗黑系！
         * 🚨 排版死命令：整个页面的标题、数据卡片、文字【必须绝对完美居中对齐】（使用 display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; width: 100vw;）。
         * 内容：包含【{time_label}量化雷达】大标题，以及并排排列的前三名ETF名称和读数卡片。字体使用 Microsoft YaHei。
    """

    ds_host = "https://" + "api.deepseek.com"
    url = f"{ds_host}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是量化专家兼设计师。必须返回严格居中、背景明亮的HTML封面设计的 JSON。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3 
    }

    last_error = ""
    for attempt in range(3):
        try:
            print(f"🔄 正在呼叫 DeepSeek 极简导演引擎 (第{attempt+1}次尝试)...")
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            raw_text = response.json()['choices'][0]['message']['content']
            clean_text = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"^```\s*", "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text, flags=re.IGNORECASE)
            return json.loads(clean_text)
        except Exception as e:
            last_error = str(e)
            time.sleep(3)

    print(f"❌ 致命错误：DeepSeek API 请求失败: {last_error}")
    sys.exit(1)

async def main():
    print(f"🚀 开始执行【物理引擎运镜 + 宽屏高亮 + 极简干脆】工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}, is_mobile=False,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        
        tv_session = os.getenv('TV_SESSION_ID', '').strip()
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        print("🔍 正在截取 1920x1080 宽屏数据总览...")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            data_loaded = False
            for _ in range(20):
                if re.search(r'\b(5\d{5}|1\d{5})\b', await page.evaluate("document.body.innerText")):
                    data_loaded = True
                    break
                await page.wait_for_timeout(1000)
            if not data_loaded: sys.exit(1)
            await page.wait_for_timeout(2000)
            await page.screenshot(path="ss_main.png")
            
            etf_list = []
            for i in range(await page.locator("tr, .el-table__row, .row, li").count()):
                if len(etf_list) >= 4: break
                text = re.sub(r'[\t\r\n]+', '\n', await page.locator("tr, .el-table__row, .row, li").nth(i).inner_text())
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                code = next((re.search(r'\b(5\d{5}|1\d{5})\b', l).group(1) for l in lines if re.search(r'\b(5\d{5}|1\d{5})\b', l)), None)
                if code:
                    name = re.sub(r'\(.*?\)|>', '', lines[0]).strip()
                    pcts = [val for val in lines if '%' in val]
                    if pcts:
                        change = pcts[1] if not IS_MIDDAY and len(pcts) >= 2 else pcts[0]
                        if not any(e['code'] == code for e in etf_list):
                            etf_list.append({"name": name, "code": code, "change": change})
        except Exception as e:
            sys.exit(1)

        print("🎭 正在调度 AI 专家生成明亮居中封面与解说词...")
        ai_script = call_ai_director(etf_list, TIME_LABEL)
        global SELECTED_HOOK
        SELECTED_HOOK = ai_script['social_title']
        
        await page.set_content(ai_script.get('cover_html', '<html><body><h1>生成异常</h1></body></html>'))
        await page.wait_for_timeout(2000) 
        await page.screenshot(path="cover_image.png")

        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            html { background: #f8fafc; margin: 0; padding: 0; overflow: hidden; width: 100vw; height: 100vh; }
            body { 
                background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
                display: flex; flex-direction: column; justify-content: center; align-items: center; 
                font-family: 'Microsoft YaHei', sans-serif; color: #1e293b; text-align: center; height: 100vh; margin: 0;
            }
            h1 { color: #0f172a; font-size: 80px; margin-bottom: 50px; font-weight: 900; letter-spacing: 10px;}
            p { font-size: 45px; line-height: 2; font-weight: bold; color: #334155; }
            .footer { margin-top: 80px; font-size: 35px; color: #94a3b8; border-top: 2px solid #cbd5e1; padding-top: 40px; width: 60%;}
        </style></head><body>
            <h1>免责声明</h1>
            <p>本视频内所有数据、图表及指标读数<br>均基于特定量化模型客观记录生成<br><br>不代表标的真实涨跌幅<br>亦不构成任何买卖及投资建议</p>
            <div class="footer">市场有风险 · 投资需谨慎</div>
        </body></html>
        """
        await page.set_content(disclaimer_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="disclaimer.png")

        print("🌐 正在抓取 115% 缩放去边框 TV 图表...")
        clean_css = ".layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } .layout__area--center { position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; z-index: 9999 !important; transform-origin: top left !important; transform: scale(1.15) !important; }"
        target_interval = "180" if IS_MIDDAY else "1D"
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            await page.goto(f"{TV_CHART_URL.rstrip('/')}/?symbol={symbol}&interval={target_interval}", wait_until="domcontentloaded", timeout=60000)
            await page.add_style_tag(content=clean_css)
            await page.evaluate("window.dispatchEvent(new Event('resize'));")
            await page.wait_for_timeout(5000)
            await page.screenshot(path=f"ss_etf_{i}.png")
        await browser.close()

    print("🎵 正在合成配音与物理运镜序列...")
    video_segments = []
    audio_segments = []

    create_static_video("cover_image.png", "seg_cover.mp4", 1.5)
    video_segments.append("seg_cover.mp4")
    
    create_static_video("ss_main.png", "seg_main_static.mp4", 1.0)
    video_segments.append("seg_main_static.mp4")

    active_intro = clean_for_tts(ai_script['video_intro'])
    await safe_generate_tts(active_intro, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    
    remain_zoom_time = max(8.0, dur_intro) - 2.5
    if dur_intro < max(8.0, dur_intro):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(max(8.0, dur_intro) - dur_intro), "intro_pad.mp3"])
        with open("i_audio.txt", "w") as f: f.write("file 'audio_intro.mp3'\nfile 'intro_pad.mp3'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "i_audio.txt", "-c", "copy", "seg_audio_intro.mp3"])
    else:
        shutil.copy("audio_intro.mp3", "seg_audio_intro.mp3")
    audio_segments.append("seg_audio_intro.mp3")

    print("🎬 正在使用物理引擎渲染大盘运镜...")
    create_zoom_video("ss_main.png", "seg_main_zoom.mp4", remain_zoom_time, zoom_type='main')
    video_segments.append("seg_main_zoom.mp4")

    for i, etf in enumerate(etf_list):
        etf_text = clean_for_tts(ai_script['etf_narratives'][i]) if i < len(ai_script['etf_narratives']) else f"来看{etf['name']}走势。"
        audio_name = f"seg_audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, audio_name)
        await asyncio.sleep(1)
        dur_etf = get_audio_duration(audio_name)
        audio_segments.append(audio_name)
        
        print(f"🎬 正在使用物理引擎渲染 TV 图表 {etf['name']} 的呼吸推镜...")
        video_name = f"seg_video_etf_{i}.mp4"
        create_zoom_video(f"ss_etf_{i}.png", video_name, dur_etf, zoom_type='tv')
        video_segments.append(video_name)

    await safe_generate_tts(OUTRO_TEXT, "seg_audio_outro.mp3")
    dur_outro = get_audio_duration("seg_audio_outro.mp3")
    audio_segments.append("seg_audio_outro.mp3")
    create_static_video("disclaimer.png", "seg_video_outro.mp4", dur_outro)
    video_segments.append("seg_video_outro.mp4")

    print("🎬 正在无缝拼装 1080P 音视频序列...")
    with open("list_v.txt", "w") as f: f.writelines([f"file '{v}'\n" for v in video_segments])
    with open("list_a.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_segments])
    
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_v.txt", "-c:v", "copy", "temp_v.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_a.txt", "-c:a", "copy", "temp_a.mp3"], check=True)

    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    if os.path.exists("bgm.mp3"):
        subprocess.run(["ffmpeg", "-y", "-i", "temp_v.mp4", "-i", "temp_a.mp3", "-stream_loop", "-1", "-i", "bgm.mp3", "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "temp_v.mp4", "-i", "temp_a.mp3", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
        
    print("✈️ 正在推送到 Telegram 接收端...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    xhs_text = f"📝 【明亮高燃运镜版】\n\n💡 {ai_script['social_title']}\n\n{ai_script['social_body']}\n\n--- 🎬 视频文案备份 ---\n{ai_script['video_intro']}"
    
    # 💡 终极物理防粘补丁：把 Telegram 的网址也彻底切断！
    tg_host = "https://" + "api.telegram.org/bot"
    
    try:
        requests.post(f"{tg_host}{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': xhs_text}).raise_for_status()
        with open(final_video, 'rb') as vf:
            requests.post(f"{tg_host}{bot_token}/sendVideo", data={'chat_id': chat_id, 'caption': f"🎬 {ai_script['social_title']}"}, files={'video': vf}, timeout=120).raise_for_status()

        img_list = ["cover_image.png", "ss_main.png", "disclaimer.png"] + [f"ss_etf_{i}.png" for i in range(len(etf_list))]
        for i in range(0, len(img_list), 10):
            chunk, media_group, files = img_list[i:i+10], [], {}
            for idx, img in enumerate(chunk):
                if os.path.exists(img):
                    files[f"f{idx}"] = open(img, "rb")
                    media_group.append({"type": "photo", "media": f"attach://f{idx}"})
            requests.post(f"{tg_host}{bot_token}/sendMediaGroup", data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60).raise_for_status()
            for f in files.values(): f.close()
    except Exception as e:
        print(f"🛑 推送至 Telegram 失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
