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
        # 遵循口语化指令：ATR涨了/跌了
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
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace('*', '').replace('_', '').replace('#', '').replace('`', '')
    text = re.sub(r'(?i)\betf\b', ' E T F ', text)
    text = re.sub(r'(?i)\batr\b', ' A T R ', text)
    text = re.sub(r'(?i)\ba股\b', ' A 股 ', text)
    return text.strip()

# ==========================================
# 🔥 核心升级：AI 全能导演中枢 (编剧 + 视觉前端 + 摄像运镜)
# ==========================================
def call_ai_director(etf_list, time_label):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！请检查 Github Secrets。")
        sys.exit(1)

    prompt = f"""
    你现在是顶级的A股量化交易专家兼爆款自媒体运营大师。你的任务是完全自主掌控今天【{time_label}】的短视频脚本、大盘运镜规划以及 1920x1080 宽屏电脑封面的设计。作品必须科学、客观、极具观赏性，能引起观众共鸣。
    
    【今日核心触发数据（绝对事实，不可篡改）】：
    {json.dumps(etf_list, ensure_ascii=False, indent=2)}

    🚨 【最高指令：客观数据与绝对服从】 🚨
    1. 你引用的 ETF 名称和百分比数值，必须 100% 对应上表。
    2. JSON中 `change` 字段是独家【ATR异动指标】。正数代表向上异动，负数代表向下异动。
    3. 严禁在文案里编造5日均线、MACD、KDJ等垃圾指标！你的分析必须客观、科学，以数据为准绳。

    【输出要求】：必须返回合法的 JSON，精确包含以下 6 个字段：
    - "video_intro": 短视频开场口播（50-80字）。一针见血，点出榜首数据和今日交易情绪。英文写 E T F、A T R，无表情符号。
    - "etf_narratives": 数组，包含{len(etf_list)}个元素的短评。结合读数分析资金动作，客观犀利，无表情符号。
    - "social_title": 小红书/公众号爆款标题（20字内，带emoji）。
    - "social_body": 排版精美的推文正文。多用emoji，复盘真实数据。文末引流：想白嫖全天候量化信号，评论区见。
    - "camera_effect": 你作为导演，根据今天的大盘情绪自主选择一个开场运镜特效，必须是以下四个之一："zoom_in"（缓慢推进放大，适合强调和暴涨）、"zoom_out"（缓慢拉远，适合全局观和暴跌）、"pan_left"（向左平移，适合震荡）、"pan_right"（向右平移，适合趋势延续）。
    - "cover_html": 这是一段完整的 HTML5+CSS 代码字符串。
         * 尺寸：适配 1920x1080 电脑宽屏。
         * 风格：你完全自由发挥！可以根据今天的数据情况设计（比如：大涨用热血赤红、大跌用深渊冷蓝、高端极简黑金等）。
         * 内容：包含【{time_label}量化雷达】大标题，以及极具视觉冲击力的前三名ETF名称和读数排版。
         * 限制：纯代码实现，不可引入外部网络图片。
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
            {"role": "system", "content": "你是量化专家兼视觉导演。你的输出必须是纯粹的 JSON 格式，且绝对客观不捏造指标。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.6  # 给予 AI 一定的创作自由度来设计视觉，但限制其乱编数据
    }

    last_error = ""
    for attempt in range(3):
        try:
            print(f"🔄 正在呼叫 DeepSeek 全能导演引擎 (第{attempt+1}次尝试)...")
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            
            raw_text = response.json()['choices'][0]['message']['content']
            
            clean_text = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"^```\s*", "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text, flags=re.IGNORECASE)
            
            result = json.loads(clean_text)
            print(f"✅ DeepSeek 创意设计完成！AI 选定的运镜特效为: {result.get('camera_effect', 'zoom_in')}")
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
    print(f"🚀 开始执行【全自动AI宽屏重构版】工作流... {NOW}")
    
    async with async_playwright() as p:
        # 🚨 宽屏核心改造：强制切换为 1920x1080 的电脑网页视口
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

            # 宽屏需要多一点时间确保全屏渲染
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

        print("🎭 正在调度 AI 专家进行自由创作...")
        ai_script = call_ai_director(etf_list, TIME_LABEL)
        
        global SELECTED_HOOK
        SELECTED_HOOK = ai_script['social_title']
        
        # 🎨 让 AI 全权接管封面生成
        print("🎨 正在渲染 AI 自由发挥的 1080P 宽屏封面...")
        await page.set_content(ai_script.get('cover_html', '<html><body style="background:black;color:white;"><h1>设计生成失败，应用极简模式</h1></body></html>'))
        await page.wait_for_timeout(2000) # 给 CSS 炫酷滤镜留出渲染时间
        await page.screenshot(path="cover_image.png")

        # 宽屏免责声明
        disclaimer_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            html { background: #0b0f19; margin: 0; padding: 0; overflow: hidden; width: 100vw; height: 100vh; }
            body { 
                background: linear-gradient(135deg, #0b0f19 0%, #1a233a 100%);
                display: flex; flex-direction: column; justify-content: center; align-items: center; 
                font-family: 'Microsoft YaHei', sans-serif; color: #e2e8f0; text-align: center; height: 100vh; margin: 0;
            }
            h1 { color: #f8fafc; font-size: 80px; margin-bottom: 50px; font-weight: 900; letter-spacing: 10px;}
            p { font-size: 45px; line-height: 2; font-weight: bold; color: #cbd5e1; }
            .footer { margin-top: 80px; font-size: 35px; color: #64748b; border-top: 2px solid #334155; padding-top: 40px; width: 60%;}
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

    print("🎵 正在合成 AI 定制情绪化配音...")
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

    # 🎬 AI 掌机：根据 AI 意图加载相应的电影级运镜滤镜
    print("🎬 正在使用 FFmpeg 渲染 AI 选定的电影级运镜特效...")
    camera_effect = ai_script.get('camera_effect', 'zoom_in')
    zoom_fps = 30
    zoom_frames = int(remain_zoom_time * zoom_fps)
    
    if camera_effect == 'zoom_out':
        vf_filter = f"zoompan=z='max(1.3-0.001*in,1)':d={zoom_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
    elif camera_effect == 'pan_left':
        vf_filter = f"zoompan=z=1.2:d={zoom_frames}:x='max(0, (iw*0.2)-in*2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
    elif camera_effect == 'pan_right':
        vf_filter = f"zoompan=z=1.2:d={zoom_frames}:x='min(in*2, iw*0.2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
    else: # 默认 zoom_in
        vf_filter = f"zoompan=z='min(zoom+0.001,1.3)':d={zoom_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080"

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
    
    xhs_text = f"📝 【AI 全自动宽屏视觉版】\n\n💡 {ai_script['social_title']}\n\n{ai_script['social_body']}\n\n--- 🎬 视频文案备份 ---\n{ai_script['video_intro']}"
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
