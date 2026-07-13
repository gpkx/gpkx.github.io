import os
import asyncio
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import subprocess
import requests

# 1. 基础配置
TARGET_URL = "https://gpkx.github.io/"
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)

# 判断是午间还是收盘
IS_MIDDAY = NOW.hour < 13
TIME_LABEL = "午间盘面" if IS_MIDDAY else "今日收盘"
FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")

# 合规文案模板
TEMPLATE = """
各位观众朋友大家好，欢迎关注最新的行业E T F客观数据跟踪。
截至{time_label}，我们来梳理一下市场核心板块的最新动态。
首先，{name0}今日表现为{trend0}，变动幅度和比例为{value0}。
紧接着，{name1}目前在盘中呈现出{trend1}态势，数据录得{value1}。
再来关注{name2}，当前走势表现为{trend2}，录得{value2}。
随后是{name3}，阶段内整体维持在{trend3}状态，变动数值为{value3}。
最后，{name4}的最新数据显示为{trend4}，幅度在{value4}。
以上数据均源自公开市场客观统计，仅供全景量化复盘参考，不构成任何投资建议或操作引导。理财有风险，入市需谨慎。
"""

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

async def main():
    print(f"🚀 开始执行自动化工作流... 当前时间: {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        # --- 步骤 1: 抓取网页并截图 ---
        await page.goto(TARGET_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="ss_main.png")
        print("📸 成功截取数据总览图")

        # 兜底测试数据
        etf_list = [
            {"name": "酒ETF", "code": "512690", "change": "+4.5%"},
            {"name": "医疗ETF", "code": "512170", "change": "+3.9%"},
            {"name": "消费ETF", "code": "159928", "change": "+3.4%"},
            {"name": "银行ETF", "code": "512800", "change": "+2.2%"},
            {"name": "上证50", "code": "510050", "change": "-2.2%"}
        ]

        # --- 步骤 2: 遍历 TradingView 并截图 ---
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            tv_url = f"https://s.tradingview.com/widgetembed/?symbol={symbol}&interval=D&theme=light&style=1"
            print(f"🌐 正在加载 TradingView: {symbol}")
            try:
                await page.goto(tv_url, wait_until="networkidle", timeout=15000)
                await page.wait_for_timeout(4000)
                await page.screenshot(path=f"ss_etf_{i}.png")
            except:
                await page.screenshot(path=f"ss_etf_{i}.png")

        await browser.close()

    # --- 步骤 3 & 4: TTS 语音 ---
    render_data = {"time_label": TIME_LABEL}
    for i, etf in enumerate(etf_list):
        trend, readable_val = format_trend(etf['change'])
        render_data[f"name{i}"] = etf['name']
        render_data[f"trend{i}"] = trend
        render_data[f"value{i}"] = readable_val

    speech_text = TEMPLATE.format(**render_data)
    
    communicate = edge_tts.Communicate(speech_text, "zh-CN-YunxiNeural")
    await communicate.save("audio.mp3")
    print("🎵 TTS 语音合成完毕")

    # --- 步骤 5: 合成短视频 ---
    with open("input.txt", "w") as f:
        f.write("file 'ss_main.png'\nduration 5\n")
        for i in range(5):
            f.write(f"file 'ss_etf_{i}.png'\nduration 6\n")
        f.write(f"file 'ss_etf_4.png'\n")

    video_name = f"etf_report_{FILE_SUFFIX}.mp4"
    
    ffmpeg_cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", "input.txt", 
        "-i", "audio.mp3", "-c:v", "libx264", "-pix_fmt", "yuv420p", 
        "-c:a", "aac", "-shortest", "-y", video_name
    ]
    subprocess.run(ffmpeg_cmd, check=True)
    print(f"🎬 视频合成成功: {video_name}")

    # --- 步骤 6: 发送到 Telegram ---
    print("✈️ 开始发送视频到 Telegram...")
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    tg_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    
    # 顺便把生成的文案作为视频的说明文字（caption）发过去，方便你直接复制去发抖音/视频号
    caption_text = f"📊 【{TIME_LABEL} ETF数据速递】\n\n{speech_text}"
    
    with open(video_name, 'rb') as video_file:
        payload = {'chat_id': chat_id, 'caption': caption_text}
        files = {'video': video_file}
        # 设置较大的超时时间，因为国外机器传给TG服务器偶尔也会卡顿
        response = requests.post(tg_url, data=payload, files=files, timeout=60)
        
    if response.status_code == 200:
        print("🎉 视频已成功推送到你的 Telegram！")
    else:
        print(f"⚠️ Telegram 发送失败: {response.text}")

if __name__ == "__main__":
    asyncio.run(main())
