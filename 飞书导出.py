import os
import re
import sys
import json
import time
import pathlib
import requests
from typing import Dict, Any, Iterable, Optional
from datetime import datetime

# ===================== 基础配置（按你的实际情况填写） =====================
FEISHU_APP_ID = "cli_a80299d461bd100e"      # 你的 App ID
FEISHU_APP_SECRET = "6xgQ9apGbhgmm8l1qB514dJDfJc23Xkl"    # 你的 App Secret

# 中国大陆用 FEISHU；国际版(Lark)改为 True
USE_LARK_INTERNATIONAL = False

APP_TOKEN = "NosabvdODa6WgtsfA4Fcc16inVh"              # 多维表格 Base app_token
TABLE_ID = "tblAPuyiAmf9Guf8"               # 数据表 ID；单品：tblEjhZoMSD39Ab4；多品对比：tblAPuyiAmf9Guf8；多品科普：tblzYjroPZlahvYM
VIEW_ID = ""                                 # 视图 ID（可留空）

# 列名配置（支持列名或 field_id；推荐用列名，和飞书里显示的文字一致）
MD_FIELD_NAME = "内容"               # 存 Markdown 正文的列
TITLE_FIELD_NAME = "标题"                    # 用于文档开头那行“标题：xxx”
KEYWORD_FIELD_NAME = "关键词"                 # 用于生成文件名的“关键词列”（重要）
USERNAME_FIELD_NAME = "用户名"               # 用于筛选：用户名 = kaede
TIME_FIELD_NAME = "时间"                     # 用于筛选：时间 > 指定阈值
PRODUCT_FIELD_NAME = "产品名称"              # 用于筛选：产品名称 = 万艾可他达拉非

# 过滤配置 —— 根据你的需求修改这些值
# 注意：空字符串表示不进行该项筛选
FILTER_USERNAME_EQ = ""                       # 用户名筛选（空字符串表示不筛选）
FILTER_PRODUCT_EQ = "华安创业板50etf(159949)"      # 产品名称筛选（空字符串表示不筛选）
# 时间阈值：支持 "08-18 12:00"（自动补当前年），或 "2025-08-18 12:00"、"2025/08/18 12:00"
# 注意：两个时间都为空表示不筛选时间；只填一个表示只筛选该方向；两个都填表示筛选时间段
FILTER_TIME_AFTER = "11-03 15:30"                         # 表示时间晚于此值（空字符串表示不限制）
FILTER_TIME_BEFORE = ""                        # 表示时间早于此值（空字符串表示不限制）
# 导出目录配置
OUTPUT_DIR = "./华安"

PAGE_SIZE = 500                              # 分页拉取条数（飞书上限 500）
REQUEST_TIMEOUT = 15                         # 秒
# ========================================================================

API_BASE = "https://open.larksuite.com" if USE_LARK_INTERNATIONAL else "https://open.feishu.cn"
AUTH_URL = f"{API_BASE}/open-apis/auth/v3/tenant_access_token/internal"
LIST_RECORDS_URL = f"{API_BASE}/open-apis/bitable/v1/apps/{{app_token}}/tables/{{table_id}}/records"

HEADERS_JSON = {"Content-Type": "application/json; charset=utf-8"}


# --------------------------- 工具函数区 ---------------------------

def normalize_md_for_word(md: str) -> str:
    """
    目的：
    - 确保 ATX 标题（#…）是块级开头：在标题行前自动补一行空行；
    - 把普通文本的“单换行”提升为“新段落”（加一个空行=硬回车）；
    - 保持代码块/列表/引用的结构，不乱加空行。

    做法：
    - 遇到 ``` 进出代码块，不改里面的内容；
    - 列表（- * + / 1. 2. ...）、引用（> ）行保留原样；
    - 标题行（^#{1,6}\s+）前保证至少一行空行；
    - 其它普通文本行后面补一行空行（制造新段落）。
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    in_code = False

    def is_list(line: str) -> bool:
        return bool(re.match(r"^\s{0,3}(-|\*|\+)\s+.+", line) or re.match(r"^\s{0,3}\d+\.\s+.+", line))

    def is_quote(line: str) -> bool:
        return bool(re.match(r"^\s{0,3}>\s+.+", line))

    def is_hr(line: str) -> bool:
        s = line.strip()
        return s in ("---", "***", "___") or bool(re.match(r"^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$", line))

    def is_heading(line: str) -> bool:
        return bool(re.match(r"^\s{0,3}#{1,6}\s+.+", line))

    i = 0
    while i < len(lines):
        line = lines[i]
        # 代码块围栏
        if line.strip().startswith("```"):
            out.append(line)
            in_code = not in_code
            i += 1
            continue
        if in_code:
            out.append(line)
            i += 1
            continue

        if is_heading(line):
            # 保证标题前有空行（除非已经在文档开头或前一行本来就是空行）
            if out and out[-1] != "":
                out.append("")
            out.append(line)
            #（可选）标题后留空行更稳
            out.append("")
            i += 1
            continue

        if is_list(line) or is_quote(line) or is_hr(line):
            out.append(line)
            i += 1
            continue

        # 空行直接保留（并避免重复空行）
        if line.strip() == "":
            if out and out[-1] != "":
                out.append("")
            elif not out:
                out.append("")
            i += 1
            continue

        # 普通文本行：转为“新段落”——行后补一个空行
        out.append(line)
        out.append("")
        i += 1

    # 去重多余空行
    compact = []
    for l in out:
        if l == "" and (not compact or compact[-1] == ""):
            continue
        compact.append(l)

    return "\n".join(compact)

def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取 tenant_access_token（有效期约2小时）"""
    resp = requests.post(
        AUTH_URL,
        headers=HEADERS_JSON,
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"get token failed: {data}")
    return data["tenant_access_token"]


def iter_records(app_token: str, table_id: str, view_id: str, token: str) -> Iterable[Dict[str, Any]]:
    """分页遍历记录；返回一条条 record（包含 record_id 与 fields）"""
    url = LIST_RECORDS_URL.format(app_token=app_token, table_id=table_id)
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(HEADERS_JSON)
    page_token = ""
    while True:
        params = {"page_size": PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id

        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"list records failed: {data}")

        items = data.get("data", {}).get("items", []) or data.get("data", {}).get("records", []) or []
        for it in items:
            yield it

        has_more = data.get("data", {}).get("has_more", False)
        page_token = data.get("data", {}).get("page_token", "")
        if not has_more or not page_token:
            break


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    name = (name or "").strip()
    if not name:
        name = fallback
    # 去除非法字符
    name = re.sub(r'[\/:*?"<>|]+', "_", name)
    # 避免过长
    return name[:150]


def extract_text_like(value: Any) -> str:
    """
    宽松提取文本：
    - 字符串：直接返回
    - 列表：拼接其中元素（若是对象则取 text 字段）
    - 字典：优先取 text；否则转成 JSON 字符串
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if isinstance(value, list):
            parts = []
            for it in value:
                if isinstance(it, dict) and "text" in it:
                    parts.append(str(it["text"]))
                else:
                    parts.append(str(it))
            return "\n".join(parts)
        if isinstance(value, dict):
            if "text" in value:
                return str(value["text"])
            return json.dumps(value, ensure_ascii=False)
    except Exception:
        pass
    return str(value)


def get_by_field_name_or_id(fields: Dict[str, Any], key: str) -> Optional[Any]:
    """
    用列名或 field_id 拿值：
    - key 恰好是 fields 的键时，直接取
    - 否则做一次“忽略大小写”的宽松匹配
    """
    if key in fields:
        return fields[key]
    wanted = str(key).strip().lower()
    for k, v in fields.items():
        if str(k).strip().lower() == wanted:
            return v
    return None


def parse_datetime_any(val: Any) -> Optional[datetime]:
    """
    尝试把飞书“时间列”的值解析为 datetime（无时区的本地时间）：
    - 支持 int/float（时间戳；自动识别毫秒/秒）
    - 支持常见字符串：YYYY-MM-DD[ HH:MM[:SS]]，YYYY/MM/DD[...]，ISO8601，或 MM-DD HH:MM（自动补当前年）
    """
    if val is None:
        return None

    # 数字时间戳
    if isinstance(val, (int, float)):
        ts = float(val)
        # 简单判别毫秒/秒
        if ts > 1e12:  # 毫秒
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None

    s = str(val).strip()
    if not s:
        return None

    # ISO 风格（处理尾部 Z）
    try:
        if s.endswith("Z"):
            s2 = s[:-1]
            return datetime.fromisoformat(s2)
        return datetime.fromisoformat(s)
    except Exception:
        pass

    # 常见格式轮询
    fmt_list = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]
    for fmt in fmt_list:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue

    # 仅有 月-日 时:分（补当前年）
    try:
        if re.match(r"^\d{2}-\d{2}\s+\d{2}:\d{2}$", s):
            s2 = f"{datetime.now().year}-{s.replace(' ', ' ')}"
            # 转回形如 2025-08-18 12:00
            yyyy = datetime.now().year
            month_day, hm = s.split()
            s3 = f"{yyyy}-{month_day} {hm}"
            return datetime.strptime(s3, "%Y-%m-%d %H:%M")
    except Exception:
        pass

    # 最后兜底：纯日期/月日
    for fmt in ["%m-%d %H:%M", "%m-%d", "%H:%M"]:
        try:
            base = datetime.now().strftime("%Y")
            if fmt == "%m-%d %H:%M":
                return datetime.strptime(f"{base}-{s}", "%Y-%m-%d %H:%M")
            if fmt == "%m-%d":
                return datetime.strptime(f"{base}-{s}", "%Y-%m-%d")
            if fmt == "%H:%M":
                today = datetime.now().strftime("%Y-%m-%d")
                return datetime.strptime(f"{today} {s}", "%Y-%m-%d %H:%M")
        except Exception:
            continue

    return None


def normalize_eq(val: Any) -> str:
    """用于等值比较：取纯文本后做大小写折叠和去空格"""
    return extract_text_like(val).strip().lower()


def calc_threshold_dt(expr: str) -> Optional[datetime]:
    """把 FILTER_TIME_AFTER（如 '08-18 12:00' 或 '2025-08-18 12:00'）解析为 datetime"""
    return parse_datetime_any(expr)


def next_indexed_docx_path(directory: pathlib.Path, base_name: str) -> str:
    """
    生成不重复的文件路径：
    形如：<base_name> 01.docx，如果存在，则 <base_name> 02.docx，依次类推。
    """
    base_name = sanitize_filename(base_name, fallback="untitled")
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(1, 1000):
        suffix = f"{i:02d}"
        candidate = directory / f"{base_name} {suffix}.docx"
        if not candidate.exists():
            return str(candidate.resolve())
    # 极端情况：都占用，退而求其次加时间戳
    ts = int(time.time())
    return str((directory / f"{base_name} {ts}.docx").resolve())


# ================= Markdown -> DOCX（支持 Pandoc，内置兜底） =================
def markdown_to_docx(md_text: str, out_path: str, header_text: Optional[str] = None):
    """
    转换优先级：
    1) 若系统安装了 Pandoc 且 pip 有 pypandoc，走高质量转换；
    2) 否则用 python-docx 的简易渲染（支持标题/列表/粗斜体/代码块/引用等基础样式）。
    额外：若 header_text 非空，会插入到正文最前面。
    """
    md_text = md_text or ""
    if header_text:
        # 在 Markdown 文本最前插入一段纯文本标题
        md_text = f"标题：{header_text}\n\n" + md_text

    # ★★★ 新增：预处理，确保标题/段落正确识别
    md_text = normalize_md_for_word(md_text)

    try:
        import pypandoc  # type: ignore
        pypandoc.convert_text(
            md_text, 
            "docx", 
            format="markdown", 
            outputfile=out_path,
            extra_args=["--wrap=preserve"]
        )
        return
    except Exception:
        print("pandoc 不可用，切换到简易渲染模式")  # <--- 兜底提示
        pass

    # 优先用 pypandoc
    try:
        import pypandoc  # type: ignore
        pypandoc.convert_text(md_text, "docx", format="md", outputfile=out_path)
        return
    except Exception:
        pass

    # ---- 兜底渲染（python-docx）----
    from docx import Document  # type: ignore
    from docx.shared import Pt
    from docx.oxml.ns import qn

    def add_paragraph_with_inlines(p, text):
        # 简易行内语法：**粗体**、*斜体*、`行内代码`、[链接](url)
        tokens = []
        pattern = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`|\[.+?\]\(.+?\))")
        last = 0
        for m in pattern.finditer(text):
            if m.start() > last:
                tokens.append(("text", text[last:m.start()]))
            tokens.append(("md", m.group(0)))
            last = m.end()
        if last < len(text):
            tokens.append(("text", text[last:]))

        for ttype, tval in tokens:
            run = p.add_run()
            if ttype == "text":
                run.text = tval
            else:
                if tval.startswith("**"):
                    run.text = tval[2:-2]
                    run.bold = True
                elif tval.startswith("*"):
                    run.text = tval[1:-1]
                    run.italic = True
                elif tval.startswith("`"):
                    run.text = tval[1:-1]
                    run.font.name = "Consolas"
                elif tval.startswith("["):
                    m2 = re.match(r"\[(.+?)\]\((.+?)\)", tval)
                    if m2:
                        run.text = f"{m2.group(1)} ({m2.group(2)})"
                    else:
                        run.text = tval

    doc = Document()
    # 基本字体
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    style.font.size = Pt(11)

    # 先插入“标题：xxx”
    if header_text:
        doc.add_paragraph(f"标题：{header_text}")
        doc.add_paragraph("")  # 空行

    lines = md_text.splitlines()
    in_code_block = False
    code_buffer = []

    # 如果前面已经塞了标题行，上面 lines 也包含了；为了避免重复，这里从 header_text 之后的内容重新生成：
    if header_text:
        # 重新组装为 header_text + 两个换行之后的正文；上面已写入标题，这里截掉标题部分：
        body = "\n".join(md_text.splitlines()[2:])  # 假定前两行是“标题：xx”和空行
        lines = body.splitlines()

    for raw in lines:
        line = raw.rstrip("\n")

        # 三引号代码块
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_buffer = []
            else:
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_buffer))
                run.font.name = "Consolas"
                in_code_block = False
                code_buffer = []
            continue

        if in_code_block:
            code_buffer.append(line)
            continue

        # 标题 #
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = min(len(m.group(1)), 4)
            text = m.group(2).strip()
            heading = doc.add_heading(level=level)
            heading.text = ""
            add_paragraph_with_inlines(heading, text)
            continue

        # 引用 >
        if line.startswith("> "):
            p = doc.add_paragraph(style="Intense Quote")
            add_paragraph_with_inlines(p, line[2:].strip())
            continue

        # 分割线
        if re.match(r"^(-{3,}|_{3,}|\*{3,})$", line.strip()):
            doc.add_paragraph("—" * 20)
            continue

        # 列表
        if re.match(r"^(\s*[-*+]\s+).+", line):
            p = doc.add_paragraph(style="List Bullet")
            add_paragraph_with_inlines(p, re.sub(r"^\s*[-*+]\s+", "", line))
            continue
        if re.match(r"^\s*\d+\.\s+.+", line):
            p = doc.add_paragraph(style="List Number")
            add_paragraph_with_inlines(p, re.sub(r"^\s*\d+\.\s+", "", line))
            continue

        # 普通段落（保留空行）
        if line.strip() == "":
            doc.add_paragraph("")
        else:
            p = doc.add_paragraph()
            add_paragraph_with_inlines(p, line)

    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)


# --------------------------- 过滤逻辑 ---------------------------
def record_passes_filters(fields: Dict[str, Any], time_after: Optional[datetime], time_before: Optional[datetime]) -> bool:
    """是否满足筛选条件：用户名=xxx 且 产品名称=xxx 且 时间条件"""
    # 用户名筛选（如果配置了筛选条件）
    if FILTER_USERNAME_EQ.strip():
        usr_val = normalize_eq(get_by_field_name_or_id(fields, USERNAME_FIELD_NAME))
        if usr_val != normalize_eq(FILTER_USERNAME_EQ):
            return False

    # 产品名称筛选（如果配置了筛选条件）
    if FILTER_PRODUCT_EQ.strip():
        prod_val = normalize_eq(get_by_field_name_or_id(fields, PRODUCT_FIELD_NAME))
        if prod_val != normalize_eq(FILTER_PRODUCT_EQ):
            return False

    # 时间筛选
    # 只有当需要时间筛选时，才获取和解析时间字段
    if time_after is not None or time_before is not None:
        t_raw = get_by_field_name_or_id(fields, TIME_FIELD_NAME)
        t_dt = parse_datetime_any(t_raw)
        if t_dt is None:
            return False  # 没有时间或无法解析，则视为不通过
        
        # 检查时间是否在指定范围内
        if time_after is not None and not (t_dt > time_after):
            return False
        if time_before is not None and not (t_dt < time_before):
            return False

    return True


# --------------------------- 主流程 ---------------------------
def main():
    print("== 飞书多维表格 Markdown 批量导出为 Word ==")
    print(f"API_BASE = {API_BASE}")

    token = get_tenant_access_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    print("已获取 tenant_access_token")

    out_dir = pathlib.Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 解析时间筛选条件
    time_after_dt = calc_threshold_dt(FILTER_TIME_AFTER) if FILTER_TIME_AFTER.strip() else None
    time_before_dt = calc_threshold_dt(FILTER_TIME_BEFORE) if FILTER_TIME_BEFORE.strip() else None
    
    # 打印时间筛选条件信息
    if time_after_dt and time_before_dt:
        print(f"时间筛选：在 {time_after_dt} 之后且在 {time_before_dt} 之前")
    elif time_after_dt:
        print(f"时间筛选：在 {time_after_dt} 之后")
    elif time_before_dt:
        print(f"时间筛选：在 {time_before_dt} 之前")
    else:
        print("时间筛选：不限制")
    
    # 打印其他筛选条件
    if FILTER_USERNAME_EQ.strip():
        print(f"用户名筛选：等于 '{FILTER_USERNAME_EQ}'")
    else:
        print("用户名筛选：不限制")
    
    if FILTER_PRODUCT_EQ.strip():
        print(f"产品名称筛选：等于 '{FILTER_PRODUCT_EQ}'")
    else:
        print("产品名称筛选：不限制")

    total_scanned = 0
    total_matched = 0
    total_saved = 0

    for rec in iter_records(APP_TOKEN, TABLE_ID, VIEW_ID, token):
        total_scanned += 1
        record_id = rec.get("record_id") or rec.get("id") or f"row{total_scanned}"
        fields = rec.get("fields", {})

        # 过滤
        if not record_passes_filters(fields, time_after_dt, time_before_dt):
            continue
        total_matched += 1

        # 取各列内容
        md_raw = get_by_field_name_or_id(fields, MD_FIELD_NAME)
        md_text = extract_text_like(md_raw)

        if not md_text.strip():
            print(f"- 跳过（空 Markdown）：{record_id}")
            continue

        title_raw = get_by_field_name_or_id(fields, TITLE_FIELD_NAME)
        title_text = extract_text_like(title_raw).strip()

        keyword_raw = get_by_field_name_or_id(fields, KEYWORD_FIELD_NAME)
        keyword_text = extract_text_like(keyword_raw).strip() or record_id
        base_name = sanitize_filename(keyword_text, fallback=record_id)

        # 生成不重复文件名：<关键词> 01.docx / 02.docx / ...
        out_path = next_indexed_docx_path(out_dir, base_name)

        try:
            markdown_to_docx(md_text, out_path, header_text=title_text if title_text else None)
            total_saved += 1
            print(f"+ 已保存：{out_path}")
        except Exception as e:
            print(f"! 导出失败：{record_id} -> {e}")

    print(f"\n完成：扫描 {total_scanned} 行；符合条件 {total_matched} 行；成功导出 {total_saved} 个 .docx；输出目录：{out_dir.resolve()}")

# --------------------------- 新增：指定导出功能 ---------------------------
def export_specific_records(
    app_token: str = None,
    table_id: str = None,
    view_id: str = None,
    output_dir: str = None,
    filter_conditions: Dict[str, Any] = None,
    field_mappings: Dict[str, str] = None
) -> int:
    """
    指定导出飞书表格记录为Word文档的便捷函数
    
    Args:
        app_token: 飞书应用token，默认使用配置文件中的APP_TOKEN
        table_id: 表格ID，默认使用配置文件中的TABLE_ID  
        view_id: 视图ID，默认使用配置文件中的VIEW_ID
        output_dir: 输出目录，默认使用配置文件中的OUTPUT_DIR
        filter_conditions: 过滤条件字典，例如：{"用户名": "张三", "产品名称": "万艾可"}
        field_mappings: 字段映射，例如：{"内容": "MD_FIELD_NAME", "标题": "TITLE_FIELD_NAME"}
    
    Returns:
        成功导出的文档数量
    """
    # 使用默认配置或传入参数
    app_token = app_token or APP_TOKEN
    table_id = table_id or TABLE_ID
    view_id = view_id or VIEW_ID
    output_dir = output_dir or OUTPUT_DIR
    
    # 字段映射
    if field_mappings:
        global MD_FIELD_NAME, TITLE_FIELD_NAME, KEYWORD_FIELD_NAME
        MD_FIELD_NAME = field_mappings.get("内容", MD_FIELD_NAME)
        TITLE_FIELD_NAME = field_mappings.get("标题", TITLE_FIELD_NAME)
        KEYWORD_FIELD_NAME = field_mappings.get("关键词", KEYWORD_FIELD_NAME)
    
    print("== 指定导出飞书表格记录为Word文档 ==")
    print(f"API_BASE = {API_BASE}")
    
    token = get_tenant_access_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    print("已获取 tenant_access_token")
    
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    total_scanned = 0
    total_saved = 0
    
    for rec in iter_records(app_token, table_id, view_id, token):
        total_scanned += 1
        record_id = rec.get("record_id") or rec.get("id") or f"row{total_scanned}"
        fields = rec.get("fields", {})
        
        # 应用过滤条件
        if filter_conditions:
            passes_filter = True
            for field_name, expected_value in filter_conditions.items():
                actual_value = normalize_eq(get_by_field_name_or_id(fields, field_name))
                if actual_value != normalize_eq(expected_value):
                    passes_filter = False
                    break
            if not passes_filter:
                continue
        
        # 获取内容
        md_raw = get_by_field_name_or_id(fields, MD_FIELD_NAME)
        md_text = extract_text_like(md_raw)
        
        if not md_text.strip():
            print(f"- 跳过（空内容）：{record_id}")
            continue
        
        # 获取标题和关键词
        title_raw = get_by_field_name_or_id(fields, TITLE_FIELD_NAME)
        title_text = extract_text_like(title_raw).strip()
        
        keyword_raw = get_by_field_name_or_id(fields, KEYWORD_FIELD_NAME)
        keyword_text = extract_text_like(keyword_raw).strip() or record_id
        base_name = sanitize_filename(keyword_text, fallback=record_id)
        
        # 生成文件名
        out_path = next_indexed_docx_path(out_dir, base_name)
        
        try:
            markdown_to_docx(md_text, out_path, header_text=title_text if title_text else None)
            total_saved += 1
            print(f"+ 已保存：{out_path}")
        except Exception as e:
            print(f"! 导出失败：{record_id} -> {e}")
    
    print(f"\n完成：扫描 {total_scanned} 行，成功导出 {total_saved} 个 .docx 文件；输出目录：{out_dir.resolve()}")
    return total_saved


def export_by_record_ids(
    record_ids: list,
    app_token: str = None,
    table_id: str = None,
    output_dir: str = None,
    field_mappings: Dict[str, str] = None
) -> int:
    """
    根据记录ID列表导出指定记录
    
    Args:
        record_ids: 要导出的记录ID列表
        app_token: 飞书应用token
        table_id: 表格ID
        output_dir: 输出目录
        field_mappings: 字段映射
    
    Returns:
        成功导出的文档数量
    """
    app_token = app_token or APP_TOKEN
    table_id = table_id or TABLE_ID
    output_dir = output_dir or OUTPUT_DIR
    
    # 字段映射
    if field_mappings:
        global MD_FIELD_NAME, TITLE_FIELD_NAME, KEYWORD_FIELD_NAME
        MD_FIELD_NAME = field_mappings.get("内容", MD_FIELD_NAME)
        TITLE_FIELD_NAME = field_mappings.get("标题", TITLE_FIELD_NAME)
        KEYWORD_FIELD_NAME = field_mappings.get("关键词", KEYWORD_FIELD_NAME)
    
    print("== 根据记录ID导出指定记录 ==")
    print(f"API_BASE = {API_BASE}")
    
    token = get_tenant_access_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    print("已获取 tenant_access_token")
    
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    total_saved = 0
    
    # 获取所有记录并筛选指定ID
    for rec in iter_records(app_token, table_id, "", token):
        record_id = rec.get("record_id") or rec.get("id")
        if record_id not in record_ids:
            continue
            
        fields = rec.get("fields", {})
        
        # 获取内容
        md_raw = get_by_field_name_or_id(fields, MD_FIELD_NAME)
        md_text = extract_text_like(md_raw)
        
        if not md_text.strip():
            print(f"- 跳过（空内容）：{record_id}")
            continue
        
        # 获取标题和关键词
        title_raw = get_by_field_name_or_id(fields, TITLE_FIELD_NAME)
        title_text = extract_text_like(title_raw).strip()
        
        keyword_raw = get_by_field_name_or_id(fields, KEYWORD_FIELD_NAME)
        keyword_text = extract_text_like(keyword_raw).strip() or record_id
        base_name = sanitize_filename(keyword_text, fallback=record_id)
        
        # 生成文件名
        out_path = next_indexed_docx_path(out_dir, base_name)
        
        try:
            markdown_to_docx(md_text, out_path, header_text=title_text if title_text else None)
            total_saved += 1
            print(f"+ 已保存：{out_path}")
        except Exception as e:
            print(f"! 导出失败：{record_id} -> {e}")
    
    print(f"\n完成：成功导出 {total_saved} 个 .docx 文件；输出目录：{out_dir.resolve()}")
    return total_saved


if __name__ == "__main__":
    # 原有的主函数仍然可用
    main()
    
    # 示例：使用新增的功能
    print("\n" + "="*50)
    print("示例：使用新增功能导出指定记录")
    
    # 示例1：按条件导出
    # export_specific_records(
    #     filter_conditions={"用户名": "张三", "产品名称": "万艾可"},
    #     output_dir="./指定导出"
    # )
    
    # 示例2：按记录ID导出
    # export_by_record_ids(
    #     record_ids=["rec_123", "rec_456"],
    #     output_dir="./ID导出"
    # )
