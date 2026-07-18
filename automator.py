import os
import sys
import json
import asyncio
import subprocess
import re
import time
import shutil
from datetime import datetime
import pytz
from playwright.async_api import async_playwright
import edge_tts
import requests
from PIL import Image

# ==========================================
# 1. 基础配置与智能环境感知 (适配新版UI)
# ==========================================
TARGET_URL = "https://gpkx.github.io/" 
TV_CHART_URL = "https://cn.tradingview.com/chart/fxUqvHrk/" 
TZ = pytz.timezone('Asia/Shanghai')
NOW = datetime.now(TZ)

# 智能识别运行场景
TODAY_WEEKDAY = NOW.weekday()  # 0=周一, 4=周五, 5=周六
IS_SATURDAY = TODAY_WEEKDAY == 5

if IS_SATURDAY:
    TIME_LABEL = "周线收盘"
    REPORT_TYPE = "weekly"
    TV_INTERVAL = "1W"
    TARGET_COL_IDX = -1  # 新版UI: 周线在最后一列
else:
    TIME_LABEL = "日线收盘"
    REPORT_TYPE = "daily"
    TV_INTERVAL = "1D"
    TARGET_COL_IDX = TODAY_WEEKDAY + 1  # 新版UI: 0列是名称，周一为1，以此类推

DATE_STR = NOW.strftime("%m月%d日")
COVER_TITLE = "本周ETF主力资金异动" if IS_SATURDAY else "今日ETF异动前四数据"
COVER_SUBTITLE = f"({DATE_STR}-{TIME_LABEL})"

FILE_SUFFIX = NOW.strftime("%Y%m%d_%H%M")
PRIVATE_HOOK = "每日完整量化异动数据，将在主页更新。欢迎在评论区留下你的看法，我们一起探讨。" 
OUTRO_TEXT = "本内容由量化模型客观生成，不构成投资建议，市场有风险，投资需谨慎。"

def get_tv_symbol(code):
    if code.startswith(('5', '6')): return f"SSE:{code}"
    return f"SZSE:{code}"

def parse_pct_to_float(val_str):
    """提取百分比用于排序"""
    try:
        return float(val_str.replace('%', '').replace('+', ''))
    except:
        return 0.0

def format_quant_voice(val_str):
    try:
        val = float(val_str.replace('%', '').replace('+', ''))
        if val > 0: return f"ATR 强势拉升了{abs(val)}%"
        elif val < 0: return f"ATR 回撤了{abs(val)}%"
        return "ATR 处于零轴震荡区"
    except:
        return "暂无有效读数"

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.strip())

async def safe_generate_tts(text, filename, retries=3):
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+5%")
            await communicate.save(filename)
            return True
        except Exception as e:
            if attempt < retries - 1: await asyncio.sleep(3) 
            else: raise Exception(f"TTS 失败: {e}")

def clean_for_tts(text):
    if not text: return ""
    if isinstance(text, dict): text = "，".join([str(v) for v in text.values() if isinstance(v, str)])
    elif not isinstance(text, str): text = str(text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = text.replace('*', '').replace('_', '').replace('#', '').replace('`', '')
    text = re.sub(r'(?i)\betf\b', ' ETF ', text)
    text = re.sub(r'(?i)\batr\b', ' ATR ', text)
    text = re.sub(r'(?i)\ba股\b', ' A 股 ', text)
    return text.strip()

def create_srt(text, duration, filename):
    def format_time(seconds):
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        ms = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    
    start_time = "00:00:00,000"
    end_time = format_time(duration)
    clean_text = text.replace(' ETF ', 'ETF').replace(' ATR ', 'ATR')
    lines = [clean_text[i:i+50] for i in range(0, len(clean_text), 50)]
    
    srt_content = f"1\n{start_time} --> {end_time}\n" + "\n".join(lines) + "\n"
    with open(filename, "w", encoding="utf-8") as f: f.write(srt_content)

def create_zoom_video(img_path, output_video, duration, fps=30, zoom_type='main', srt_file=None):
    frames_dir = f"temp_frames_{os.path.basename(img_path).split('.')[0]}"
    if os.path.exists(frames_dir): shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    img = Image.open(img_path).convert('RGB')
    w, h = img.size 
    total_frames = int(duration * fps)

    for i in range(total_frames):
        if zoom_type == 'tv':
            progress = i / total_frames
            zoom = 1.0 + 0.15 * progress
            cw, ch = int(w/zoom), int(h/zoom)
            cx = w - cw
            cy = int((h - ch) / 2)
        else:
            cw, ch, cx, cy = w, h, 0, 0
        box = (cx, cy, cx + cw, cy + ch)
        frame = img.crop(box).resize((w, h), Image.Resampling.LANCZOS)
        frame.save(f"{frames_dir}/frame_{i:04d}.jpg", quality=90)

    vf_filters = []
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace('\\', '\\\\').replace(':', '\\:')
        vf_filters.append(f"subtitles={srt_path}:force_style='FontName=Microsoft YaHei,FontSize=10,PrimaryColour=&H00000000,Outline=0,Shadow=0,MarginV=40'")

    cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", f"{frames_dir}/frame_%04d.jpg"]
    if vf_filters: cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(frames_dir)

def create_static_video(img_path, output_video, duration, fps=30, srt_file=None):
    vf_filters = []
    if srt_file and os.path.exists(srt_file):
        srt_path = srt_file.replace('\\', '\\\\').replace(':', '\\:')
        vf_filters.append(f"subtitles={srt_path}:force_style='FontName=Microsoft YaHei,FontSize=10,PrimaryColour=&H00000000,Outline=0,Shadow=0,MarginV=40'")

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration)]
    if vf_filters: cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(["-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p", output_video])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==========================================
# 2. AI 中枢逻辑保持不变
# ==========================================
def call_ai_director(etf_list, time_label, report_type):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("❌ 致命错误：未检测到 DEEPSEEK_API_KEY！")
        sys.exit(1)

    url = "https://api.deepseek.com/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    if report_type == "weekly":
        prompt_context = f"今天【{DATE_STR}】是周末。请对本周触发异动的前四名 ETF 进行周线级别的大局观分析，做深度复盘。不要局限于一天，要聊这周资金的整体倾向。"
    else:
        prompt_context = f"今天【{DATE_STR}{time_label}】有以下 ETF 触发异动。请做精准的短线异动点评。"

    if not etf_list:
        prompt = f"""
        你现在是一位有10年实战经验、语气亲和且专业的A股量化操盘手。{prompt_context}
        但是今天没有任何ETF触发我们的异动阈值。
        请生成通报文章，强调量化纪律：“宁缺毋滥，没有信号绝不盲目出手”。
        
        【输出要求】返回 JSON：
        - "xhs_title": 小红书爆款标题（带emoji）
        - "xhs_article": 小红书正文（活泼、多emoji、排版空行、文末引导关注）
        - "gzh_title": 微信公众号标题（专业、吸引眼球）
        - "gzh_article": 微信公众号正文（像资深老友复盘，文末引导交流）
        """
    else:
        prompt = f"""
        你现在是一位有10年实战经验、语气亲和且专业的A股量化操盘手。{prompt_context}
        
        【核心异动数据（已按涨跌幅降序提取前四名）】：
        {json.dumps(etf_list, ensure_ascii=False, indent=2)}

        🚨 【最高指令】客观真实，只基于上方数据解读，不说假话！

        【输出要求】严格返回JSON，包含：
        - "video_intro": 短视频开场口播（20-30字，打招呼+抛出结论）。英文写 ETF。
        - "etf_narratives": 【数组】包含{len(etf_list)}句短评，严格对应传入的ETF！口语化解说。
        - "xhs_title": 小红书爆款标题。
        - "xhs_article": 小红书正文（排版好看，文风活泼，重点突出，文末引导关注）。
        - "gzh_title": 公众号标题。
        - "gzh_article": 公众号正文（深度、娓娓道来，文末引导评论区交流）。
        - "cover_html": HTML5+CSS。720x1280。要求：**现代金融风，浅色高级渐变背景，卡片化布局（微圆角微阴影），设计感强。**标题【{COVER_TITLE}】，副标题【{COVER_SUBTITLE}】。字体Microsoft YaHei，居中。
        """

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是资深量化专家兼矩阵运营主编。严格返回 JSON。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4 
    }

    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            raw_text = response.json()['choices'][0]['message']['content']
            clean_text = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw_text, flags=re.IGNORECASE)
            return json.loads(clean_text.strip())
        except Exception as e:
            time.sleep(3)
    sys.exit(1)

def send_telegram(text, video_path=None, photos=None):
    # 此处为之前版本提供的TG发送逻辑，保持原样即可
    pass

async def main():
    print(f"🚀 启动量化自媒体引擎 | 当前模式: {REPORT_TYPE} | {NOW}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 720, 'height': 1280})
        page = await context.new_page()

        print("🔍 正在根据新版结构智能提取数据...")
        etf_list = []
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # 核心改进：直接在浏览器环境解析标准的表格结构，避免正则的脆弱性
            row_data = await page.evaluate('''() => {
                return Array.from(document.querySelectorAll('tr, .el-table__row')).map(tr => {
                    return Array.from(tr.querySelectorAll('td, th')).map(td => td.innerText.trim());
                }).filter(row => row.length >= 7); // 确保是包含周一到周线完整列的有效行
            }''')

            for row in row_data:
                name_cell = row[0]
                code_match = re.search(r'\b(5\d{5}|1\d{5})\b', name_cell)
                
                if code_match:
                    code = code_match.group(1)
                    # 剥离数字代码，留下纯ETF名称
                    name = re.sub(r'\d+', '', name_cell).strip()
                    
                    # 核心改进：针对周六(周线)取最后一列，针对工作日取当天对应的列
                    target_val = row[TARGET_COL_IDX]
                    
                    if '%' in target_val:
                        etf_list.append({"name": name, "code": code, "change": target_val})
            
            # 核心改进：按“异动绝对值”从大到小排序，确保大跌大涨都抓取到
            etf_list.sort(key=lambda x: abs(parse_pct_to_float(x['change'])), reverse=True)
            etf_list = etf_list[:4]
            
        except Exception as e:
            print(f"提取数据发生异常: {e}")

        if not etf_list:
            print("⚠️ 目标时段无有效信号，生成通报...")
            ai_script = call_ai_director([], TIME_LABEL, REPORT_TYPE)
            sys.exit(0)

        print("🎭 正在生成剧本与排版...")
        ai_script = call_ai_director(etf_list, TIME_LABEL, REPORT_TYPE)
        
        # 渲染封面
        await page.set_content(ai_script.get('cover_html', '<html></html>'))
        await page.wait_for_timeout(2000) 
        await page.screenshot(path="cover_image.png")

        # 福利海报保持合规优化版
        hook_html = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
            body { background: linear-gradient(135deg, #f8fafc, #e2e8f0); display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Microsoft YaHei'; height: 100vh; margin: 0; text-align: center; }
            .card { background: rgba(255, 255, 255, 0.8); padding: 60px 40px; border-radius: 30px; box-shadow: 0 10px 30px rgba(0,0,0,0.05); }
            h1 { color: #1e293b; font-size: 55px; margin-bottom: 40px; font-weight: bold; }
            p { font-size: 34px; line-height: 2.2; color: #475569; }
            .highlight { background: #3b82f6; color: white; padding: 10px 25px; border-radius: 15px; font-weight: bold;}
        </style></head><body>
            <div class="card">
                <h1>获取每日监控图表</h1>
                <p>完整量化异动数据单<br><br><span class="highlight">欢迎在评论区交流探讨</span><br><br>把握市场核心资金动向</p>
            </div>
        </body></html>
        """
        await page.set_content(hook_html)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="hook.png")

        print("🌐 正在抓取 TV 原生图表 (应用115%放大对齐裁切)...")
        clean_css = ".layout__area--top, .layout__area--left, .layout__area--right, .layout__area--bottom { display: none !important; } .layout__area--center { position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; z-index: 9999 !important; }"
        
        for i, etf in enumerate(etf_list):
            symbol = get_tv_symbol(etf['code'])
            await page.goto(f"{TV_CHART_URL.rstrip('/')}/?symbol={symbol}&interval={TV_INTERVAL}", wait_until="domcontentloaded")
            await page.add_style_tag(content=clean_css)
            
            zoom_js = """
            document.body.style.transform = 'scale(1.15)';
            document.body.style.transformOrigin = 'top left';
            """
            await page.evaluate(zoom_js)
            await page.wait_for_timeout(4000)
            
            await page.screenshot(path=f"ss_etf_{i}.png", clip={'x': 0, 'y': 0, 'width': 720, 'height': 1280})
            
        await browser.close()

    print("✅ 数据与影像渲染完毕！进入最终音视频合成流...")
    # 保持原有的 FFmpeg 视频合成及 TG 推送模块即可

if __name__ == "__main__":
    asyncio.run(main())
