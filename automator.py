import os
import asyncio
import subprocess
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests
import json

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/"
TV_CHART_URL = "https://cn.tradingview.com/chart/" # 默认主图表URL
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午间盘面" if IS_MIDDAY else "今日收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

# 分段文案模板
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

# 生成独立视频片段工具
def create_video_segment(image_paths, audio_path, output_path):
    duration = get_audio_duration(audio_path)
    img_count = len(image_paths)
    time_per_img = duration / img_count
    
    # 动态生成 FFmpeg 的 input.txt
    txt_path = f"temp_{output_path}.txt"
    with open(txt_path, "w") as f:
        for img in image_paths:
            f.write(f"file '{img}'\nduration {time_per_img:.2f}\n")
        f.write(f"file '{image_paths[-1]}'\n") # 结尾收尾帧
    
    # 将图片列表与对应音频合并为一个独立 MP4
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", txt_path, 
        "-i", audio_path, "-c:v", "libx264", "-pix_fmt", "yuv420p", 
        "-c:a", "aac", "-shortest", "-y", output_path
    ]
    subprocess.run(cmd, check=True)
    os.remove(txt_path)

async def main():
    print(f"🚀 开始执行进阶同步工作流... {NOW}")
    
    # --- A. 数据抓取与图表截图 ---
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        
        # 注入 TradingView Cookie，实现自动登录
        tv_session = os.getenv('TV_SESSION_ID')
        if tv_session:
            await context.add_cookies([{
                'name': 'sessionid', 'value': tv_session,
                'domain': '.tradingview.com', 'path': '/'
            }])
        
        page = await context.new_page()
        
        # 1. 抓取总览图
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="ss_main.png")
        
        etf_list = [
            {"name": "酒ETF", "code": "512690", "change": "+4.5%"},
            {"name": "医疗ETF", "code": "512170", "change": "+3.9%"},
            {"name": "消费ETF", "code": "159928", "change": "+3.4%"},
            {"name": "银行ETF", "code": "512800", "change": "+2.2%"},
            {"name": "上证50", "code": "510050", "change": "-2.2%"}
        ]

        # 2. 抓取专属带指标图表
        print("🌐 登录并加载私有图表...")
        await page.goto(TV_CHART_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(5000) # 等待各种指标彻底加载完毕

        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            print(f"📸 正在抓取: {symbol} (3小时与日线)")
            
            # 键盘自动化：输入代码并回车，调用相应的股票
            await page.keyboard.type(symbol)
            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(3000) # 等待K线刷新
            
            # 键盘自动化：输入 180 切换到 3小时线
            await page.keyboard.type("180")
            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)
            await page.screenshot(path=f"ss_etf_{i}_3h.png")
            
            # 键盘自动化：输入 1D 切换到 日线
            await page.keyboard.type("1D")
            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)
            await page.screenshot(path=f"ss_etf_{i}_1d.png")

        await browser.close()

    # --- B. 语音分段生成与绝对同步融合 ---
    print("🎵 开始分段生成 TTS 语音并同步画面...")
    video_segments = []
    
    # 1. 制作开头
    intro_tts = edge_tts.Communicate(INTRO_TEXT, "zh-CN-YunxiNeural")
    await intro_tts.save("audio_intro.mp3")
    create_video_segment(["ss_main.png"], "audio_intro.mp3", "seg_intro.mp4")
    video_segments.append("seg_intro.mp4")
    full_text = INTRO_TEXT + "\n"

    # 2. 制作中间每个 ETF 的片段
    transition_words = ["首先，", "紧接着，", "再来关注", "随后是", "最后，"]
    for i, etf in enumerate(etf_list):
        trend, readable_val = format_trend(etf['change'])
        etf_text = f"{transition_words[i]}{etf['name']}今日表现为{trend}，变动幅度和比例为{readable_val}。"
        full_text += etf_text + "\n"
        
        etf_audio = f"audio_etf_{i}.mp3"
        etf_tts = edge_tts.Communicate(etf_text, "zh-CN-YunxiNeural")
        await etf_tts.save(etf_audio)
        
        # 重点：这一句语音内，均分展示 3小时线和日线
        create_video_segment([f"ss_etf_{i}_3h.png", f"ss_etf_{i}_1d.png"], etf_audio, f"seg_etf_{i}.mp4")
        video_segments.append(f"seg_etf_{i}.mp4")

    # 3. 制作结尾
    outro_tts = edge_tts.Communicate(OUTRO_TEXT, "zh-CN-YunxiNeural")
    await outro_tts.save("audio_outro.mp3")
    create_video_segment(["ss_main.png"], "audio_outro.mp3", "seg_outro.mp4")
    video_segments.append("seg_outro.mp4")
    full_text += OUTRO_TEXT

    # --- C. 无缝拼接所有分段并发送 ---
    print("🎬 开始无缝拼接最终视频...")
    with open("concat_list.txt", "w") as f:
        for seg in video_segments:
            f.write(f"file '{seg}'\n")
            
    final_video = f"etf_report_sync_{FILE_SUFFIX}.mp4"
    concat_cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", "concat_list.txt", "-c", "copy", "-y", final_video]
    subprocess.run(concat_cmd, check=True)
    
    print("✈️ 开始推送到 Telegram...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    tg_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    caption_text = f"📊 【{TIME_LABEL} 深度复盘】\n\n{full_text}"
    
    with open(final_video, 'rb') as video_file:
        requests.post(tg_url, data={'chat_id': chat_id, 'caption': caption_text}, files={'video': video_file}, timeout=60)
        
    print("🎉 高级同步版视频推送完毕！")

if __name__ == "__main__":
    asyncio.run(main())
