import os
import json
import asyncio
import subprocess
import random
import re
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/"
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/" 
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午盘" if IS_MIDDAY else "收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

# 2. 严谨的量化专属文案模板
INTRO_TEXT = f"各位观众朋友大家好，欢迎关注最新的行业ETF客观数据跟踪。截至{TIME_LABEL}，我们来梳理一下核心板块的量化监控指标最新读数。"
OUTRO_TEXT = "特别提示，以上数值均为特定策略下的 A T R 监控指标，不代表标的实际涨跌幅。数据仅供客观复盘参考，不构成任何投资建议。"

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def format_quant_voice(val_str):
    # 将数值转化为 TTS 语音友好的客观播报格式
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return f"正的百分之{abs(val)}"
        elif val < 0: return f"负的百分之{abs(val)}"
        return "零轴附近"
    except:
        return "无有效读数"

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
           "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

# 引入带重试和缓冲机制的防封 TTS 生成器
async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            await edge_tts.Communicate(text, "zh-CN-YunxiNeural").save(filename)
            return True
        except Exception as e:
            print(f"⚠️ TTS 接口被限流或报错 (尝试 {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(3) # 失败后强行休息 3 秒再试
            else:
                raise Exception(f"TTS 生成彻底失败，请稍后再试: {e}")

async def main():
    print(f"🚀 开始执行【量化严谨版】全自动工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        
        # 注入 Cookie 破解登录墙加载私有指标
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        # --- A. 抓取网页真实数据（正则防弹版） ---
        print("🔍 正在实时抓取今日行情数据...")
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(5000) 
        await page.screenshot(path="ss_main.png")
        
        etf_list = []
        try:
            row_locators = page.locator("tr, .el-table__row, .row, li")
            count = await row_locators.count()
            
            for i in range(count):
                if len(etf_list) >= 4:
                    break
                    
                text = await row_locators.nth(i).inner_text()
                # 将杂乱的 Tab 和空格统一替换为换行符
                text = re.sub(r'[\t\r\n]+', '\n', text)
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
                # 正则匹配 A 股 ETF 6位代码
                code = None
                code_idx = -1
                for idx, line in enumerate(lines):
                    match = re.search(r'\b(5\d{5}|1\d{5})\b', line)
                    if match:
                        code = match.group(1)
                        code_idx = idx
                        break
                
                if code and code_idx >= 0:
                    raw_name = lines[0]
                    name = re.sub(r'\(.*?\)|>', '', raw_name).strip()
                    pcts = [val for val in lines[code_idx+1:] if '%' in val]
                    
                    if pcts:
                        if not IS_MIDDAY and len(pcts) >= 2:
                            change = pcts[1]
                        else:
                            change = pcts[0]
                            
                        if not any(e['code'] == code for e in etf_list):
                            etf_list.append({"name": name, "code": code, "change": change})
            
            if not etf_list:
                print("⚠️ 页面未匹配到符合特征的ETF数据。")
                
        except Exception as e:
            print(f"⚠️ 网页解析异常 ({e})")
            etf_list = []

        # ==========================================
        # 🛑 核心熔断机制：无数据即停止，保证宁缺毋滥
        # ==========================================
        if not etf_list:
            print("🛑 今日无触发数据，中止视频生成，静默汇报...")
            await browser.close()
            
            bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
            chat_id = os.getenv('TELEGRAM_CHAT_ID')
            if bot_token and chat_id:
                msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                msg_text = f"📭 【{TIME_LABEL} 监控报告】\n\n今日网页暂无符合条件的 ETF 触发（或无有效读数）。\n为保证数据严谨性，本次自动短视频及图文暂停生成。"
                requests.post(msg_url, data={'chat_id': chat_id, 'text': msg_text})
                
            return # 优雅结束程序
        # ==========================================

        # --- 👇 数据驱动的量化客观钩子引擎 👇 ---
        global SELECTED_HOOK
        print("🧠 正在根据今日数据生成严谨标题...")
        
        # 找出今天绝对值波动最大的 ETF
        top_etf = etf_list[0]
        max_abs_val = -1
        for e in etf_list:
            try:
                val = abs(float(e['change'].replace('%', '').replace('+', '')))
                if val > max_abs_val:
                    max_abs_val = val
                    top_etf = e
            except:
                pass
        
        action_word = "指标数值达" 
        emoji = "📊" 
        SELECTED_HOOK = f"{emoji} {TIME_LABEL}量化追踪！{top_etf['name']}{action_word}{top_etf['change']}，核心数据客观复盘！"
        print(f"🎯 最终生成钩子标题: {SELECTED_HOOK}")

        # --- 🎨 渲染沉稳的量化风封面图 ---
        print("🎨 正在渲染包含真实数据的量化风封面图...")
        cover_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    margin: 0; padding: 0; width: 1280px; height: 720px;
                    background: linear-gradient(135deg, #121212 0%, #1a2a3a 100%);
                    display: flex; flex-direction: column; justify-content: center; align-items: center;
                    font-family: 'Microsoft YaHei', sans-serif; color: white;
                }}
                .tag {{ background: #0078d7; padding: 12px 35px; border-radius: 50px; font-size: 38px; font-weight: bold; margin-bottom: 35px; letter-spacing: 5px; box-shadow: 0 4px 15px rgba(0,120,215,0.5); }}
                .title {{ font-size: 75px; font-weight: 900; color: #ffcc00; text-shadow: 4px 4px 15px rgba(0,0,0,0.9); text-align: center; line-height: 1.3; width: 90%; }}
                .subtitle {{ font-size: 45px; color: #00e5ff; margin-top: 50px; font-weight: bold; letter-spacing: 2px; }}
            </style>
        </head>
        <body>
            <div class="tag">量化监控速递</div>
            <div class="title">{SELECTED_HOOK.replace('！', '！<br>')}</div>
            <div class="subtitle">👉 数据记录 · 客观呈现 · 杜绝预测 👈</div>
        </body>
        </html>
        """
        await page.set_content(cover_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="cover_image.png")

        # --- 📸 抓取 TV 专属带指标图表 ---
        print("🌐 正在使用账号身份加载私有画板...")
        base_chart_url = TV_CHART_URL.rstrip('/')
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            
            await page.goto(f"{base_chart_url}/?symbol={symbol}&interval=180", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            await page.screenshot(path=f"ss_etf_{i}_3h.png")
            
            await page.goto(f"{base_chart_url}/?symbol={symbol}&interval=1D", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            await page.screenshot(path=f"ss_etf_{i}_1d.png")

        await browser.close()

    # --- B. 语音合成与画面底层时间轴 ---
    print("🎵 开始生成 TTS 语音并构建时间轴 (已开启防限流模式)...")
    image_timeline = []
    audio_files = []
    
    # 替换原有的 edge_tts.Communicate，使用新的 safe_generate_tts
    await safe_generate_tts(INTRO_TEXT, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    image_timeline.append(f"file 'cover_image.png'\nduration {dur_intro:.3f}\n")
    audio_files.append("audio_intro.mp3")
    full_text = INTRO_TEXT + "\n"

    transition_words = ["首先，", "接下来，", "再来看", "最后是"]
    for i, etf in enumerate(etf_list):
        readable_val = format_quant_voice(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}，当前的 A T R 监控数值录得{readable_val}。"
        full_text += etf_text + "\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, etf_audio)
        await asyncio.sleep(1.5) # 💡 核心防封：每次生成完强行停顿 1.5 秒，模拟真人语速，避免触发接口警报
        
        dur_etf = get_audio_duration(etf_audio)
        half_dur = dur_etf / 2.0 
        image_timeline.append(f"file 'ss_etf_{i}_3h.png'\nduration {half_dur:.3f}\n")
        image_timeline.append(f"file 'ss_etf_{i}_1d.png'\nduration {half_dur:.3f}\n")
        audio_files.append(etf_audio)

    await safe_generate_tts(OUTRO_TEXT, "audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'ss_main.png'\nduration {dur_outro:.3f}\n")
    image_timeline.append(f"file 'ss_main.png'\n")
    audio_files.append("audio_outro.mp3")
    full_text += OUTRO_TEXT

    with open("video_input.txt", "w") as f: f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_files])

    # --- C. 电影级底层双轨混流混音 ---
    print("🎬 正在进行终极音画合成...")
    final_video = f"etf_report_sync_{FILE_SUFFIX}.mp4"
    
    if os.path.exists("bgm.mp3"):
        print("🎧 检测到 bgm.mp3，开启双轨自适应混音模式...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", 
            "-f", "concat", "-safe", "0", "-i", "video_input.txt", 
            "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
            "-stream_loop", "-1", "-i", "bgm.mp3", 
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", 
            "-map", "0:v", "-map", "[a]", 
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-b:a", "192k", 
            final_video
        ]
    else:
        print("🔇 未检测到 bgm.mp3，使用无配乐纯净版语音合成...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", 
            "-f", "concat", "-safe", "0", "-i", "video_input.txt", 
            "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-b:a", "192k", 
            final_video
        ]
    
    subprocess.run(ffmpeg_cmd, check=True)
    
    # --- D. 推送 Telegram (视频 + 图文素材包全平台分发) ---
    print("✈️ 开始打包并推送到 Telegram...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    print("1️⃣ 正在发送短视频...")
    video_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    video_caption = f"🎬 【视频素材】\n⭐⭐ {SELECTED_HOOK} ⭐⭐\n\n{full_text}"
    with open(final_video, 'rb') as video_file:
        requests.post(video_url, data={'chat_id': chat_id, 'caption': video_caption}, files={'video': video_file}, timeout=120)

    print("2️⃣ 正在发送图文排版文案...")
    xhs_body = full_text.replace("各位观众朋友大家好，欢迎关注", "🔥 欢迎关注")\
                        .replace("首先，", "🟢 ")\
                        .replace("接下来，", "🟢 ")\
                        .replace("再来看", "🟢 ")\
                        .replace("最后是", "🟢 ")\
                        .replace("特别提示，以上数值均为", "💡 特别提示：以上数值均为")
    
    xhs_text = (
        f"📝 【图文排版素材（直接长按复制）】\n\n"
        f"【标题建议】{SELECTED_HOOK}\n\n"
        f"【正文内容】\n{xhs_body}\n\n"
        f"#ETF #量化投资 #ATR指标 #复盘记录 #A股 #交易策略"
    )
    msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(msg_url, data={'chat_id': chat_id, 'text': xhs_text})

    print("3️⃣ 正在发送高清配图相册...")
    album_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
    img_list = ["cover_image.png", "ss_main.png"]
    for i in range(len(etf_list)):
        img_list.append(f"ss_etf_{i}_3h.png")
        img_list.append(f"ss_etf_{i}_1d.png")
        
    for i in range(0, len(img_list), 10):
        chunk = img_list[i:i+10]
        media_group = []
        files = {}
        for idx, img in enumerate(chunk):
            if os.path.exists(img):
                files[f"file{idx}"] = open(img, "rb")
                media_group.append({"type": "photo", "media": f"attach://file{idx}"})
        
        requests.post(album_url, data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
        
        for f in files.values():
            f.close()
            
    print("🎉 全平台矩阵素材（视频+排版文案+无损套图）已全部推送！")

if __name__ == "__main__":
    asyncio.run(main())
