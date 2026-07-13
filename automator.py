import os
import asyncio
import subprocess
import random
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/"
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/" # 你的专属带指标画板
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午盘" if IS_MIDDAY else "收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

# 2. 合规文案与动态钩子标题
INTRO_TEXT = f"各位观众朋友大家好，欢迎关注最新的行业ETF客观数据跟踪。截至{TIME_LABEL}，我们来梳理一下市场核心板块的最新动态。"
OUTRO_TEXT = "以上数据均源自公开市场客观统计，仅供全景量化复盘参考，不构成任何投资建议或操作引导。理财有风险，入市需谨慎。"

# 生成吸引眼球的标题（供封面和 Telegram 发送使用）
HOOK_TITLES = [
    f"🚨 {TIME_LABEL}异动追踪！四大核心ETF关键信号出现？",
    f"🔥 拒绝马后炮！{TIME_LABEL}真实数据复盘，主力意图显现？",
    f"⚡ 支撑还是突破？{TIME_LABEL}核心板块量化客观全景推演",
    f"📊 {TIME_LABEL}数据速递：抛开情绪，这四大板块客观走势如何？"
]
SELECTED_HOOK = random.choice(HOOK_TITLES)

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def format_trend(val_str):
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return "震荡上行", f"正的{abs(val)}%"
        elif val < 0: return "震荡回调", f"负的{abs(val)}%"
        return "横盘震荡", "零轴附近"
    except:
        return "平稳运行", "暂无明显波动"

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
           "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

async def main():
    print(f"🚀 开始执行【运营增强版】工作流... {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        
        # 注入 TradingView Cookie
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
            
        page = await context.new_page()

        # --- 新增：使用 HTML 动态渲染高转化率封面 ---
        print("🎨 正在生成爆款封面图...")
        cover_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    margin: 0; padding: 0; width: 1280px; height: 720px;
                    background: linear-gradient(135deg, #121212 0%, #2b0000 100%);
                    display: flex; flex-direction: column; justify-content: center; align-items: center;
                    font-family: 'Microsoft YaHei', sans-serif; color: white;
                }}
                .tag {{ background: #e50914; padding: 12px 35px; border-radius: 50px; font-size: 38px; font-weight: bold; margin-bottom: 35px; letter-spacing: 5px; box-shadow: 0 4px 15px rgba(229,9,20,0.5); }}
                .title {{ font-size: 85px; font-weight: 900; color: #ffcc00; text-shadow: 4px 4px 15px rgba(0,0,0,0.9); text-align: center; line-height: 1.3; width: 90%; }}
                .subtitle {{ font-size: 45px; color: #00e5ff; margin-top: 50px; font-weight: bold; letter-spacing: 2px; }}
                .footer {{ position: absolute; bottom: 30px; font-size: 28px; color: #777; }}
            </style>
        </head>
        <body>
            <div class="tag">硬核数据复盘</div>
            <div class="title">{SELECTED_HOOK.replace('！', '！<br>')}</div>
            <div class="subtitle">👉 纯客观 · 无感情 · 拒绝剧本 👈</div>
            <div class="footer">ETF走势全景推演</div>
        </body>
        </html>
        """
        await page.set_content(cover_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="cover_image.png")
        
        # 1. 抓取总览图
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(3000) # 等待数据完全渲染
        await page.screenshot(path="ss_main.png")
        
        # --- 👇 核心大升级：实时智能解析网页数据 👇 ---
        print("🔍 正在实时抓取今日行情数据...")
        etf_list = []
        try:
            # 获取页面上所有的行元素 (兼容各种表格和列表结构)
            row_locators = page.locator("tr, .el-table__row, .row, li")
            count = await row_locators.count()
            
            for i in range(count):
                if len(etf_list) >= 4: # 只取排名前 4 的数据
                    break
                    
                text = await row_locators.nth(i).inner_text()
                # 将每一行内的文本按换行符拆分，过滤掉空行
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
                # 智能识别逻辑：必须至少有3行数据，且第二行包含数字（对应股票代码）
                if len(lines) >= 3 and any(c.isdigit() for c in lines[1]):
                    # 1. 提取名称 (例如 "银行ETF (2) >" -> "银行ETF")
                    name = lines[0].split('(')[0].replace('>', '').strip()
                    
                    # 2. 提取6位股票代码 (例如 "512800 07-13" -> "512800")
                    code = ''.join(filter(str.isdigit, lines[1]))[:6]
                    
                    # 3. 提取涨跌幅 (根据截图，索引2通常是上午，索引4是全天。根据当前时间智能取值)
                    # 如果元素不够长，就取最后一个带 % 号的值兜底
                    try:
                        if not IS_MIDDAY and len(lines) > 4:
                            change = lines[4]
                        else:
                            change = lines[2]
                    except:
                        change = [L for L in lines if '%' in L][-1]
                    
                    # 确保提取到了正确的6位A股代码才加入列表
                    if len(code) == 6:
                        etf_list.append({"name": name, "code": code, "change": change})
            
            if not etf_list:
                raise Exception("页面结构变动，未匹配到有效数据")
                
            print(f"✅ 成功抓取今日实时数据：")
            for e in etf_list:
                print(f"   - {e['name']} ({e['code']}): {e['change']}")
                
        except Exception as e:
            print(f"⚠️ 实时抓取解析遇到问题 ({e})，启用兜底安全模式...")
            # 只有当目标网页完全打不开或结构大改时，才会用这组数据保底，防止工作流崩溃
            etf_list = [
                {"name": "核心ETF1", "code": "510050", "change": "+0.0%"},
                {"name": "核心ETF2", "code": "159928", "change": "+0.0%"},
                {"name": "核心ETF3", "code": "512800", "change": "+0.0%"},
                {"name": "核心ETF4", "code": "512170", "change": "+0.0%"}
            ]
        # --- 👆 实时智能解析网页数据结束 👆 ---

        # 2. 抓取专属带指标图表
        print("🌐 加载 TV 私有画板...")
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

    # --- B. 语音与画面底层时间轴 ---
    print("🎵 生成 TTS 语音...")
    image_timeline = []
    audio_files = []
    
    # 播放开场白时，展示刚刚生成的带有超大黄字的封面图
    await edge_tts.Communicate(INTRO_TEXT, "zh-CN-YunxiNeural").save("audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    image_timeline.append(f"file 'cover_image.png'\nduration {dur_intro:.3f}\n")
    audio_files.append("audio_intro.mp3")
    full_text = INTRO_TEXT + "\n"

    transition_words = ["首先，", "紧接着，", "再来关注", "最后，"]
    for i, etf in enumerate(etf_list):
        trend, readable_val = format_trend(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}今日表现为{trend}，变动幅度和比例为{readable_val}。"
        full_text += etf_text + "\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        await edge_tts.Communicate(etf_text, "zh-CN-YunxiNeural").save(etf_audio)
        
        dur_etf = get_audio_duration(etf_audio)
        half_dur = dur_etf / 2.0 
        image_timeline.append(f"file 'ss_etf_{i}_3h.png'\nduration {half_dur:.3f}\n")
        image_timeline.append(f"file 'ss_etf_{i}_1d.png'\nduration {half_dur:.3f}\n")
        audio_files.append(etf_audio)

    await edge_tts.Communicate(OUTRO_TEXT, "zh-CN-YunxiNeural").save("audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'ss_main.png'\nduration {dur_outro:.3f}\n")
    image_timeline.append(f"file 'ss_main.png'\n")
    audio_files.append("audio_outro.mp3")
    full_text += OUTRO_TEXT

    with open("video_input.txt", "w") as f: f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f: f.writelines([f"file '{a}'\n" for a in audio_files])

    # --- C. 电影级底层双轨混流混音 ---
    print("🎬 正在进行终极音画合成（带BGM自适应避让）...")
    final_video = f"etf_report_sync_{FILE_SUFFIX}.mp4"
    
    # 检测仓库中是否有用户上传的背景音乐
    if os.path.exists("bgm.mp3"):
        print("🎧 检测到背景音乐 bgm.mp3，开启双轨混音模式...")
        # 黑科技混音算法：
        # [1:a]是主讲人声音音量设为1.0；[2:a]是BGM，音量压低到0.15；
        # duration=first 确保视频总长度跟随解说录音结束，音乐自动淡出或裁切。
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
        print("🔇 未检测到 bgm.mp3，使用无纯净版语音合成...")
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
    
    # 【第一发：发送生成的短视频】
    print("1️⃣ 正在发送短视频...")
    video_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    video_caption = f"🎬 【视频素材】\n⭐⭐ {SELECTED_HOOK} ⭐⭐\n\n{full_text}"
    with open(final_video, 'rb') as video_file:
        requests.post(video_url, data={'chat_id': chat_id, 'caption': video_caption}, files={'video': video_file}, timeout=120)

    # 【第二发：发送小红书/公众号专属排版文案】
    print("2️⃣ 正在发送图文排版文案...")
    # 对原有的口语化文案进行图文网感改造
    xhs_body = full_text.replace("各位观众朋友大家好，欢迎关注", "🔥 欢迎关注")\
                        .replace("首先，", "🟢 ")\
                        .replace("紧接着，", "🟢 ")\
                        .replace("再来关注", "🟢 ")\
                        .replace("随后是", "🟢 ")\
                        .replace("最后，", "🟢 ")\
                        .replace("以上数据均源自", "💡 以上数据均源自")
    
    xhs_text = (
        f"📝 【图文排版素材（直接长按复制）】\n\n"
        f"【标题建议】{SELECTED_HOOK}\n\n"
        f"【正文内容】\n{xhs_body}\n\n"
        f"#ETF #股市复盘 #量化交易 #行情分析 #A股 #投资记录"
    )
    msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(msg_url, data={'chat_id': chat_id, 'text': xhs_text})

    # 【第三发：将所有高清截图作为“相册(Album)”发送】
    print("3️⃣ 正在发送高清配图相册...")
    album_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
    
    # 收集所有的图片路径 (封面 + 总数据 + 4只ETF的3H和1D图)
    img_list = ["cover_image.png", "ss_main.png"]
    for i in range(len(etf_list)):
        img_list.append(f"ss_etf_{i}_3h.png")
        img_list.append(f"ss_etf_{i}_1d.png")
        
    # Telegram 限制每个相册最多 10 张图，这里做动态切分发送，确保极其稳定
    for i in range(0, len(img_list), 10):
        chunk = img_list[i:i+10]
        media_group = []
        files = {}
        for idx, img in enumerate(chunk):
            if os.path.exists(img):
                files[f"file{idx}"] = open(img, "rb")
                media_group.append({"type": "photo", "media": f"attach://file{idx}"})
        
        requests.post(album_url, data={'chat_id': chat_id, 'media': json.dumps(media_group)}, files=files, timeout=60)
        
        # 发送完毕后关闭文件占用
        for f in files.values():
            f.close()
            
    print("🎉 全平台矩阵素材（视频+排版文案+无损套图）已全部推送到你的手机！")

if __name__ == "__main__":
    asyncio.run(main())
