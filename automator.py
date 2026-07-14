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

# 2. 极简播报话术
INTRO_TEXT = f"欢迎关注最新ETF量化跟踪。截至{TIME_LABEL}，来看核心板块最新读数。"
OUTRO_TEXT = "本内容不构成投资建议。"

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

async def main():
    print(f"🚀 开始执行【高完播率动态版】工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 720, 'height': 1280}, is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
        )
        
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        # --- A. 抓取真实数据 ---
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(5000) 
        await page.screenshot(path="ss_main.png")
        
        etf_list = []
        try:
            row_locators = page.locator("tr, .el-table__row, .row, li")
            for i in range(await row_locators.count()):
                if len(etf_list) >= 4: break
                text = re.sub(r'[\t\r\n]+', '\n', await row_locators.nth(i).inner_text())
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
                code = next((re.search(r'\b(5\d{5}|1\d{5})\b', l).group(1) for l in lines if re.search(r'\b(5\d{5}|1\d{5})\b', l)), None)
                if code:
                    name = re.sub(r'\(.*?\)|>', '', lines[0]).strip()
                    pcts = [val for val in lines if '%' in val]
                    if pcts:
                        # 智能取值：下午取全天(索引1)，上午取半天(索引0)
                        change = pcts[1] if not IS_MIDDAY and len(pcts) >= 2 else pcts[0]
                        if not any(e['code'] == code for e in etf_list):
                            etf_list.append({"name": name, "code": code, "change": change})
        except: pass

        if not etf_list:
            print("🛑 无触发数据，停止生成。")
            await browser.close()
            return 

        # --- 🎨 1. 生成【动态前三名】高级封面 ---
        top3_html_blocks = ""
        for i, e in enumerate(etf_list[:3]):
            color = "#ff4d4f" if "+" in e['change'] else "#00e5ff" if "-" in e['change'] else "#ffffff"
            top3_html_blocks += f"<div style='background:rgba(255,255,255,0.1); padding:20px 40px; border-radius:15px; margin:15px 0; display:flex; justify-content:space-between; width:80%; font-size:38px;'><span style='font-weight:bold;'>TOP {i+1} {e['name']}</span><span style='color:{color}; font-weight:900;'>{e['change']}</span></div>"

        cover_html = f"""
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body {{ margin: 0; padding: 0; width: 720px; height: 1280px; background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Microsoft YaHei', sans-serif; color: white; }}
            .tag {{ background: #3b82f6; padding: 12px 35px; border-radius: 50px; font-size: 28px; font-weight: bold; margin-bottom: 50px; letter-spacing: 2px; }}
            .title {{ font-size: 70px; font-weight: 900; color: #fbbf24; text-align: center; margin-bottom: 60px; }}
        </style></head><body>
            <div class="tag">{TIME_LABEL}量化异动榜</div>
            <div class="title">核心数据客观呈现</div>
            {top3_html_blocks}
        </body></html>
        """
        await page.set_content(cover_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="cover_image.png")

        # --- 🎨 2. 生成【片尾文字画】免责声明 ---
        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { margin: 0; padding: 0; width: 720px; height: 1280px; background: #000000; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Microsoft YaHei', sans-serif; color: #888888; text-align: center; padding: 0 50px; box-sizing: border-box;}
            h1 { color: #ffffff; font-size: 45px; margin-bottom: 30px;}
            p { font-size: 28px; line-height: 1.6; }
        </style></head><body>
            <h1>免责声明</h1>
            <p>本视频内所有数据、图表及指标读数，<br>均基于特定量化模型客观记录生成。<br><br>不代表标的真实涨跌幅，<br>亦不构成任何买卖及投资建议。<br><br>市场有风险，投资需谨慎。</p>
        </body></html>
        """
        await page.set_content(disclaimer_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="disclaimer.png")

        # --- 📸 3. 分时段抓取图表 (上午只抓3h，下午只抓1D) ---
        clean_css = ".layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } .layout__area--center { position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; z-index: 9999 !important; }"
        base_chart_url = TV_CHART_URL.rstrip('/')
        
        target_interval = "180" if IS_MIDDAY else "1D"
        suffix = "3h" if IS_MIDDAY else "1d"
        
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            await page.goto(f"{base_chart_url}/?symbol={symbol}&interval={target_interval}", wait_until="domcontentloaded", timeout=60000)
            await page.add_style_tag(content=clean_css)
            await page.evaluate("window.dispatchEvent(new Event('resize'));")
            await page.wait_for_timeout(5000)
            await page.screenshot(path=f"ss_etf_{i}_{suffix}.png")

        await browser.close()

    # --- B. 语音与时间轴 ---
    image_timeline = []
    audio_files = []
    
    await safe_generate_tts(INTRO_TEXT, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    image_timeline.append(f"file 'cover_image.png'\nduration {dur_intro:.3f}\n")
    audio_files.append("audio_intro.mp3")

    full_text = f"🔥 【{TIME_LABEL}量化雷达播报】\n\n"
    transition_words = ["首先，", "其次，", "再看", "最后，"]
    
    for i, etf in enumerate(etf_list):
        readable_val = format_quant_voice(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}，ATR读数{readable_val}。"
        full_text += f"🔹 {etf['name']} 👉 {etf['change']}\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, etf_audio)
        await asyncio.sleep(1)
        
        dur_etf = get_audio_duration(etf_audio)
        # 匹配对应时段的唯一截图
        img_name = f"ss_etf_{i}_3h.png" if IS_MIDDAY else f"ss_etf_{i}_1d.png"
        image_timeline.append(f"file '{img_name}'\nduration {dur_etf:.3f}\n")
        audio_files.append(etf_audio)

    # 简短口播结尾
    await safe_generate_tts(OUTRO_TEXT, "audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'ss_main.png'\nduration {dur_outro:.3f}\n")
    audio_files.append("audio_outro.mp3")

    # 🎯 核心修复：为最后 2 秒的文字免责声明画面，生成一段 2 秒的静音音频，彻底对齐音画，消除黑屏/空白
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "-q:a", "9", "-acodec", "libmp3lame", "silence.mp3"])
    image_timeline.append(f"file 'disclaimer.png'\nduration 2.000\n")
    # 强制 ffmpeg 最后读取一帧防止无限延长
    image_timeline.append(f"file 'disclaimer.png'\n")
    audio_files.append("silence.mp3")

    with open("video_input.txt", "w") as f: f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_files])

    # --- C. 视频极速合成 (-shortest 参数斩断所有多余尾巴) ---
    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    if os.path.exists("bgm.mp3"):
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_input.txt", 
            "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
            "-stream_loop", "-1", "-i", "bgm.mp3", 
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", 
            "-map", "0:v", "-map", "[a]", 
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "192k", 
            "-shortest", final_video  # 🎯 确保视频在主音轨结束时立即掐断
        ]
    else:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_input.txt", "-f", "concat", "-safe", "0", "-i", "audio_input.txt", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video]
    
    subprocess.run(cmd, check=True)
    
    # --- D. 推送网感文案 ---
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    msg_title = f"📈 {TIME_LABEL}异动！{etf_list[0]['name']}触发{etf_list[0]['change']}！"
    xhs_text = (
        f"📝 【网感文案素材】\n\n"
        f"【标题】{msg_title}\n\n"
        f"【正文】\n{full_text}\n"
        f"💡 以上数值均为特定策略下的ATR指标，纯数据记录，拒绝主观预测。\n\n"
        f"#ETF #量化交易 #A股复盘 #{etf_list[0]['name']}"
    )

    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': xhs_text})

    with open(final_video, 'rb') as vf:
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendVideo", data={'chat_id': chat_id, 'caption': f"🎬 {msg_title}"}, files={'video': vf}, timeout=120)

    img_list = ["cover_image.png", "ss_main.png"]
    for i in range(len(etf_list)):
        img_list.append(f"ss_etf_{i}_{suffix}.png")
    img_list.append("disclaimer.png")
        
    for i in range(0, len(img_list), 10):
        chunk, media_group, files = img_list[i:i+10], [], {}
        for idx, img in enumerate(chunk):
            if os.path.exists(img):
                files[f"f{idx}"] = open(img, "rb")
                media_group.append({"type": "photo", "media": f"attach://f{idx}"})
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMediaGroup", data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
        for f in files.values(): f.close()

if __name__ == "__main__":
    asyncio.run(main())
