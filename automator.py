import os
import sys
import json
import asyncio
import subprocess
import random
import re
import time
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
    
    # 🛡️ 物理装甲：如果 AI 智障返回了字典/对象，强行提取里面的字符串
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
# 🔥 核心升级：高亮明快封面 + 客观极简文案 + 防崩溃装甲
# ==========================================
def call_ai_director(etf_list, time_label):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！请检查 Github Secrets。")
        sys.exit(1)

    prompt = f"""
    你现在是顶级的A股量化交易专家兼爆款自媒体视觉设计师。你的任务是接管今天【{time_label}】的短视频极简脚本，以及设计一张 1920x1080 的明亮系宽屏封面。作品必须科学、客观、极其清爽。
    
    【今日核心触发数据（绝对事实，不可篡改）】：
    {json.dumps(etf_list, ensure_ascii=False, indent=2)}

    🚨 【最高指令：极简客观与绝对服从】 🚨
    1. 你的分析必须 100% 依赖上方 JSON 里的 ETF 名称和 `change`（ATR异动指标）数值。
    2. 严禁在文案里编造5日均线、MACD、KDJ等垃圾指标，严禁对未来走势进行额外猜测和主观推演。
    3. 语言必须极其精炼，少即是多！

    【输出要求】：必须返回合法的 JSON，精确包含以下 5 个字段：
    - "video_intro": 短视频开场口播。只需1到2句（20-30字），极简概括今日盘面即可，拒绝长篇大论。英文写 E T F、A T R，无表情符号。
    - "etf_narratives": 🚨 必须是一个【纯字符串数组】（格式示例：["短评1", "短评2"]），严格包含{len(etf_list)}个字符串元素。数组内部绝对不能是字典或对象！针对单只ETF，只需两三句客观讲解数据，绝对不要进行任何额外猜测。无表情符号。
    - "social_title": 小红书/公众号爆款标题（20字内，带emoji）。
    - "social_body": 排版精美、分段清晰的推文正文。多用emoji，客观复盘真实数据。文末引流：想白嫖全天候量化信号，评论区见。
    - "cover_html": 这是一段完整的 HTML5+CSS 代码字符串。
         * 尺寸：适配 1920x1080 电脑宽屏。
         * 风格要求：必须使用明亮、通透、积极的浅色系背景（如纯白、浅金、天蓝、科技银灰等渐变），坚决弃用暗黑系沉闷色调！保持视觉上的清爽和现代高级感。
         * 内容：包含【{time_label}量化雷达】大标题，以及排版极具冲击力的前三名ETF名称和读数。
         * 限制：纯代码实现，不可引入外部网络图片。字体使用 Microsoft YaHei，字号要大且醒目。
    """

    ds_host = "https://" + "api.deepseek.com"
    url = f"{ds_host}/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是量化专家兼视觉设计师。你的输出必须是纯粹的 JSON 格式，且绝对客观不捏造指标，视觉追求明亮极简。"},
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
            
            result = json.loads(clean_text)
            print(f"✅ DeepSeek 极简客观文案与明亮封面设计完成！")
            return result
            
        except requests.exceptions.HTTPError as e:
            last_error = str(e)
            print(f"⚠️ DeepSeek 接口请求拒绝 (状态码: {e.response.status_code})。")
            time.sleep(3)
        except json.JSONDecodeError:
            print(f"⚠️ DeepSeek 未按规范输出 JSON，废弃重试...")
            time.sleep(3)
        except Exception as e:
            last_error = str(e)
            print(f"⚠️ DeepSeek 网络连接异常: {e}，重试中...")
            time.sleep(3)

    print(f"❌ 致命错误：DeepSeek API 请求失败，最后报错: {last_error}")
    sys.exit(1)

async def main():
    print(f"🚀 开始执行【高亮宽屏 + 左侧 3 倍强势暴推】工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}, 
            is_mobile=False,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
                page_text = await page.evaluate("document.body.innerText")
                if re.search(r'\b(5\d{5}|1\d{5})\b', page_text):
                    data_loaded = True
                    break
                await page.wait_for_timeout(1000)
                
            if not data_loaded:
                print("🛑 网页加载超时，未能在20秒内渲染出有效的大盘 ETF 数据。")
                await browser.close()
                sys.exit(1)

            await page.wait_for_timeout(2000)
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

        print("🎭 正在调度 AI 专家进行自由明亮创作...")
        ai_script = call_ai_director(etf_list, TIME_LABEL)
        
        global SELECTED_HOOK
        SELECTED_HOOK = ai_script['social_title']
        
        print("🎨 正在渲染 AI 生成的 1080P 明亮宽屏封面...")
        await page.set_content(ai_script.get('cover_html', '<html><body style="background:white;color:black;"><h1>设计生成失败，应用极简模式</h1></body></html>'))
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

        print("🌐 正在抓取真实带指标的 16:9 TradingView 宽屏图表...")
        clean_css = """
            .layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom, [data-name='widgetbar'], #widgetbar, .widgetbar-wrap { display: none !important; } 
            .layout__area--center { 
                position: fixed !important; top: 0 !important; left: 0 !important; 
                width: 100vw !important; height: 100vh !important; z-index: 9999 !important; 
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

    print("🎵 正在合成极简口语化配音...")
    active_intro = clean_for_tts(ai_script['video_intro'])
    await safe_generate_tts(active_intro, "audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    
    intro_visual_total = max(8.000, dur_intro)
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
        "file 'ss_main.png'\nduration 1.000\n"
    ]
    remain_zoom_time = intro_visual_total - 2.500

    print("🎬 正在使用 FFmpeg 渲染强力左侧 3 倍推进特写...")
    zoom_fps = 30
    zoom_frames = int(remain_zoom_time * zoom_fps)
    
    vf_filter = f"zoompan=z='min(zoom+0.03, 3.0)':d={zoom_frames}:x='iw*0.05':y='ih/2-(ih/zoom/2)':s=1920x1080"

    zoom_cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", "ss_main.png",
        "-vf", vf_filter,
        "-t", str(remain_zoom_time),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(zoom_fps), "ss_main_zoomed.mp4"
    ]
    subprocess.run(zoom_cmd, check=True)
    image_timeline.append(f"file 'ss_main_zoomed.mp4'\nduration {remain_zoom_time:.3f}\n")

    for i, etf in enumerate(etf_list):
        if i < len(ai_script['etf_narratives']):
            etf_text = clean_for_tts(ai_script['etf_narratives'][i])
        else:
            etf_text = f"来看{etf['name']}的客观走势。"
            
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

    print("🎬 正在拼装 1920x1080 最终视频序列...")
    final_video = f"etf_report_{FILE_SUFFIX}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "cover_image.png", "-t", "1.5", "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", "p1.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", "ss_main.png", "-t", "1.0", "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", "p2.mp4"], check=True)
    
    with open("video_backend.txt", "w") as f: f.writelines(image_timeline[3:])
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video_backend.txt", "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", "p4.mp4"], check=True)
    
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
    
    xhs_text = f"📝 【明亮宽屏 + 客观极简版】\n\n💡 {ai_script['social_title']}\n\n{ai_script['social_body']}\n\n--- 🎬 视频文案备份 ---\n{ai_script['video_intro']}"
    msg_title = ai_script['social_title']

    tg_host = "https://" + "api.telegram.org/bot"

    try:
        res_text = requests.post(f"{tg_host}{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': xhs_text})
        res_text.raise_for_status()
        
        with open(final_video, 'rb') as vf:
            res_video = requests.post(f"{tg_host}{bot_token}/sendVideo", data={'chat_id': chat_id, 'caption': f"🎬 {msg_title}"}, files={'video': vf}, timeout=120)
            res_video.raise_for_status()

        img_list = ["cover_image.png", "ss_main.png"] + [f"ss_etf_{i}_{suffix}.png" for i in range(len(etf_list))] + ["disclaimer.png"]
        for i in range(0, len(img_list), 10):
            chunk, media_group, files = img_list[i:i+10], [], {}
            for idx, img in enumerate(chunk):
                if os.path.exists(img):
                    files[f"f{idx}"] = open(img, "rb")
                    media_group.append({"type": "photo", "media": f"attach://f{idx}"})
            
            res_media = requests.post(f"{tg_host}{bot_token}/sendMediaGroup", data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
            res_media.raise_for_status()
            
            for f in files.values(): f.close()
            
    except Exception as e:
        print(f"🛑 推送至 Telegram 失败！原因: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
