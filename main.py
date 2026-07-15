import os
import asyncio
import requests
import google.generativeai as genai
import edge_tts
from playwright.async_api import async_playwright
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

# ==========================================
# 环境变量配置 (由 GitHub Actions 运行时动态注入)
# ==========================================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
TV_SESSION_ID = os.getenv("TV_SESSION_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 初始化 Gemini 大模型
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
else:
    print("警告: 未检测到 GEMINI_API_KEY")

# ==========================================
# 模块1: 核心数据抓取与极值解析
# ==========================================
def fetch_and_analyze_data():
    """
    请求 Cloudflare Worker 接口，解析 JSON 并提取当日 ATR 触发极值的 ETF。
    """
    api_url = "https://etf.hahagw.eu.org"
    print(f"正在向 {api_url} 请求实时监控数据...")
    
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status() 
        raw_data = response.json() 
        
        top_drop_etf = {"name": "", "code": "", "am_val": 0.0, "raw_am": ""}
        top_rise_etf = {"name": "", "code": "", "am_val": 0.0, "raw_am": ""}
        
        for item in raw_data:
            am_status = item.get("am_status")
            
            # 边界条件过滤
            if am_status is not None and isinstance(am_status, str) and "%" in am_status:
                val_str = am_status.replace("%", "").replace("+", "")
                try:
                    float_val = float(val_str)
                except ValueError:
                    continue
                    
                if float_val < top_drop_etf["am_val"]:
                    top_drop_etf["am_val"] = float_val
                    top_drop_etf["name"] = item.get("etf_name", "")
                    top_drop_etf["code"] = item.get("etf_code", "")
                    top_drop_etf["raw_am"] = am_status
                    
                if float_val > top_rise_etf["am_val"]:
                    top_rise_etf["am_val"] = float_val
                    top_rise_etf["name"] = item.get("etf_name", "")
                    top_rise_etf["code"] = item.get("etf_code", "")
                    top_rise_etf["raw_am"] = am_status

        current_date = raw_data[0].get("date", "") if raw_data else "今日"
        
        return {
            "date": current_date,
            "top_drop_name": top_drop_etf["name"],
            "top_drop_code": top_drop_etf["code"],
            "top_drop_am": top_drop_etf["raw_am"],
            "top_rise_name": top_rise_etf["name"],
            "top_rise_code": top_rise_etf["code"],
            "top_rise_am": top_rise_etf["raw_am"]
        }
    except Exception as e:
        print(f"数据请求或解析错误: {e}")
        return None

# ==========================================
# 模块2: AI 文案生成与 Edge-TTS 语音合成
# ==========================================
async def generate_script_and_audio(data):
    """
    调用 Gemini 生成极具情绪张力的短视频口播文案，并转换为音频。
    """
    print("正在调用 Gemini 生成文案...")
    prompt = f"""
    你是一个资深的金融短视频运营。请根据以下客观数据，写一段30秒左右的短视频口播文案。
    要求：用词犀利、有情绪拉扯，强调“拒绝盲目盯盘，相信量化系统”。
    今日数据：
    - 跌幅极值触发：{data['top_drop_name']}，上午跌幅高达 {data['top_drop_am']}。
    - 涨幅极值触发：{data['top_rise_name']}，上午飙升 {data['top_rise_am']}。
    结尾引导观众在评论区留言“监控”获取系统。不要任何特殊符号。
    """
    
    try:
        response = model.generate_content(prompt)
        script_text = response.text.replace("*", "").replace("#", "")
        print(f"生成的文案: \n{script_text}")
    except Exception as e:
        print(f"Gemini 生成失败，使用备用文案。错误: {e}")
        script_text = f"注意看，散户还在恐慌，但我们的ATR系统已经抓到极值。今天{data['top_drop_name']}杀跌{data['top_drop_am']}，而{data['top_rise_name']}强势拉升{data['top_rise_am']}。拒绝情绪交易，评论区留言监控，获取实时数据表。"

    print("正在合成语音...")
    audio_path = "voiceover.mp3"
    communicate = edge_tts.Communicate(script_text, "zh-CN-YunxiNeural")
    await communicate.save(audio_path)
    return audio_path

# ==========================================
# 模块3: 自动化截图 (Playwright 注入 Cookie)
# ==========================================
async def capture_screenshots(data):
    """
    模拟浏览器登录 TradingView，截取带有 ATR 标签的图表。
    """
    print("正在启动无头浏览器截图...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        
        # 注入你的 TradingView Session 以绕过登录并加载自定义指标
        await context.add_cookies([{
            "name": "sessionid",
            "value": TV_SESSION_ID,
            "domain": ".tradingview.com",
            "path": "/"
        }])
        
        page = await context.new_page()
        
        img_paths = []
        # 截取跌幅最大的 ETF
        if data['top_drop_code']:
            url = f"https://cn.tradingview.com/chart/fxUqvHrk/?symbol={data['top_drop_code']}"
            await page.goto(url)
            await page.wait_for_timeout(5000) # 等待图表和指标完全加载
            path = "drop_etf.png"
            await page.screenshot(path=path)
            img_paths.append(path)
            
        # 截取涨幅最大的 ETF
        if data['top_rise_code']:
            url = f"https://cn.tradingview.com/chart/fxUqvHrk/?symbol={data['top_rise_code']}"
            await page.goto(url)
            await page.wait_for_timeout(5000)
            path = "rise_etf.png"
            await page.screenshot(path=path)
            img_paths.append(path)
            
        await browser.close()
    return img_paths

# ==========================================
# 模块4: 视频合成与渲染
# ==========================================
def render_video(img_paths, audio_path, output_path="final_video.mp4"):
    """
    将图像和音频合成 MP4 文件。
    """
    print("正在渲染最终视频...")
    if not img_paths:
        print("没有可用的截图，停止渲染。")
        return None
        
    audio = AudioFileClip(audio_path)
    audio_duration = audio.duration
    
    # 动态计算每张图片的展示时间
    clip_duration = audio_duration / len(img_paths)
    clips = []
    
    for img in img_paths:
        clip = ImageClip(img).set_duration(clip_duration)
        clips.append(clip)
        
    video = concatenate_videoclips(clips, method="compose")
    video = video.set_audio(audio)
    
    video.write_videofile(
        output_path, 
        fps=24, 
        codec="libx264", 
        audio_codec="aac"
    )
    return output_path

# ==========================================
# 模块5: Telegram 推送
# ==========================================
def send_to_telegram(video_path):
    """
    发送生成的视频到指定 TG 机器人。
    """
    if not video_path or not os.path.exists(video_path):
        print("未找到视频文件，取消推送。")
        return

    print("正在推送到 Telegram...")
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendVideo"
    
    with open(video_path, 'rb') as video:
        files = {'video': video}
        data = {'chat_id': TG_CHAT_ID, 'caption': "🤖 您的今日 ATR 动态监控短视频已生成，请查收！"}
        response = requests.post(url, files=files, data=data)
    
    if response.status_code == 200:
        print("Telegram 推送成功！")
    else:
        print(f"推送失败: {response.text}")

# ==========================================
# 主控枢纽
# ==========================================
async def main():
    try:
        # 1. 获取并清洗数据
        data = fetch_and_analyze_data()
        if not data or (not data['top_drop_code'] and not data['top_rise_code']):
            print("未能提取到有效数据，工作流终止。")
            return
            
        # 2. 生成文案与配音
        audio_path = await generate_script_and_audio(data)
        
        # 3. 截取图表
        img_paths = await capture_screenshots(data)
        
        # 4. 视频剪辑
        video_path = render_video(img_paths, audio_path)
        
        # 5. TG 消息分发
        send_to_telegram(video_path)
        
        print("全自动工作流执行完毕。")
    except Exception as e:
        print(f"主程序运行发生错误: {e}")

if __name__ == "__main__":
    asyncio.run(main())
