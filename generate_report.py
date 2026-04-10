#!/usr/bin/env python3
"""
每日日记报告生成器
- 从 Notion 拉取当天日记中心及所有关联数据
- 生成结构化 Markdown 报告
- 发送到 Get 笔记
"""

import json
import os
import sys
import random
import re
import hashlib
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import ssl

# ============ 配置 ============
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"

GETNOTE_API_KEY = os.environ.get("GETNOTE_API_KEY", "")
GETNOTE_CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "cli_3802f9db08b811f197679c63c078bacc")

# Notion 数据库 ID
DIARY_CENTER_DB = "4e6607f4-7140-4317-8fc9-d52102337869"  # 日记中心
WEATHER_DB = "33e33b33-7f23-8185-a733-fbde3544a1dd"       # 天气记录
FRAGMENT_DB = "11233b33-7f23-8024-9555-cb8de8c58e02"      # 碎片中心

# 播客筛选阈值（秒）
PODCAST_MIN_LISTEN_SECONDS = 600  # 10 分钟

# 碎片回顾：优先选取的标签（有深度的内容）
FRAGMENT_PRIORITY_TAGS = ["类别/领悟", "类别/思考", "类别/反思", "类别/困惑", "类别/灵感"]
# 每日碎片回顾数量
FRAGMENT_PICK_COUNT = 2


# ============ Notion API ============

def notion_request(url, body=None):
    data = json.dumps(body).encode() if body else None
    method = "POST" if data else "GET"
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    if data:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"[ERROR] Notion API error {e.code}: {e.read().decode()[:200]}")
        return None
    except Exception as e:
        print(f"[ERROR] Request failed: {e}")
        return None


def query_database(db_id, filter_body=None, page_size=100):
    body = {"page_size": page_size}
    if filter_body:
        body["filter"] = filter_body
    
    results = []
    while True:
        data = notion_request(f"https://api.notion.com/v1/databases/{db_id}/query", body)
        if not data:
            break
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return results


def get_page(page_id):
    return notion_request(f"https://api.notion.com/v1/pages/{page_id}")


def get_page_blocks(page_id):
    results = []
    start_cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if start_cursor:
            url += f"&start_cursor={start_cursor}"
        data = notion_request(url)
        if not data:
            break
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        start_cursor = data["next_cursor"]
    return results


# ============ 辅助函数 ============

def get_rich_text(props, key):
    val = props.get(key)
    if not val:
        return ""
    if val["type"] == "rich_text":
        return "".join([t["plain_text"] for t in val.get("rich_text", [])])
    if val["type"] == "title":
        return "".join([t["plain_text"] for t in val.get("title", [])])
    return ""


def get_title(props):
    for key, val in props.items():
        if val["type"] == "title":
            return "".join([t["plain_text"] for t in val["title"]])
    return ""


def get_select_name(props, key):
    sel = props.get(key, {}).get("select")
    return sel["name"] if sel else ""


def get_multi_select_names(props, key):
    return [s["name"] for s in props.get(key, {}).get("multi_select", [])]


def get_number(props, key):
    return props.get(key, {}).get("number")


def get_prop(props, key):
    """获取属性值"""
    val = props.get(key)
    if not val:
        return ""
    t = val["type"]
    if t == "rich_text":
        return "".join([x["plain_text"] for x in val.get("rich_text", [])])
    if t == "title":
        return "".join([x["plain_text"] for x in val.get("title", [])])
    if t == "select":
        return val.get("select", {}).get("name", "") if val.get("select") else ""
    if t == "multi_select":
        return ", ".join([x["name"] for x in val.get("multi_select", [])])
    if t == "number":
        return str(val.get("number", ""))
    if t == "checkbox":
        return "✅" if val.get("checkbox") else "⬜"
    return ""


def format_duration(seconds):
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m}m"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m{s}s"


def format_duration_cn(seconds):
    """中文时长格式"""
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}小时{m}分钟"
    else:
        m = seconds // 60
        return f"{m}分钟"


# ============ 数据拉取 ============

def get_yesterday():
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def get_diary_entry(date_str):
    results = query_database(DIARY_CENTER_DB, {
        "property": "日期",
        "date": {"equals": date_str}
    })
    return results[0] if results else None


def get_related_pages(diary_props, relation_key):
    rels = diary_props.get(relation_key, {}).get("relation", [])
    if not rels:
        return []
    pages = []
    for rel in rels:
        page = get_page(rel["id"])
        if page:
            pages.append(page)
    return pages


def get_weather_for_date(date_str):
    """通过天气记录数据库按日期查询天气"""
    results = query_database(WEATHER_DB, {
        "property": "日期",
        "date": {"equals": date_str}
    })
    return results


def get_block_text(page_id):
    """从页面 blocks 中提取文本"""
    blocks = get_page_blocks(page_id)
    texts = []
    for block in blocks:
        bt = block["type"]
        if bt in ["paragraph", "heading_1", "heading_2", "heading_3",
                   "bulleted_list_item", "numbered_list_item", "quote", "toggle"]:
            content = "".join([t["plain_text"] for t in block.get(bt, {}).get("rich_text", [])])
            if content:
                texts.append(content)
        elif bt == "callout":
            content = "".join([t["plain_text"] for t in block.get("callout", {}).get("rich_text", [])])
            if content:
                texts.append(content)
    return texts


# ============ 报告模块 ============

def build_weather_section(diary_props, date_str):
    """天气模块：优先从日记中心属性取，再从天气记录数据库查"""
    lines = ["## ☁️ 天气\n"]
    
    # 方法1：日记中心的天气 select 属性
    weather_select = get_select_name(diary_props, "天气")
    if weather_select:
        lines.append(f"**{weather_select}**")
    
    # 方法2：从天气记录数据库查询
    weather_records = get_weather_for_date(date_str)
    if weather_records:
        for wr in weather_records:
            wprops = wr["properties"]
            city = get_rich_text(wprops, "城市") or "未知"
            climate = get_select_name(wprops, "气候")
            temp = get_number(wprops, "气温")
            humidity = get_number(wprops, "湿度")
            wind = get_number(wprops, "风速")
            uv = get_number(wprops, "紫外线")
            tags = get_multi_select_names(wprops, "天气标签")
            
            parts = [f"**{city}**"]
            if climate:
                parts.append(climate)
            if temp is not None:
                parts.append(f"{temp}°C")
            if humidity is not None:
                parts.append(f"湿度 {humidity}%")
            if wind is not None:
                parts.append(f"风速 {wind}")
            if uv is not None:
                parts.append(f"紫外线 {uv}")
            if tags:
                parts.append(" ".join(tags))
            
            lines.append(" · ".join(parts))
    
    if len(lines) == 1:
        return ""  # 没有天气数据
    
    lines.append("")
    return "\n".join(lines)


def build_sleep_section(sleep_pages):
    if not sleep_pages:
        return ""
    
    lines = ["## 😴 睡眠\n"]
    for sp in sleep_pages:
        props = sp["properties"]
        duration = get_number(props, "睡眠时长")
        quality = get_select_name(props, "睡眠质量")
        energy = get_select_name(props, "能量水平")
        wake_state = get_select_name(props, "起床状态")
        dream = get_rich_text(props, "梦境记录")
        
        if duration is not None:
            h = int(duration)
            m = int((duration - h) * 60)
            lines.append(f"- **睡眠时长**：{h}小时{m}分钟")
        if quality and quality != "未选择":
            lines.append(f"- **睡眠质量**：{quality}")
        if energy and energy != "未选择":
            lines.append(f"- **能量水平**：{energy}")
        if wake_state and wake_state != "未选择":
            lines.append(f"- **起床状态**：{wake_state}")
        if dream:
            lines.append(f"- **梦境**：{dream}")
        
        # 从页面 blocks 中提取额外信息
        block_texts = get_block_text(sp["id"])
        for text in block_texts:
            if text not in lines:  # 避免重复
                lines.append(f"- {text}")
    
    # 过滤空行
    if len(lines) <= 1:
        return ""
    
    lines.append("")
    return "\n".join(lines)


def build_tasks_section(task_pages):
    if not task_pages:
        return ""
    
    lines = [f"## ✅ 任务与事件（{len(task_pages)}项）\n"]
    
    for tp in task_pages:
        props = tp["properties"]
        title = get_title(props)
        if not title:
            continue
        priority = get_rich_text(props, "优先级")
        status = get_rich_text(props, "状态")
        
        if status and status not in ["", "——", "——————————————————"]:
            lines.append(f"- [{status}] {title}")
        elif priority and priority not in ["", "——", "——————————————————"]:
            lines.append(f"- {title} *({priority})*")
        else:
            lines.append(f"- {title}")
    
    lines.append("")
    return "\n".join(lines)


def build_podcast_section(episode_pages):
    if not episode_pages:
        return ""
    
    serious = []
    skipped = []
    
    for ep in episode_pages:
        props = ep["properties"]
        title = get_title(props)
        progress = get_number(props, "收听进度") or 0
        duration = get_number(props, "时长") or 0
        rating = get_select_name(props, "评分")
        desc = get_rich_text(props, "Description")
        
        if progress >= PODCAST_MIN_LISTEN_SECONDS:
            pct = int(progress / duration * 100) if duration > 0 else 0
            serious.append({
                "title": title,
                "progress": progress,
                "duration": duration,
                "percentage": pct,
                "rating": rating,
                "desc": desc
            })
        else:
            skipped.append(title)
    
    if not serious and not skipped:
        return ""
    
    lines = ["## 🎧 播客\n"]
    
    for ep in serious:
        lines.append(f"### {ep['title']}")
        lines.append(f"- 收听 {format_duration(ep['progress'])} / 总时长 {format_duration(ep['duration'])}（{ep['percentage']}%）")
        if ep['rating'] and ep['rating'] != "待评价":
            lines.append(f"- 评分：{ep['rating']}")
        if ep['desc']:
            # 截取到第一个换行或句号
            desc_short = ep['desc'][:200].split('\n')[0]
            lines.append(f"> {desc_short}")
        lines.append("")
    
    if skipped:
        names = ", ".join(skipped[:3])
        lines.append(f"*（还打开了 {len(skipped)} 期但收听不足 10 分钟：{names}）*\n")
    
    return "\n".join(lines)


def build_reading_section(reading_pages, label="📚 阅读"):
    if not reading_pages:
        return ""
    
    lines = [f"## {label}\n"]
    
    for rp in reading_pages:
        props = rp["properties"]
        title = get_title(props)
        if not title:
            continue
        
        lines.append(f"- **{title}**")
        
        # 获取划线/笔记内容
        for text_key in ["划线", "笔记", "摘要", "内容", "文本", "总结"]:
            text = get_rich_text(props, text_key)
            if text:
                lines.append(f"  > {text[:150]}")
                break
        
        # 从 blocks 中获取内容
        block_texts = get_block_text(rp["id"])
        for text in block_texts[:2]:
            lines.append(f"  > {text[:150]}")
        
        lines.append("")
    
    return "\n".join(lines)


def build_journal_section(pages, label, emoji):
    """通用日记模块（成功日记、感恩日记等）"""
    if not pages:
        return ""
    
    lines = [f"## {emoji} {label}\n"]
    has_content = False
    
    for p in pages:
        props = p["properties"]
        title = get_title(props)
        
        if title:
            lines.append(f"- {title}")
            has_content = True
        
        # 从 blocks 中获取内容
        block_texts = get_block_text(p["id"])
        for text in block_texts:
            lines.append(f"- {text}")
            has_content = True
    
    if not has_content:
        return ""
    
    lines.append("")
    return "\n".join(lines)


def build_article_section(article_pages):
    if not article_pages:
        return ""
    
    lines = ["## 📝 文章与输入\n"]
    for ap in article_pages:
        props = ap["properties"]
        title = get_title(props)
        if not title:
            continue
        
        lines.append(f"### {title}")
        
        summary = get_rich_text(props, "摘要") or get_rich_text(props, "总结") or get_rich_text(props, "补充")
        if summary:
            lines.append(f"> {summary[:200]}")
        
        link = get_rich_text(props, "链接") or get_rich_text(props, "Link")
        if link:
            lines.append(f"🔗 {link}")
        
        lines.append("")
    
    return "\n".join(lines)


def build_health_section(health_pages, label="🏃 健康"):
    if not health_pages:
        return ""
    
    lines = [f"## {label}\n"]
    for hp in health_pages:
        props = hp["properties"]
        title = get_title(props)
        if not title:
            continue
        
        lines.append(f"- **{title}**")
        
        for detail_key in ["详情", "内容", "描述", "文本", "笔记", "备注"]:
            text = get_rich_text(props, detail_key)
            if text:
                lines.append(f"  {text[:100]}")
                break
        
        block_texts = get_block_text(hp["id"])
        for text in block_texts[:2]:
            lines.append(f"  {text[:100]}")
        
        lines.append("")
    
    return "\n".join(lines)


def build_finance_section(finance_pages):
    if not finance_pages:
        return ""
    
    lines = ["## 💰 每日收支\n"]
    total_income = 0
    total_expense = 0
    
    for fp in finance_pages:
        props = fp["properties"]
        title = get_title(props)
        
        # 尝试直接获取金额
        amount = get_number(props, "金额")
        finance_type = get_select_name(props, "收支") or get_select_name(props, "类型") or ""
        category = get_rich_text(props, "类别") or get_select_name(props, "类别") or ""
        account = get_select_name(props, "账户") or get_rich_text(props, "账户") or ""
        note = get_rich_text(props, "备注") or get_rich_text(props, "说明") or ""
        
        # 如果有金额，直接记
        if amount is not None and amount != 0:
            if "收入" in finance_type:
                total_income += amount
                lines.append(f"- ✅ {title or note or category} · ¥{amount}")
            else:
                total_expense += amount
                lines.append(f"- ❌ {title or note or category} · ¥{amount}")
        
        # 尝试从关联的子条目获取（日支出）
        for sub_key in ["日支出", "日收入", "每日收支"]:
            sub_rels = props.get(sub_key, {}).get("relation", [])
            if sub_rels:
                for rel in sub_rels:
                    sub_page = get_page(rel["id"])
                    if sub_page:
                        sub_props = sub_page["properties"]
                        sub_title = get_title(sub_props)
                        sub_amount = get_number(sub_props, "金额") or 0
                        sub_type = get_select_name(sub_props, "收支") or get_select_name(sub_props, "类型") or ""
                        sub_cat = get_rich_text(sub_props, "类别") or get_select_name(sub_props, "类别") or ""
                        sub_account = get_select_name(sub_props, "账户") or ""
                        sub_note = get_rich_text(sub_props, "备注") or ""
                        
                        display = sub_note or sub_cat or sub_title or "消费"
                        if "收入" in sub_type:
                            total_income += sub_amount
                            lines.append(f"- ✅ {display} · ¥{sub_amount} ({sub_account})".strip())
                        else:
                            total_expense += sub_amount
                            lines.append(f"- ❌ {display} · ¥{sub_amount} ({sub_account})".strip())
                break
    
    if total_income == 0 and total_expense == 0 and len(lines) <= 1:
        return ""
    
    lines.append(f"\n**收入 ¥{total_income} | 支出 ¥{total_expense} | 净额 ¥{total_income - total_expense}**\n")
    return "\n".join(lines)


def build_location_section(location_pages):
    if not location_pages:
        return ""
    
    lines = ["## 📍 位置轨迹\n"]
    locations = []
    for lp in location_pages:
        title = get_title(lp["properties"])
        if title:
            locations.append(title)
    
    # 去重并保留顺序
    seen = set()
    for loc in locations:
        short = loc.split(" ")[0] if " " in loc else loc  # 取地名不取地址
        if short not in seen:
            seen.add(short)
            lines.append(f"- {loc}")
    
    lines.append("")
    return "\n".join(lines)


def build_text_records_section(diary_props):
    """日记中心属性中的文本字段"""
    parts = []
    
    rating = get_select_name(diary_props, "评分")
    today_work = get_rich_text(diary_props, "今日工作")
    today_tasks = get_rich_text(diary_props, "今日事务")
    summary = get_rich_text(diary_props, "总结")
    success_text = get_rich_text(diary_props, "☀️成功日记")
    gratitude_text = get_rich_text(diary_props, "💗感恩日记")
    
    if rating:
        parts.append(f"**评分**：{rating}")
    if today_work:
        parts.append(f"### 今日工作\n{today_work}")
    if today_tasks:
        parts.append(f"### 今日事务\n{today_tasks}")
    if summary:
        parts.append(f"### 总结\n{summary}")
    if success_text:
        parts.append(f"### ☀️ 成功日记\n{success_text}")
    if gratitude_text:
        parts.append(f"### 💗 感恩日记\n{gratitude_text}")
    
    if not parts:
        return ""
    
    return "## 📋 文本记录\n\n" + "\n\n".join(parts) + "\n"


def build_insights_section(diary_props):
    """今日领悟"""
    insight_text = get_rich_text(diary_props, "总结")
    if insight_text:
        return f"## 💡 今日领悟\n\n{insight_text}\n"
    return ""


def build_fragment_reflection_section(date_str):
    """从碎片中心随机挑选深度碎片，作为每日回顾"""
    print(f"  🔍 正在从碎片中心选取回顾内容...")
    
    # 获取所有未删除的碎片
    fragments = query_database(FRAGMENT_DB, {
        "property": "删除",
        "checkbox": {"does_not_equal": True}
    })
    
    if not fragments:
        print(f"  ⚠️ 碎片中心为空")
        return ""
    
    # 解析碎片，提取标题、标签、创建时间
    parsed = []
    for f in fragments:
        props = f["properties"]
        title = get_title(props)
        if not title:
            continue
        
        tags = [t["name"] for t in props.get("Tags", {}).get("multi_select", [])]
        created_raw = props.get("Created At", {}).get("date") or {}
        created = created_raw.get("start", "") if isinstance(created_raw, dict) else ""
        
        # 计算优先级分数：有深度标签的排前面
        priority = 0
        for pt in FRAGMENT_PRIORITY_TAGS:
            if pt in tags:
                priority = 2
                break
        
        parsed.append({
            "title": title,
            "tags": tags,
            "created": created,
            "priority": priority
        })
    
    if not parsed:
        return ""
    
    # 基于日期的伪随机，同一天选出来的相同
    seed = int(hashlib.md5(date_str.encode()).hexdigest(), 16)
    rng = random.Random(seed)
    
    # 优先从高优先级碎片中选
    priority_items = [p for p in parsed if p["priority"] == 2]
    other_items = [p for p in parsed if p["priority"] == 0]
    
    picked = []
    rng.shuffle(priority_items)
    rng.shuffle(other_items)
    
    # 先从高优先级中选
    pick_from = priority_items + other_items
    for item in pick_from:
        if len(picked) >= FRAGMENT_PICK_COUNT:
            break
        # 避免选当天的碎片（如果碎片有日期的话）
        if item["created"] and date_str in item["created"]:
            continue
        picked.append(item)
    
    if not picked:
        return ""
    
    lines = ["## 💭 碎片回顾\n"]
    lines.append("*过去的自己，写给现在的自己*\n")
    
    for p in picked:
        # 取标签中类别/领悟、类别/思考等作为前缀
        category_tag = ""
        for tag in p["tags"]:
            if tag.startswith("类别/"):
                category_tag = tag.replace("类别/", "")
                break
            elif tag.startswith("领域/"):
                category_tag = tag.replace("领域/", "")
                break
        
        # 清理标题中的 #标签
        clean_title = p["title"]
        clean_title = re.sub(r'\s*#\S+', '', clean_title).strip()
        
        if category_tag:
            lines.append(f"- **[{category_tag}]** {clean_title}")
        else:
            lines.append(f"- {clean_title}")
    
    lines.append("")
    print(f"  ✅ 碎片回顾 ({len(picked)}条)")
    return "\n".join(lines)


# ============ 主流程 ============

def generate_report(date_str=None):
    if not date_str:
        date_str = get_yesterday()
    
    print(f"📅 正在生成 {date_str} 的日记报告...")
    
    # 1. 获取日记中心条目
    diary = get_diary_entry(date_str)
    if not diary:
        print(f"⚠️ {date_str} 没有日记中心条目，跳过")
        return None
    
    diary_props = diary["properties"]
    diary_id = diary["id"]
    print(f"  ✅ 找到日记中心: {get_title(diary_props)}")
    
    # 2. 构建报告
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = weekday_names[dt.weekday()]
        date_display = dt.strftime("%Y年%m月%d日")
    except:
        weekday = ""
        date_display = date_str
    
    sections = [f"# {date_display} · {weekday} 日记报告\n"]
    
    # 天气
    s = build_weather_section(diary_props, date_str)
    if s:
        sections.append(s)
        print(f"  ✅ 天气")
    
    # 睡眠
    sleep_pages = get_related_pages(diary_props, "睡眠记录")
    if not sleep_pages:
        sleep_pages = get_related_pages(diary_props, "睡眠")
    s = build_sleep_section(sleep_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 睡眠 ({len(sleep_pages)})")
    
    # 任务
    task_pages = get_related_pages(diary_props, "事件与任务")
    s = build_tasks_section(task_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 任务 ({len(task_pages)})")
    
    # 播客
    podcast_pages = get_related_pages(diary_props, "播客")
    s = build_podcast_section(podcast_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 播客 ({len(podcast_pages)})")
    
    # 阅读
    reading_pages = get_related_pages(diary_props, "书籍")
    if not reading_pages:
        reading_pages = get_related_pages(diary_props, "当日阅读")
    s = build_reading_section(reading_pages, "📚 阅读")
    if s:
        sections.append(s)
        print(f"  ✅ 阅读 ({len(reading_pages)})")
    
    # 影视
    movie_pages = get_related_pages(diary_props, "影视")
    s = build_reading_section(movie_pages, "🎬 影视")
    if s:
        sections.append(s)
        print(f"  ✅ 影视 ({len(movie_pages)})")
    
    # 成功日记
    success_pages = get_related_pages(diary_props, "成功日记")
    s = build_journal_section(success_pages, "成功日记", "☀️")
    if s:
        sections.append(s)
        print(f"  ✅ 成功日记 ({len(success_pages)})")
    
    # 感恩日记
    gratitude_pages = get_related_pages(diary_props, "感恩日记")
    s = build_journal_section(gratitude_pages, "感恩日记", "💗")
    if s:
        sections.append(s)
        print(f"  ✅ 感恩日记 ({len(gratitude_pages)})")
    
    # 文章
    article_pages = get_related_pages(diary_props, "文章")
    if article_pages:
        s = build_article_section(article_pages)
        sections.append(s)
        print(f"  ✅ 文章 ({len(article_pages)})")
    
    # 健康
    health_pages = get_related_pages(diary_props, "健康")
    if not health_pages:
        health_pages = get_related_pages(diary_props, "运动")
    s = build_health_section(health_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 健康 ({len(health_pages)})")
    
    # 收支
    finance_pages = get_related_pages(diary_props, "日收支")
    if not finance_pages:
        finance_pages = get_related_pages(diary_props, "每日收支")
    s = build_finance_section(finance_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 收支 ({len(finance_pages)})")
    
    # 位置
    location_pages = get_related_pages(diary_props, "位置")
    s = build_location_section(location_pages)
    if s:
        sections.append(s)
        print(f"  ✅ 位置 ({len(location_pages)})")
    
    # 打卡
    checkin_pages = get_related_pages(diary_props, "打卡记录")
    s = build_health_section(checkin_pages, "✌️ 打卡")
    if s:
        sections.append(s)
        print(f"  ✅ 打卡 ({len(checkin_pages)})")
    
    # 今日领悟
    insight_pages = get_related_pages(diary_props, "今日领悟")
    s = build_journal_section(insight_pages, "今日领悟", "💡")
    if s:
        sections.append(s)
        print(f"  ✅ 今日领悟 ({len(insight_pages)})")
    
    # 文本记录
    s = build_text_records_section(diary_props)
    if s:
        sections.append(s)
        print(f"  ✅ 文本记录")
    
    # 页面 blocks 中的内容
    block_texts = get_block_text(diary_id)
    if block_texts:
        block_section = "## 📝 页面笔记\n\n" + "\n\n".join(block_texts) + "\n"
        sections.append(block_section)
        print(f"  ✅ 页面笔记 ({len(block_texts)}段)")
    
    # 碎片回顾（放在最后，作为收尾）
    s = build_fragment_reflection_section(date_str)
    if s:
        sections.append(s)
    
    full_report = "\n".join(sections)
    print(f"\n📊 报告生成完成，共 {len(full_report)} 字符")
    
    return full_report


# ============ Get 笔记 API ============

def send_to_getnote(title, content, tags=None):
    if not GETNOTE_API_KEY:
        print("❌ 未配置 GETNOTE_API_KEY")
        return False
    
    body = {
        "title": title,
        "content": content,
        "note_type": "plain_text",
        "tags": tags or ["日记报告", "每日回顾"]
    }
    
    data = json.dumps(body).encode()
    req = Request(
        "https://openapi.biji.com/open/api/v1/resource/note/save",
        data=data,
        method="POST"
    )
    req.add_header("Authorization", f"Bearer {GETNOTE_API_KEY}")
    req.add_header("X-Client-ID", GETNOTE_CLIENT_ID)
    req.add_header("Content-Type", "application/json")
    
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                note_id = result.get("data", {}).get("note_id", "")
                print(f"✅ 已发送到 Get 笔记 (note_id: {note_id})")
                return True
            else:
                error = result.get("error", {})
                print(f"❌ Get 笔记保存失败: {error.get('message', '未知错误')}")
                return False
    except HTTPError as e:
        print(f"❌ Get 笔记 API 错误 {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False


# ============ 入口 ============

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    
    report = generate_report(date_str)
    
    if not report:
        print("没有生成报告，退出")
        sys.exit(0)
    
    # 保存到本地
    filename = f"report_{date_str or get_yesterday()}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"📄 报告已保存到 {filename}")
    
    # 发送到 Get 笔记
    if GETNOTE_API_KEY:
        title = f"{date_str or get_yesterday()} 日记报告"
        send_to_getnote(title, report)
    else:
        print("⚠️ 未配置 GETNOTE_API_KEY，跳过发送")
    
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
