import os
import sys
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

def clean_for_tts(text):
    if not text: return ""
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace('*', '').replace('_', '').replace('#', '').replace('`', '')
    text = re.sub(r'(?i)\betf\b', ' E T F ', text)
    text = re.sub(r'(?i)\batr\b', ' A T R ', text)
    text = re.sub(r'(?i)\ba股\b', ' A 股 ', text)
    return text.strip()

# ==========================================
# 🔥 核心升级：AI 动态情绪与防断联轮询引擎
# ==========================================
def call_gemini_director(etf_list, time_label):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 GEMINI_API_KEY 环境变量！请检查 workflow 文件。")
        sys.exit(1)

    personas = [
        "【犀利毒舌风】恨铁不成钢，嘲讽散户无脑追高，强调量化纪律和ATR阈值的冷酷无情。",
        "【老派教授风】语重心长，逻辑严密，像上课一样拆解今天ETF异动背后的宏观逻辑和主力意图。",
        "【激情打血风】热血澎湃，看到指标突破极其兴奋，极力渲染主力大资金进场带来的爆发力。",
        "【悬疑揭秘风】故作神秘，仿佛发现了主力不可告人的建仓底牌，一步步引导观众看懂数据背后的猫腻。"
    ]
    today_persona = random.choice(personas)

    prompt = f"""
    你现在是一位在A股摸爬滚打了十几年的ETF量化交易老手，你的任务是完全掌控今天【{time_label}】的自媒体内容创作。
    
    今天的量化雷达异动数据如下：
    {json.dumps(etf_list, ensure_ascii=False, indent=2)}

    🚨 【今日最高指示：人设强制加载】 🚨
    今天你必须使用这种情绪状态和口吻来创作所有内容：{today_persona}

    【创作要求】：
    1. 必须返回合法的 JSON 格式（绝对不要包含 ```json 等 markdown 标记）。
    2. JSON 必须包含以下字段：
       - "video_intro": "短视频开场白（50-80字）。必须完美契合今日情绪！上来直接用这股情绪抛出暴论或悬念，不要任何俗套问候。注意：英文全写成 E T F、A T R 方便TTS朗读，绝对不要有表情符号。"
       - "etf_narratives": "一个数组（与输入数据长度一致）。用今日情绪对每一只ETF进行50字左右的犀利短评，结合它的涨跌幅，解释主力意图，拒绝平铺直叙。绝对不要重复用同样的句式，绝对不带表情符号。"
       - "social_title": "小红书/公众号的爆款标题（20字内，带emoji，极具煽动性或悬念，必须贴合今日设定的情绪）。"
       - "social_body": "一篇排版极其精美的小红书长文稿。大量运用自媒体emoji，分段清晰。用今日设定的情绪深度复盘今天的大盘，解释为什么咱们的专属量化指标（特别是ATR的涨跌异动）比看均线更准。文末必须加上强势引流钩子（例如：想白嫖我这套全天候监控信号的，评论区见）。"
    """

    model_pool = [
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-pro"
    ]

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    # 💡 物理阻断法：打断 URL 字符串，防止复制粘贴时被各种编辑器自动变成 Markdown 超链接！
    api_host = "https://" + "generativelanguage.googleapis.com"
    api_path = "/v1beta/models/"

    last_error = ""
    for model_name in model_pool:
        # 安全拼装 URL，彻底杜绝隐形括号和富文本识别
        url = f"{api_host}{api_path}{model_name}:generateContent?key={api_key}"
        
        try:
            print(f"🔄 正在尝试唤醒 AI 模型: {model_name} ...")
            response = requests.post(url, json=payload, headers=headers, timeout=45)
            response.raise_for_status()
            
            raw_text = response.json()['candidates'][0]['content']['parts'][0]['text']
            
            clean_text = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"^```\s*", "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text, flags=re.IGNORECASE)
            
            result = json.loads(clean_text)
            print(f"✅ 成功通过 {model_name} 输出深度好文案！")
            return result
            
        except requests.exceptions.HTTPError as e:
            last_error = str(e)
            if e.response.status_code == 404:
                print(f"⚠️ 模型 {model_name} 未被授权或找不到 (404)，自动切换下一个备胎...")
                continue
            else:
                print(f"❌ 致命错误：AI 接口请求被拒绝 (状态码: {e.response.status_code})。请确认 API Key 额度或权限。")
                sys.exit(1)
        except json.JSONDecodeError:
            print(f"⚠️ 模型 {model_name} 没按规矩输出 JSON，废弃重试...")
            continue
        except Exception as e:
            last_error = str(e)
            print(f"⚠️ 模型 {model_name} 网络或解析异常: {e}")
            continue

    print(f"❌ 致命错误：轮询池内所有 AI 模型全军覆没！最后一次报错: {last_error}")
    sys.exit(1)

async def main():
    print(f"🚀 开始执行【全自动AI情绪化复盘】工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 720, 'height': 1280}, is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
        )
        
        tv_session = os.getenv('TV_SESSION_ID', '').strip()
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        print("🔍 正在截取前端真实数据总览并提取核心指标...")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            data_loaded = False
            for _ in range(20):
                page_text = await page.evaluate("document.body.innerText")
                if re.search(r'\b(5\d{5}|1\d{5})\b', page_text):
                    data_loaded = True
                    break
                await page.wait_for_timeout(1000)
                
            if not data_loaded:
                print("🛑 网页加载超时，未能在20秒内渲染出有效的大盘 ETF 数据。")
                await browser.close()
                sys.exit(1)

            await page.wait_for_timeout(1000)
            await page.screenshot(path="ss_main.png")
            
            etf_list = []
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
                raise Exception("未解析到任何ETF数据条目")
        except Exception as e:
            print(f"🛑 数据抓取失败，停止生成。({e})")
            await browser.close()
            sys.exit(1)

        print("🎭 正在调度 AI 专家生成今日动态情绪剧本...")
        ai_script = call_gemini_director(etf_list, TIME_LABEL)
        
        global SELECTED_HOOK
        SELECTED_HOOK = ai_script['social_title']
        
        top3_html_blocks = ""
        for i, e in enumerate(etf_list[:3]):
            color = "#ff4d4f" if "+" in e['change'] else "#00e5ff" if "-" in e['change'] else "#ffffff"
            top3_html_blocks += f"<div style='background:rgba(255,255,255,0.1); padding:20px 40px; border-radius:15px; margin:15px 0; display:flex; justify-content:space-between; width:80%; font-size:38px;'><span style='font-weight:bold;'>TOP {i+1} {e['name']}</span><span style='color:{color}; font-weight:900;'>{e['change']}</span></div>"

        cover_html = f"""
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            html {{ background: #0f172a; margin: 0; padding: 0; overflow: hidden; width: 100vw; height: 100vh; }}
            body {{ 
                position: absolute; top: -5px; left: -5px; 
                width: calc(100vw + 10px); height: calc(100vh + 10px);
                margin: 0; padding: 0; overflow: hidden; box-sizing: border-box;
                background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); 
                display: flex; flex-direction: column; justify-content: center; align-items: center; 
                font-family: 'Microsoft YaHei', sans-serif; color: white; 
            }}
            .tag {{ background: #3b82f6; padding: 12px 35px; border-radius: 50px; font-size: 28px; font-weight: bold; margin-bottom: 50px; letter-spacing: 2px; }}
            .title {{ font-size: 55px; font-weight: 900; color: #fbbf24; text-align: center; margin-bottom: 60px; padding: 0 40px; line-height: 1.4; }}
        </style></head><body>
            <div class="tag">{TIME_LABEL}量化雷达</div>
            <div class="title">{SELECTED_HOOK}</div>
            {top3_html_blocks}
        </body></html>
        """
        await page.set_content(cover_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="cover_image.png")

        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            html { background: #ffffff; margin: 0; padding: 0; overflow: hidden; width: 100vw; height: 100vh; }
            body { 
                position: absolute; top: -5px; left: -5px; 
                width: calc(100vw + 10px); height: calc(100vh + 10px); background: #ffffff;
                display: flex; flex-direction: column; justify-content: center; align-items: center; 
                font-family: 'Microsoft YaHei', sans-serif; color: #333333; text-align: center; padding: 0 50px; 
            }
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

        print("🌐 正在抓取真实带指标的 K 线图...")
        clean_css = """
            .layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } 
            .layout__area--center { 
                position: fixed !important; top: 0 !important; left: 0 !important; 
                width: 100vw !important; height: 100vh !important; z-index: 9999 !important; 
                transform-origin: top left !important; 
                transform: scale(1.45, 1.15) !important; 
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

    print("🎵 正在合成 AI 定制情绪化配音...")
    active_intro = clean_for_tts(ai_script['video_intro'])
    await safe_generate_tts(active_intro, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    
    intro_visual_total = max(9.000, dur_intro)
    if dur_intro < intro_visual_total:
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(intro_visual_total - dur_intro), "intro_pad.mp3"])
        with open("intro_audio_list.txt", "w") as f: f.write("file 'audio_intro.mp3'\nfile 'intro_pad.mp3'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "intro_audio_list.txt", "-c", "copy", "final_intro.mp3"])
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "audio_intro.mp3", "-c", "copy", "final_intro.mp3"])
        intro_visual_total = dur_intro

    audio_files = ["final_intro.mp3"]
    image_timeline = [
        "file 'cover_image.png'\nduration 1.500\n",
        "file 'ss_main.png'\nduration 1.500\n"
    ]
    remain_zoom_time = intro_visual_total - 3.000
    image_timeline.append(f"file 'ss_main_zoomed.mp4'\nduration {remain_zoom_time:.3f}\n")

    for i, etf in enumerate(etf_list):
        if i < len(ai_script['etf_narratives']):
            etf_text = clean_for_tts(ai_script['etf_narratives'][i])
        else:
            etf_text = f"最后，别忘了看一眼{etf['name']}的核心异动。"
            
        etf_audio = f"audio_etf_{i}.mp3"
        await safe_generate_tts(etf_text, etf_audio)
        await asyncio.sleep(1)
        
        dur_etf = get_audio_duration(etf_audio)
        img_name = f"ss_etf_{i}_3h.png" if IS_MIDDAY else f"ss_etf_{i}_1d.png"
        image_timeline.append(f"file '{img_name}'\nduration {dur_etf:.3f}\n")
        audio_files.append(etf_audio)

    await safe_generate_tts(OUTRO_TEXT, "audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'disclaimer.png'\nduration {dur_outro:.3f}\n")
    audio_files.append("audio_outro.mp3")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "silence_end.mp3"])
    image_timeline.extend([f"file 'disclaimer.png'\nduration 2.000\n", "file 'disclaimer.png'\n"])
    audio_files.append("silence_end.mp3")

    with open("video_input.txt", "w") as f: f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_files])

    print("🎬 正在使用 Python 物理引擎渲染运镜 (大字号+极速垂直摇摄版)...")
    from PIL import Image
    import shutil
    zoom_fps, frames_dir = 25, "temp_zoom_frames"
    zoom_frames = int(remain_zoom_time * zoom_fps)
    if os.path.exists(frames_dir): shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    img = Image.open("ss_main.png")
    w, h = img.size
    target_w, target_h = int(w / 5.0), int(h / 5.0)

    for i in range(zoom_frames):
        if i <= 30:
            progress = i / 30.0
            ease_progress = 1 - (1 - progress) ** 3
            current_w = int(w - (w - target_w) * ease_progress)
            current_h = int(h - (h - target_h) * ease_progress)
            current_x, current_y = 0, int(165 * ease_progress) 
        else:
            current_w, current_h, current_x = target_w, target_h, 0
            pan_progress = (i - 30) / (zoom_frames - 30)
            current_y = int(165 + (420 * pan_progress))
        
        box = (current_x, current_y, current_x + current_w, current_y + current_h)
        frame = img.crop(box).resize((w, h), Image.Resampling.LANCZOS).convert('RGB')
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=95)

    subprocess.run(["ffmpeg", "-y", "-framerate", str(zoom_fps), "-i", f"{frames_dir}/frame_%04d.jpg", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(zoom_fps), "ss_main_zoomed.mp4"], check=True)
    shutil.rmtree(frames_dir)

    print("🎬 正在拼装最终带有动态前奏的分镜视频...")
    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "cover_image.png", "-t", "2", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p1.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "ss_main.png", "-t", "2", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p2.mp4"], check=True)
    
    with open("video_backend.txt", "w") as f: f.writelines(image_timeline[3:])
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_backend.txt", "-c:v", "libx264", "-r", "25", "-pix_fmt", "yuv420p", "p4.mp4"], check=True)
    
    with open("final_stitch.txt", "w") as f: f.write("file 'p1.mp4'\nfile 'p2.mp4'\nfile 'ss_main_zoomed.mp4'\nfile 'p4.mp4'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "final_stitch.txt", "-c:v", "copy", "pre_final_video.mp4"], check=True)

    if os.path.exists("bgm.mp3"):
        subprocess.run(["ffmpeg", "-y", "-i", "pre_final_video.mp4", "-f", "concat", "-safe", "0", "-i", "audio_input.txt", "-stream_loop", "-1", "-i", "bgm.mp3", "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "pre_final_video.mp4", "-f", "concat", "-safe", "0", "-i", "audio_input.txt", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video], check=True)
    
    for tmp in ["p1.mp4", "p2.mp4", "p4.mp4", "ss_main_zoomed.mp4", "pre_final_video.mp4"]:
        if os.path.exists(tmp): os.remove(tmp)
        
    print("✈️ 正在推送到 Telegram 接收端...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    
    xhs_text = f"📝 【一键直发 · 爆款推文库】\n\n💡 {ai_script['social_title']}\n\n{ai_script['social_body']}\n\n--- 🎬 视频文案备份 ---\n{ai_script['video_intro']}"
    msg_title = ai_script['social_title']

    try:
        res_text = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': xhs_text})
        res_text.raise_for_status()
        
        with open(final_video, 'rb') as vf:
            res_video = requests.post(f"https://api.telegram.org/bot{bot_token}/sendVideo", data={'chat_id': chat_id, 'caption': f"🎬 {msg_title}"}, files={'video': vf}, timeout=120)
            res_video.raise_for_status()

        img_list = ["cover_image.png", "ss_main.png"] + [f"ss_etf_{i}_{suffix}.png" for i in range(len(etf_list))] + ["disclaimer.png"]
        for i in range(0, len(img_list), 10):
            chunk, media_group, files = img_list[i:i+10], [], {}
            for idx, img in enumerate(chunk):
                if os.path.exists(img):
                    files[f"f{idx}"] = open(img, "rb")
                    media_group.append({"type": "photo", "media": f"attach://f{idx}"})
            
            res_media = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMediaGroup", data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
            res_media.raise_for_status()
            
            for f in files.values(): f.close()
            
    except Exception as e:
        print(f"🛑 推送至 Telegram 失败！原因: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
