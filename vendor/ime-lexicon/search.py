"""行业词库搜索。查询模板来自归档 industry-dict。

执行顺序：有 argo（CLI 或技能脚本）就用 argo；没有就走通用搜索 ddgs。
不自己发 HTTP。默认只打计划；--run 才真正搜。
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from clean_dict import clean_industry

# 产线行业才用「材料/工艺/设备/检测/现场」。投资、内容不要硬套。
SCHEMAS: dict[str, tuple[str, ...]] = {
    "制造": ("材料", "工艺", "设备", "检测", "现场"),
    "投资": ("品种", "策略", "平台", "指标", "圈内"),
    "内容": ("体裁", "流程", "平台", "数据", "圈内"),
    "求职": ("环节", "文书", "平台", "条件", "圈内"),
    "视频": ("演员", "导演", "剧名", "角色", "平台"),
    "汽车": ("品牌", "型号", "配件", "维修", "驾驶"),
    "食品": ("菜名", "食材", "烹饪", "餐厅", "营养"),
    "医疗": ("药品", "症状", "医院", "科室", "治疗"),
    "通用": ("术语", "流程", "工具", "指标", "圈内"),
}

QUERY_BY_SCHEMA: dict[str, tuple[str, ...]] = {
    "制造": (
        "{topic} 专业术语 大全",
        "{topic} 生产工艺 术语",
        "{topic} 检测 标准 术语",
    ),
    "投资": (
        "{topic} 专业术语 大全",
        "{topic} 交易 策略 术语",
        "{topic} 估值 指标 术语",
    ),
    "内容": (
        "{topic} 专业术语 大全",
        "{topic} 创作 流程 术语",
        "{topic} 运营 数据 指标",
    ),
    "求职": (
        "{topic} 专业术语 大全",
        "{topic} 面试 流程 术语",
        "{topic} 薪酬 合同 术语",
    ),
    "视频": (
        "{topic} 专业术语 大全",
        "{topic} 演员 导演 术语",
        "{topic} 角色 剧名 术语",
    ),
    "汽车": (
        "{topic} 专业术语 大全",
        "{topic} 品牌 型号 术语",
        "{topic} 配件 维修 术语",
    ),
    "食品": (
        "{topic} 专业术语 大全",
        "{topic} 菜名 食材 术语",
        "{topic} 烹饪 餐厅 术语",
    ),
    "医疗": (
        "{topic} 专业术语 大全",
        "{topic} 药品 症状 术语",
        "{topic} 医院 科室 术语",
    ),
    "通用": (
        "{topic} 专业术语 大全",
        "{topic} 常用说法 黑话",
        "{topic} 指标 缩写",
    ),
}

_SCHEMA_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("锂电", "电池", "涂布", "制造", "产线", "养殖", "化工", "半导体", "工厂"), "制造"),
    (("投资", "理财", "基金", "股票", "估值", "证券"), "投资"),
    (("自媒体", "内容创作", "知乎", "公众号", "写作", "新媒体"), "内容"),
    (("求职", "简历", "面试", "校招"), "求职"),
    (("电影", "电视剧", "综艺", "演员", "导演", "角色", "剧名", "影视"), "视频"),
    (("汽车", "车型", "驾驶", "维修", "配件", "品牌", "车"), "汽车"),
    (("食品", "菜名", "食材", "烹饪", "餐厅", "营养", "美食", "菜谱"), "食品"),
    (("医疗", "药品", "症状", "医院", "科室", "治疗", "医学", "药"), "医疗"),
)

# 兼容旧引用：仅制造骨架。
CATEGORIES = SCHEMAS["制造"]
QUERY_TEMPLATES = QUERY_BY_SCHEMA["制造"]


def detect_schema(topic: str) -> str:
    t = (topic or "").strip()
    for keys, name in _SCHEMA_HINTS:
        if any(k in t for k in keys):
            return name
    return "通用"


def known_categories() -> set[str]:
    out: set[str] = set()
    for cats in SCHEMAS.values():
        out.update(cats)
    return out

_TEXT_KEYS = frozenset(
    {"title", "snippet", "content", "text", "description", "body", "summary", "name"}
)

_ARGO_SCRIPT = Path.home() / ".agents/skills/argo/scripts/search.py"


def has_argo() -> bool:
    return shutil.which("argo") is not None or _ARGO_SCRIPT.is_file()


def has_generic() -> bool:
    return shutil.which("ddgs") is not None


def detect_backend() -> str:
    """argo | generic | none。有 argo 用 argo，否则通用搜索。"""
    if has_argo():
        return "argo"
    if has_generic():
        return "generic"
    return "none"


def argo_argv(query: str) -> list[str] | None:
    if shutil.which("argo"):
        return ["argo", "search", query, "--max-results", "5", "--json", "--no-envelope"]
    if _ARGO_SCRIPT.is_file():
        return [
            "python3",
            str(_ARGO_SCRIPT),
            query,
            "--max-results",
            "5",
            "--json",
            "--no-envelope",
        ]
    return None


def generic_cmd(query: str) -> list[str]:
    return ["ddgs", "text", "-q", query, "-m", "5", "-o", "json", "-r", "cn-zh"]


def generic_argv(query: str) -> list[str] | None:
    return generic_cmd(query) if shutil.which("ddgs") else None


def query_plan(topic: str, *, backend: str | None = None) -> list[dict[str, object]]:
    topic = topic.strip()
    if not topic:
        raise ValueError("topic 为空")
    be = backend or detect_backend()
    schema = detect_schema(topic)
    out: list[dict[str, object]] = []
    for tmpl in QUERY_BY_SCHEMA[schema]:
        q = tmpl.format(topic=topic)
        if be == "argo":
            argv = argo_argv(q) or [
                "argo", "search", q, "--max-results", "5", "--json", "--no-envelope",
            ]
            fb: list[str] | None = generic_cmd(q)
        elif be == "generic":
            argv = generic_argv(q) or generic_cmd(q)
            fb = None
        else:
            argv = []
            fb = None
        rec: dict[str, object] = {
            "query": q,
            "backend": be,
            "schema": schema,
            "argv": argv,
            "cmd": " ".join(shlex.quote(a) for a in argv) if argv else f"(generic) search: {q}",
        }
        if fb:
            rec["fallback"] = " ".join(shlex.quote(a) for a in fb)
        out.append(rec)
    return out


def extract_terms(text: str) -> list[str]:
    """按标点/空白切开再清洗。不在连续中文里切词，长句留给代理补。"""
    parts = re.split(r"[^\u4e00-\u9fffA-Za-z0-9]+", text)
    return clean_industry(parts)


def _walk_text(obj: Any, out: list[str], *, top: bool = True) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _TEXT_KEYS and isinstance(v, str):
                out.append(v)
            else:
                _walk_text(v, out, top=False)
    elif isinstance(obj, list):
        for item in obj:
            _walk_text(item, out, top=False)
    elif isinstance(obj, str) and top:
        out.append(obj)


def extract_from_obj(obj: Any) -> list[str]:
    chunks: list[str] = []
    _walk_text(obj, chunks)
    return extract_terms("\n".join(chunks))


def load_argo_payload(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return {}
    for i, ch in enumerate(raw):
        if ch in "{[":
            try:
                return json.loads(raw[i:])
            except json.JSONDecodeError:
                break
    items: list[Any] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line[:1] not in "{[":
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def extract_from_files(paths: Iterable[Path]) -> list[str]:
    words: list[str] = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        payload = load_argo_payload(text)
        if payload in ({}, []):
            words.extend(extract_terms(text))
        else:
            words.extend(extract_from_obj(payload))
    return clean_industry(words)


def _run_backend(
    topic: str, backend: str, *, timeout: int
) -> tuple[list[str], list[dict[str, str]]]:
    words: list[str] = []
    reports: list[dict[str, str]] = []
    for step in query_plan(topic, backend=backend):
        argv = list(step["argv"] or [])
        rec = {
            "backend": backend,
            "query": str(step["query"]),
            "status": "ok",
            "terms": "0",
        }
        if not argv:
            rec["status"] = "no-cli"
            reports.append(rec)
            continue
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            rec["status"] = "fail:timeout"
            reports.append(rec)
            continue
        except OSError as e:
            # 不带异常原文：FileNotFoundError 会把绝对用户名路径打进报告
            rec["status"] = f"fail:{type(e).__name__}:{getattr(e, 'strerror', '') or 'cli 不可用'}"
            reports.append(rec)
            continue
        blob = proc.stdout or ""
        if proc.returncode != 0 and not blob.strip():
            rec["status"] = f"exit:{proc.returncode}"
            reports.append(rec)
            continue
        got = extract_from_obj(load_argo_payload(blob))
        words.extend(got)
        rec["terms"] = str(len(got))
        if proc.returncode != 0:
            rec["status"] = f"ok+exit:{proc.returncode}"
        reports.append(rec)
    return clean_industry(words), reports


def run_search(
    topic: str, *, engine: str = "auto", timeout: int = 90
) -> tuple[list[str], list[dict[str, str]]]:
    """argo 优先；抽到 0 词或 CLI 不可用再走通用搜索。"""
    if engine == "auto":
        order: list[str] = []
        if has_argo():
            order.append("argo")
        if has_generic():
            order.append("generic")
    elif engine in {"argo", "generic"}:
        order = [engine]
    else:
        raise ValueError(f"未知引擎 {engine}")
    if not order:
        raise FileNotFoundError("没有 argo，也没有通用搜索（ddgs）。代理用宿主 web_search 跑这 3 路，再 --from")
    words: list[str] = []
    reports: list[dict[str, str]] = []
    for be in order:
        got, recs = _run_backend(topic, be, timeout=timeout)
        reports.extend(recs)
        words.extend(got)
        if got:
            break
    return clean_industry(words), reports


def run_argo(topic: str, *, timeout: int = 90) -> tuple[list[str], list[dict[str, str]]]:
    """兼容旧调用，等价 run_search(engine='auto')。"""
    return run_search(topic, engine="auto", timeout=timeout)
