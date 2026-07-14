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

# 2. 极简口播话术（配合新分镜）
INTRO_TEXT = f"欢迎关注，我们全天候监控40多只大型ETF，每天对波动最大的4只进行复盘，需要更多ETF请联系我们。"
OUTRO_TEXT = "本内容不构成投资建议。"

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def format_quant_voice(val_str):
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return f"A T R涨幅为{abs(val)}%"
        elif val < 0: return f"A T R跌幅为{abs(val)}%"
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

async def main():
    print(f"🚀 开始执行【极速推镜头定格+直接免责片尾】工作流... {NOW}")
    
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

        # --- A. 抓取真实数据并生成手机原生截图 ---
        print("🔍 正在截取监控总览页面...")
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
                        change = pcts[1] if not IS_MIDDAY and len(pcts) >= 2 else pcts[0]
                        if not any(e['code'] == code for e in etf_list):
                            etf_list.append({"name": name, "code": code, "change": change})
            if not etf_list:
                raise Exception("未找到数据")
        except Exception as e:
            print(f"🛑 数据抓取失败，停止生成。({e})")
            await browser.close()
            return 

        # --- 🎨 1. 生成【动态前三名】高级封面 ---
        global SELECTED_HOOK
        top_etf = etf_list[0]
        SELECTED_HOOK = f"📊 {TIME_LABEL}量化追踪！{top_etf['name']}指标数值达{top_etf['change']}，核心数据客观复盘！"
        
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

        # --- 🎨 2. 生成【白色极简】免责声明 ---
        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { margin: 0; padding: 0; width: 720px; height: 1280px; background: #ffffff; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Microsoft YaHei', sans-serif; color: #333333; text-align: center; padding: 0 50px; box-sizing: border-box;}
            h1 { color: #000000; font-size: 55px; margin-bottom: 40px; font-weight: 900; letter-spacing: 5px;}
            p { font-size: 32px; line-height: 1.8; font-weight: bold; }
            .footer { margin-top: 60px; font-size: 26px; color: #888888; border-top: 2px solid #eeeeee; padding-top: 30px; width: 80%;}
        </style></head><body>
            <h1>免责声明</h1>
            <p>本视频内所有数据、图表及指标读数<br>均基于特定量化模型客观记录生成<br><br>不代表标的真实涨跌幅<br>亦不构成任何买卖及投资建议</p>
            <div class="footer">市场有风险 · 投资需谨慎</div>
        </body></html>
        """
        await page.set_content(disclaimer_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="disclaimer.png")

        # --- 📸 3. TV图表 (完美微调的拉伸贴边裁剪) ---
        print("🌐 正在抓取完美微调的 K 线图...")
        clean_css = """
            .layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } 
            .layout__area--center { 
                position: fixed !important; top: 0 !important; left: 0 !important; 
                width: 100vw !important; height: 100vh !important; z-index: 9999 !important; 
                transform-origin: top left !important; 
                transform: scale(1.40, 1.0) translate(-12px, 5px) !important; 
            }
        """
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

    # --- B. 语音合成与复合时间轴生成 ---
    print("🎵 正在合成底层音频轨...")
    await safe_generate_tts(INTRO_TEXT, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    
    # 动态前奏总时长要求：2秒封面 + 2秒大盘静止 + 5秒放大推镜头 = 9秒
    intro_visual_total = 9.000
    if dur_intro < intro_visual_total:
        silence_pad = intro_visual_total - dur_intro
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(silence_pad), "intro_pad.mp3"])
        with open("intro_audio_list.txt", "w") as f:
            f.write("file 'audio_intro.mp3'\nfile 'intro_pad.mp3'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "intro_audio_list.txt", "-c", "copy", "final_intro.mp3"])
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "audio_intro.mp3", "-c", "copy", "final_intro.mp3"])
        intro_visual_total = dur_intro

    audio_files = ["final_intro.mp3"]
    image_timeline = []

    # 1. 封面展示 1.5 秒
    image_timeline.append("file 'cover_image.png'\nduration 1.500\n")
    # 2. 监控大盘原始展示 1.5 秒
    image_timeline.append("file 'ss_main.png'\nduration 1.500\n")
    # 3. 剩余时间分给大盘动态放大推镜头 (最少4秒)
    remain_zoom_time = intro_visual_total - 3.000
    image_timeline.append(f"file 'ss_main_zoomed.mp4'\nduration {remain_zoom_time:.3f}\n")

    full_text = f"🔥 【{TIME_LABEL}量化雷达播报】\n\n"
    transition_words = ["首先，", "其次，", "再看", "最后，"]
    
    # 【ETF 正片分镜】
    for i, etf in enumerate(etf_list):
        readable_val = format_quant_voice(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}，{readable_val}。"
        full_text += f"🔹 {etf['name']} 👉 {etf['change']}\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, etf_audio)
        await asyncio.sleep(1)
        
        dur_etf = get_audio_duration(etf_audio)
        img_name = f"ss_etf_{i}_3h.png" if IS_MIDDAY else f"ss_etf_{i}_1d.png"
        image_timeline.append(f"file '{img_name}'\nduration {dur_etf:.3f}\n")
        audio_files.append(etf_audio)

    # 【片尾分镜：直接显示免责声明】
    await safe_generate_tts(OUTRO_TEXT, "audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'disclaimer.png'\nduration {dur_outro:.3f}\n")
    audio_files.append("audio_outro.mp3")

    # 免责声明底部的静音留白2秒
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "silence_end.mp3"])
    image_timeline.append(f"file 'disclaimer.png'\nduration 2.000\n")
    image_timeline.append(f"file 'disclaimer.png'\n")
    audio_files.append("silence_end.mp3")

    with open("video_input.txt", "w") as f: f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_files])

    # --- C. FFmpeg 运镜渲染引擎 (精准左上角轨道推镜头) ---
    print("🎬 正在使用高级滤镜渲染 极速推镜头 定格效果...")
    # 💡 核心运镜方程解析：
    # z='min(1+(in/25), 3.0)'：in/25 代表每秒放大1倍。镜头会在前 2 秒内极速放大到 3.0 倍，后 3 秒死死定格！
    # x='10*(zoom-1)' 和 y='105*(zoom-1)'：配合缩放的轨道方程。当放大到3倍时，X刚好锁定在 20 像素，Y锁定在 210 像素。完美卡住前5只ETF！
    zoom_fps = 25
    zoom_frames = int(remain_zoom_time * zoom_fps)
    zoom_cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", "ss_main.png", 
        "-vf", f"zoompan=z='min(1+(in/25), 3.0)':x='10*(zoom-1)':y='105*(zoom-1)':d={zoom_frames}:s=720x1280,format=yuv420p", 
        "-c:v", "libx264", "-r", str(zoom_fps), "-t", f"{remain_zoom_time:.3f}", "ss_main_zoomed.mp4"
    ]
    subprocess.run(zoom_cmd, check=True)

    # --- D. 视频最终多轨合成 ---
    print("🎬 正在拼装最终带有动态前奏的分镜视频...")
    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    
    # 稳健拼接策略：分段组装再合并
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "cover_image.png", "-t", "2", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p1.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "ss_main.png", "-t", "2", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p2.mp4"], check=True)
    
    with open("video_backend.txt", "w") as f:
        f.writelines(image_timeline[3:])
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_backend.txt", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p4.mp4"], check=True)
    
    # 将 p1 + p2 + ss_main_zoomed.mp4 + p4 全体无缝串联
    with open("final_stitch.txt", "w") as f:
        f.write("file 'p1.mp4'\nfile 'p2.mp4'\nfile 'ss_main_zoomed.mp4'\nfile 'p4.mp4'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "final_stitch.txt", "-c:v", "copy", "pre_final_video.mp4"], check=True)

    # 4. 挂载混合了 BGM 的音频轨
    if os.path.exists("bgm.mp3"):
        cmd = [
            "ffmpeg", "-y", "-i", "pre_final_video.mp4", 
            "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
            "-stream_loop", "-1", "-i", "bgm.mp3", 
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", 
            "-map", "0:v", "-map", "[a]", 
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video  
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", "pre_final_video.mp4", 
            "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video
        ]
    
    subprocess.run(cmd, check=True)
    
    # --- E. 清理临时分段视频 ---
    for tmp in ["p1.mp4", "p2.mp4", "p4.mp4", "ss_main_zoomed.mp4", "pre_final_video.mp4"]:
        if os.path.exists(tmp): os.remove(tmp)
        
    # --- F. 推送 Telegram 全平台素材 ---
    print("✈️ 正在推送到 Telegram 接收端...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    msg_title = f"📈 {TIME_LABEL}异动！{etf_list[0]['name']}触发{etf_list[0]['change']}！"
    xhs_text = (
        f"📝 【网感文案素材】\n\n"
        f"【标题】{msg_title}\n\n"
        f"【正文】\n{full_text}\n"
        f"💡 本文数据为特定策略指标，纯数据记录，拒绝主观预测。\n\n"
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
