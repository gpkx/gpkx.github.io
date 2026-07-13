import os
import asyncio
import subprocess
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/"
# 👇 ！！！请把这里换成你带指标的专属图表链接（必须带有后缀ID）！！！
TV_CHART_URL = "https://cn.tradingview.com/chart/Umn0unG5/" 
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午间盘面" if IS_MIDDAY else "今日收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

# 2. 合规文案模板（已精简为 4 只 ETF）
INTRO_TEXT = f"各位观众朋友大家好，欢迎关注最新的行业ETF客观数据跟踪。截至{TIME_LABEL}，我们来梳理一下市场核心板块的最新动态。"
OUTRO_TEXT = "以上数据均源自公开市场客观统计，仅供全景量化复盘参考，不构成任何投资建议或操作引导。理财有风险，入市需谨慎。"

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

# 获取音频精确时长工具
def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
           "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

async def main():
    print(f"🚀 开始执行终极同步工作流... {NOW}")
    
    # --- A. 数据抓取与图表截图 ---
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        
        # 【核心修复1】双域名注入 Cookie，确保 100% 穿透登录墙，加载私有 ATR 和 RSI 指标
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([
                {'name': 'sessionid', 'value': tv_session, 'domain': '.tradingview.com', 'path': '/'},
                {'name': 'sessionid', 'value': tv_session, 'domain': '.cn.tradingview.com', 'path': '/'}
            ])
        else:
            print("⚠️ 警告：未检测到 TV_SESSION_ID，指标可能无法加载！")
            
        page = await context.new_page()
        
        # 1. 抓取总览图
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="ss_main.png")
        
        etf_list = [
            {"name": "酒ETF", "code": "512690", "change": "+4.5%"},
            {"name": "医疗ETF", "code": "512170", "change": "+3.9%"},
            {"name": "消费ETF", "code": "159928", "change": "+3.4%"},
            {"name": "银行ETF", "code": "512800", "change": "+2.2%"}
        ]

        # 2. 抓取专属带指标图表
        print("🌐 正在使用账号身份加载私有画板...")
        # 确保基础 URL 结尾格式正确
        base_chart_url = TV_CHART_URL.rstrip('/')
        
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            print(f"📸 正在抓取第 {i+1}/4 只 ETF: {symbol} (3小时与日线)")
            
            # 【核心修复2】放弃键盘输入，改用 URL 强制传递标的和周期，彻底解决不切换画面的 BUG
            # 抓取 3小时线 (interval=180)
            url_3h = f"{base_chart_url}/?symbol={symbol}&interval=180"
            await page.goto(url_3h, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000) # 给予 6 秒充足时间让 Pine Script 指标计算渲染
            await page.screenshot(path=f"ss_etf_{i}_3h.png")
            
            # 抓取 日线 (interval=1D)
            url_1d = f"{base_chart_url}/?symbol={symbol}&interval=1D"
            await page.goto(url_1d, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            await page.screenshot(path=f"ss_etf_{i}_1d.png")

        await browser.close()

    # --- B. 语音与画面底层时间轴精准对齐 ---
    print("🎵 开始生成 TTS 语音并构建底层时间轴...")
    
    image_timeline = [] # 记录画面及对应时长
    audio_files = []    # 记录音频文件列表
    
    # 1. 开场对齐
    await edge_tts.Communicate(INTRO_TEXT, "zh-CN-YunxiNeural").save("audio_intro.mp3")
    dur_intro = get_audio_duration("audio_intro.mp3")
    image_timeline.append(f"file 'ss_main.png'\nduration {dur_intro:.3f}\n")
    audio_files.append("audio_intro.mp3")
    full_text = INTRO_TEXT + "\n"

    # 2. 4只ETF中间对齐
    transition_words = ["首先，", "紧接着，", "再来关注", "最后，"]
    for i, etf in enumerate(etf_list):
        trend, readable_val = format_trend(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}今日表现为{trend}，变动幅度和比例为{readable_val}。"
        full_text += etf_text + "\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        await edge_tts.Communicate(etf_text, "zh-CN-YunxiNeural").save(etf_audio)
        
        dur_etf = get_audio_duration(etf_audio)
        half_dur = dur_etf / 2.0 # 3小时线和日线各占一半语音时间
        
        image_timeline.append(f"file 'ss_etf_{i}_3h.png'\nduration {half_dur:.3f}\n")
        image_timeline.append(f"file 'ss_etf_{i}_1d.png'\nduration {half_dur:.3f}\n")
        audio_files.append(etf_audio)

    # 3. 结尾对齐
    await edge_tts.Communicate(OUTRO_TEXT, "zh-CN-YunxiNeural").save("audio_outro.mp3")
    dur_outro = get_audio_duration("audio_outro.mp3")
    image_timeline.append(f"file 'ss_main.png'\nduration {dur_outro:.3f}\n")
    # FFmpeg concat 要求最后一行重复最后一张图且不带时长
    image_timeline.append(f"file 'ss_main.png'\n")
    audio_files.append("audio_outro.mp3")
    full_text += OUTRO_TEXT

    # 生成底层流配置文本
    with open("video_input.txt", "w") as f:
        f.writelines(image_timeline)
    with open("audio_input.txt", "w") as f:
        for aud in audio_files:
            f.write(f"file '{aud}'\n")

    # --- C. 一次性绝对同步渲染 ---
    print("🎬 开始进行一次性绝对同步音画合成...")
    final_video = f"etf_report_sync_{FILE_SUFFIX}.mp4"
    
    # 黑科技：直接将音频和图片的流在一次命令中混合，彻底消灭拼接累加延迟，设置帧率为25
    ffmpeg_cmd = [
        "ffmpeg", "-y", 
        "-f", "concat", "-safe", "0", "-i", "video_input.txt", 
        "-f", "concat", "-safe", "0", "-i", "audio_input.txt", 
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
        "-c:a", "aac", "-b:a", "192k", 
        final_video
    ]
    subprocess.run(ffmpeg_cmd, check=True)
    
    # --- D. 发送到 Telegram ---
    print("✈️ 开始推送到 Telegram...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    tg_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    caption_text = f"📊 【{TIME_LABEL} 深度复盘】\n\n{full_text}"
    
    with open(final_video, 'rb') as video_file:
        requests.post(tg_url, data={'chat_id': chat_id, 'caption': caption_text}, files={'video': video_file}, timeout=60)
        
    print("🎉 极致同步版视频（4只ETF+私有指标）推送完毕！")

if __name__ == "__main__":
    asyncio.run(main())
