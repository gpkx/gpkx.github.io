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
TV_CHART_URL = "https://cn.tradingview.com/chart/Umn0unG5/" 
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

# 防封 TTS 生成器
async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            await edge_tts.Communicate(text, "zh-CN-YunxiNeural").save(filename)
            return True
        except Exception as e:
            print(f"⚠️ TTS 限流 (尝试 {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(3) 
            else:
                raise Exception(f"TTS 彻底失败: {e}")

async def main():
    print(f"🚀 开始执行【完美9:16纯净竖屏版】全自动工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # 💻 iPhone 竖屏环境模拟
        context = await browser.new_context(
            viewport={'width': 720, 'height': 1280},
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )
        
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        # --- A. 抓取网页真实数据 ---
        print("🔍 正在实时抓取今日行情数据...")
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(5000) 
        await page.screenshot(path="ss_main.png")
        
        etf_list = []
        try:
            row_locators = page.locator("tr, .el-table__row, .row, li")
            count = await row_locators.count()
            for i in range(count):
                if len(etf_list) >= 4: break
                text = await row_locators.nth(i).inner_text()
                text = re.sub(r'[\t\r\n]+', '\n', text)
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
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
                        change = pcts[1] if not IS_MIDDAY and len(pcts) >= 2 else pcts[0]
                        if not any(e['code'] == code for e in etf_list):
                            etf_list.append({"name": name, "code": code, "change": change})
            if not etf_list: print("⚠️ 页面未匹配到数据。")
        except Exception as e:
            print(f"⚠️ 解析异常 ({e})")
            etf_list = []

        # 🛑 核心熔断机制
        if not etf_list:
            print("🛑 今日无触发数据，静默汇报...")
            await browser.close()
            bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
            chat_id = os.getenv('TELEGRAM_CHAT_ID')
            if bot_token and chat_id:
                requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", 
                              data={'chat_id': chat_id, 'text': f"📭 【{TIME_LABEL} 监控报告】\n\n今日网页暂无符合条件的 ETF 触发。"})
            return 

        # --- 钩子引擎 ---
        global SELECTED_HOOK
        top_etf = etf_list[0]
        max_abs_val = -1
        for e in etf_list:
            try:
                val = abs(float(e['change'].replace('%', '').replace('+', '')))
                if val > max_abs_val:
                    max_abs_val = val
                    top_etf = e
            except: pass
        SELECTED_HOOK = f"📊 {TIME_LABEL}量化追踪！{top_etf['name']}指标数值达{top_etf['change']}，核心数据客观复盘！"

        # --- 🎨 渲染 9:16 封面 ---
        cover_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ margin: 0; padding: 0; width: 720px; height: 1280px; background: linear-gradient(135deg, #121212 0%, #1a2a3a 100%); display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Microsoft YaHei', sans-serif; color: white; }}
                .tag {{ background: #0078d7; padding: 15px 40px; border-radius: 50px; font-size: 32px; font-weight: bold; margin-bottom: 60px; letter-spacing: 5px; box-shadow: 0 4px 15px rgba(0,120,215,0.5); }}
                .title {{ font-size: 85px; font-weight: 900; color: #ffcc00; text-shadow: 4px 4px 15px rgba(0,0,0,0.9); text-align: center; line-height: 1.4; width: 85%; }}
                .subtitle {{ font-size: 35px; color: #00e5ff; margin-top: 80px; font-weight: bold; letter-spacing: 2px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="tag">量化监控速递</div>
            <div class="title">{SELECTED_HOOK.replace('！', '！<br><br>')}</div>
            <div class="subtitle">👉 数据记录 · 客观呈现 · 杜绝预测 👈</div>
        </body>
        </html>
        """
        await page.set_content(cover_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="cover_image.png")

        # ==========================================
        # 📸 抓取 TV 专属带指标图表 (终极纯净全屏版)
        # ==========================================
        print("🌐 正在抓取完美比例纯净 K 线图...")
        base_chart_url = TV_CHART_URL.rstrip('/')
        
        # 💡 魔法 CSS：彻底屏蔽 TV 所有自带的菜单栏、工具栏和右侧列表，只留核心画板
        clean_css = """
            .layout__area--top,
            .layout__area--left,
            .layout__area--right,
            .layout__area--bottom,
            [data-name="widgetbar"],
            #widgetbar,
            .widgetbar-wrap {
                display: none !important;
            }
            .layout__area--center {
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                width: 100vw !important;
                height: 100vh !important;
                z-index: 9999 !important;
            }
        """

        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            
            # --- 3小时线截图 ---
            await page.goto(f"{base_chart_url}/?symbol={symbol}&interval=180", wait_until="domcontentloaded", timeout=60000)
            await page.add_style_tag(content=clean_css)
            # 💡 魔法 JS：强制触发浏览器重绘，让底层 Canvas 重新计算尺寸，完美填满 720x1280 屏幕
            await page.evaluate("window.dispatchEvent(new Event('resize'));")
            await page.wait_for_timeout(6000) # 等待 K 线和指标重新自适应排版完成
            
            # 此时页面上已经没有任何杂质，直接截取整个视口，确保获得 100% 完美的 720x1280 竖屏图片
            await page.screenshot(path=f"ss_etf_{i}_3h.png")
            
            # --- 日线截图 ---
            await page.goto(f"{base_chart_url}/?symbol={symbol}&interval=1D", wait_until="domcontentloaded", timeout=60000)
            await page.add_style_tag(content=clean_css)
            await page.evaluate("window.dispatchEvent(new Event('resize'));")
            await page.wait_for_timeout(6000)
            
            await page.screenshot(path=f"ss_etf_{i}_1d.png")

        await browser.close()

    # --- B. 语音合成与时间轴 ---
    print("🎵 开始生成 TTS 语音...")
    image_timeline = []
    audio_files = []
    
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
        await asyncio.sleep(1.5)
        
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

    # --- C. 视频合成 ---
    print("🎬 正在进行音画合成...")
    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    
    cmd_base = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_input.txt", 
        "-f", "concat", "-safe", "0", "-i", "audio_input.txt"
    ]
    
    if os.path.exists("bgm.mp3"):
        cmd = cmd_base + [
            "-stream_loop", "-1", "-i", "bgm.mp3", 
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", 
            "-map", "0:v", "-map", "[a]", 
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "192k", final_video
        ]
    else:
        cmd = cmd_base + [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "192k", final_video
        ]
    subprocess.run(cmd, check=True)
    
    # --- D. 发送至 Telegram ---
    print("✈️ 推送全平台素材包...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    video_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    with open(final_video, 'rb') as vf:
        requests.post(video_url, data={'chat_id': chat_id, 'caption': f"🎬 【视频素材】\n⭐⭐ {SELECTED_HOOK} ⭐⭐\n\n{full_text}"}, files={'video': vf}, timeout=120)

    xhs_body = full_text.replace("各位观众朋友大家好，欢迎关注", "🔥 欢迎关注").replace("首先，", "🟢 ").replace("接下来，", "🟢 ").replace("再来看", "🟢 ").replace("最后是", "🟢 ").replace("特别提示，以上数值均为", "💡 特别提示：以上数值均为")
    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': f"📝 【图文素材】\n\n【标题】{SELECTED_HOOK}\n\n【正文】\n{xhs_body}\n\n#ETF #量化投资 #行情复盘"})

    album_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
    img_list = ["cover_image.png", "ss_main.png"]
    for i in range(len(etf_list)):
        img_list.extend([f"ss_etf_{i}_3h.png", f"ss_etf_{i}_1d.png"])
        
    for i in range(0, len(img_list), 10):
        chunk = img_list[i:i+10]
        media_group, files = [], {}
        for idx, img in enumerate(chunk):
            if os.path.exists(img):
                files[f"file{idx}"] = open(img, "rb")
                media_group.append({"type": "photo", "media": f"attach://file{idx}"})
        requests.post(album_url, data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
        for f in files.values(): f.close()

if __name__ == "__main__":
    asyncio.run(main())
