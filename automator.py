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
TIME_LABEL = "上午盘" if IS_MIDDAY else "收盘"
DATE_STR = NOW.strftime("%m月%d日")

# 动态生成封面标题
COVER_TITLE = "ETF异动前四数据"
COVER_SUBTITLE = f"({DATE_STR}-{TIME_LABEL})" if IS_MIDDAY else f"({DATE_STR})"

FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")
OUTRO_TEXT = "本内容不构成投资建议。"
PRIVATE_HOOK = "需要全天候量化异动监控名单，请在评论区留言，带你进内部交流群。" # 👈 终极私域钩子

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
# 🔥 自动字幕生成器 (SRT格式)
# ==========================================
def create_srt(text, duration, filename):
    def format_time(seconds):
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        ms = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    
    start_time = "00:00:00,000"
    end_time = format_time(duration)
    
    # 智能断句，每行最多18个中文字符
    clean_text = text.replace(' E T F ', 'ETF').replace(' A T R ', 'ATR')
    lines = []
    max_len = 18
    for i in range(0, len(clean_text), max_len):
        lines.append(clean_text[i:i+max_len])
    
    srt_content = f"1\n{start_time} --> {end_time}\n" + "\n".join(lines) + "\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(srt_content)

# ==========================================
# 🔥 PIL 物理运镜渲染引擎 (带原生字幕烧录)
# ==========================================
def create_zoom_video(img_path, output_video, duration, fps=30, zoom_type='main', srt_file=None):
    frames_dir = f"temp_frames_{os.path.basename(img_path).split('.')[0]}"
    if os.path.exists(frames_dir): shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    img = Image.open(img_path).convert('RGB')
    w, h = img.size 
    total_frames = int(duration * fps)

    for i in range(total_frames):
        if zoom_type == 'main':
            if i <= 60:
                progress = i / 60.0
                ease = 1 - (1 - progress)**3
                zoom = 1.0 + ease * 1.0 
                cw, ch = int(w/zoom), int(h/zoom)
                cx = 0 
                cy = 0 
            else:
                zoom = 2.0
                cw, ch = int(w/2.0), int(h/2.0)
                cx = 0
                pan_progress = (i - 60) / (total_frames - 60)
                cy = int((h - ch) * pan_progress)
        else:
            # TV图表运镜：向左拉近放大
            progress = i / total_frames
            zoom = 1.0 + 0.15 * progress
            cw, ch = int(w/zoom), int(h/zoom)
            cx = w - cw
            cy = int((h - ch) / 2)

        box = (cx, cy, cx + cw, cy + ch)
        frame = img.crop(box).resize((w, h), Image.Resampling.LANCZOS)
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=90)

    vf_filters = []
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace('\\', '\\\\').replace(':', '\\:')
        # 🚨 字幕修改：添加 FontName=Microsoft YaHei，字号改为 14，细边框
        vf_filters.append(f"subtitles={srt_path}:force_style='FontName=Microsoft YaHei,FontSize=14,PrimaryColour=&H00FFFFFF,Outline=2,OutlineColour=&H00000000,MarginV=30'")

    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps), "-i", f"{frames_dir}/frame_%04d.jpg"
    ]
    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), output_video])
    
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(frames_dir)

def create_static_video(img_path, output_video, duration, fps=30, srt_file=None):
    vf_filters = []
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace('\\', '\\\\').replace(':', '\\:')
        # 🚨 字幕修改：添加 FontName=Microsoft YaHei，字号改为 14，细边框
        vf_filters.append(f"subtitles={srt_path}:force_style='FontName=Microsoft YaHei,FontSize=14,PrimaryColour=&H00FFFFFF,Outline=2,OutlineColour=&H00000000,MarginV=30'")

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration)
    ]
    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p", output_video])
    
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==========================================
# 🔥 核心：AI 智能中枢 
# ==========================================
def call_ai_director(etf_list, time_label):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！")
        sys.exit(1)

    prompt = f"""
    你现在是顶级的A股量化交易专家兼自媒体视觉设计师。
    
    【今日核心触发数据（绝对事实，不可篡改）】：
    {json.dumps(etf_list, ensure_ascii=False, indent=2)}

    🚨 【最高指令：极简客观与绝对服从】 🚨
    1. 你的分析必须 100% 依赖上方 JSON 里的 ETF 名称和 `change`（ATR异动指标）数值。
    2. 严禁编造均线、MACD等垃圾指标，严禁瞎猜走势。语言精炼，少即是多！

    【输出要求】：必须返回合法的 JSON，精确包含以下 5 个字段：
    - "video_intro": 视频开场白。只需1到2句（20-30字），极简概括。英文写 E T F、A T R。
    - "etf_narratives": 🚨 必须是一个【纯字符串数组】。包含4句短评，分别严格对应传入的4只ETF！只需客观讲解数据。
    - "social_title": 爆款推文标题（20字内，带emoji）。
    - "social_body": 排版精美的推文正文。多用emoji，客观复盘。
    - "cover_html": 这是一段完整的 HTML5+CSS 代码字符串。
         * 尺寸：1920x1080。
         * 内容：标题固定为【{COVER_TITLE}】，副标题固定为【{COVER_SUBTITLE}】。下面展示 4 只 ETF 的名称和数据！
         * 审美：使用【明亮、干净、通透】的高级感浅色背景，坚决弃用暗黑系！绝对完美居中对齐。字体使用 Microsoft YaHei。
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
    print(f"🚀 开始执行【4名打榜 + 直飞TV图表 + 黑字水印 + 雅黑字幕】工作流... {NOW}")
    
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

        print("🔍 正在后台提取前4名核心数据指标...")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            data_loaded = False
            for _ in range(20):
                if re.search(r'\b(5\d{5}|1\d{5})\b', await page.evaluate("document.body.innerText")):
                    data_loaded = True
                    break
                await page.wait_for_timeout(1000)
            if not data_loaded: sys.exit(1)
            
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

        print("🎭 正在调度 AI 专家生成明亮封面与解说词...")
        ai_script = call_ai_director(etf_list, TIME_LABEL)
        
        print("🎨 正在渲染 AI 生成的 1080P 明亮宽屏封面...")
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

        print("🌐 正在抓取去边框 TV 图表并注入霸气居中水印...")
        clean_css = ".layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } .layout__area--center { position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; z-index: 9999 !important; transform-origin: top left !important; transform: scale(1.15) !important; }"
        target_interval = "180" if IS_MIDDAY else "1D"
        
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            await page.goto(f"{TV_CHART_URL.rstrip('/')}/?symbol={symbol}&interval={target_interval}", wait_until="domcontentloaded", timeout=60000)
            await page.add_style_tag(content=clean_css)
            
            # 🚨 水印修改：黑色、正常雅黑字体、带淡淡白边光晕防重叠
            overlay_js = f"""
            let overlay = document.createElement('div');
            overlay.innerHTML = '{etf['name']}';
            overlay.style.position = 'fixed';
            overlay.style.top = '12%';
            overlay.style.left = '50%';
            overlay.style.transform = 'translateX(-50%)';
            overlay.style.fontFamily = '"Microsoft YaHei", sans-serif';
            overlay.style.fontSize = '130px';
            overlay.style.fontWeight = 'normal';
            overlay.style.color = '#000000'; // 纯黑
            overlay.style.textShadow = '0 0 15px rgba(255, 255, 255, 0.9)'; // 白色光晕描边，防止被K线挡住看不清
            overlay.style.zIndex = '999999';
            overlay.style.letterSpacing = '10px';
            document.body.appendChild(overlay);
            """
            await page.evaluate(overlay_js)
            await page.evaluate("window.dispatchEvent(new Event('resize'));")
            await page.wait_for_timeout(5000)
            await page.screenshot(path=f"ss_etf_{i}.png")
            
        await browser.close()

    print("🎵 正在生成字幕、合成配音与物理运镜序列...")
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
            
        if i == len(etf_list) - 1:
            etf_text += f" {PRIVATE_HOOK}"
            
        audio_name = f"seg_audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, audio_name)
        dur_etf = get_audio_duration(audio_name)
        audio_segments.append(audio_name)
        
        srt_name = f"sub_etf_{i}.srt"
        create_srt(etf_text, dur_etf, srt_name)
        
        print(f"🎬 正在渲染 {etf['name']} 的呼吸推镜及字幕烧录...")
        video_name = f"seg_video_etf_{i}.mp4"
        create_zoom_video(f"ss_etf_{i}.png", video_name, dur_etf, zoom_type='tv', srt_file=srt_name)
        video_segments.append(video_name)

    await safe_generate_tts(OUTRO_TEXT, "seg_audio_outro.mp3")
    dur_outro = get_audio_duration("seg_audio_outro.mp3")
    create_srt(OUTRO_TEXT, dur_outro, "sub_outro.srt")
    audio_segments.append("seg_audio_outro.mp3")
    
    create_static_video("disclaimer.png", "seg_video_outro.mp4", dur_outro, srt_file="sub_outro.srt")
    video_segments.append("seg_video_outro.mp4")

    print("🎬 正在无缝拼装带字幕的 1080P 音视频序列...")
    with open("list_v.txt", "w") as f: f.writelines([f"file '{v}'\n" for v in video_segments])
    with open("list_a.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_segments])
    
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_v.txt", "-c:v", "copy", "temp_v.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list_a.txt", "-c:a", "copy", "temp_a.mp3"], check=True)

    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    if os.path.exists("bgm.mp3"):
        subprocess.run(["ffmpeg", "-y", "-i", "temp_v.mp4", "-i", "temp_a.mp3", "-stream_loop", "-1", "-i", "bgm.mp3", "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "temp_v.mp4", "-i", "temp_a.mp3", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
        
    for tmp in ["temp_v.mp4", "temp_a.mp3"] + video_segments:
        if os.path.exists(tmp): os.remove(tmp)
        
    print("✈️ 正在推送到 Telegram 接收端...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    
    xhs_text = f"📝 【直飞图表 + 巨屏水印 + 全自动字幕】\n\n💡 {ai_script.get('social_title', 'ETF复盘')}\n\n{ai_script.get('social_body', '')}\n\n--- 🎬 视频文案备份 ---\n{ai_script.get('video_intro', '')}"
    msg_title = ai_script.get('social_title', '量化异动复盘')

    tg_host = "https://" + "api.telegram.org/bot"
    
    try:
        requests.post(f"{tg_host}{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': xhs_text}).raise_for_status()
        with open(final_video, 'rb') as vf:
            requests.post(f"{tg_host}{bot_token}/sendVideo", data={'chat_id': chat_id, 'caption': f"🎬 {msg_title}"}, files={'video': vf}, timeout=120).raise_for_status()

        img_list = ["cover_image.png", "disclaimer.png"] + [f"ss_etf_{i}.png" for i in range(len(etf_list))]
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
