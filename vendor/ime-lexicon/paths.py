"""本机输入法数据路径探测。不写死用户名。"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

HOME = Path.home()


def _env_path(*names: str) -> Path | None:
    """按顺序取第一个非空环境变量。TAXUE_* 是显式覆盖（测试/单次运行）优先，其次 IME_*（shell 配置）。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return Path(v)
    return None


# ══ 词库目录：位置由工作区定，代码按需创建 ══════════════════════════════
#
# 规矩：工作区下专门一个「词库/」根目录，里面按词库类型分子目录。位置只由工作区决定，
# 不由「数据碰巧在哪」决定——所以这里没有候选表、没有搜索：缺目录就建，新环境装上
# 跑一次就自举出来。要换地方就换工作区（IME_LEXICON_WORKSPACE），或单独覆盖词库根
# （IME_LEXICON_HOME），不用改代码。

KINDS = ("行业词库", "豆包词库", "微信词库", "创作词库", "研究", "备份", "整合")


def workspace_dir() -> Path | None:
    """工作区。TAXUE_LEXICON_WORKSPACE / IME_LEXICON_WORKSPACE 指定。"""
    return _env_path("TAXUE_LEXICON_WORKSPACE", "IME_LEXICON_WORKSPACE")


def lexicon_home() -> Path:
    """词库根目录 = 工作区下的「词库/」。没给工作区就落在当前目录，仍是确定的规矩。"""
    env = _env_path("TAXUE_LEXICON_HOME", "IME_LEXICON_HOME")
    if env:
        return env
    ws = workspace_dir()
    return (ws / "词库") if ws else (Path.cwd() / "词库")


def dict_dir(kind: str, *, create: bool = False) -> Path:
    """某类词库的目录。create=True 时缺就建——新环境自举靠这个。"""
    if kind not in KINDS:
        raise ValueError(f"未知词库类型 {kind}，可选：{'、'.join(KINDS)}")
    p = lexicon_home() / kind
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_dict_dirs() -> list[Path]:
    """把各类词库目录都建出来，返回清单（新环境一条命令把布局立起来）。"""
    return [dict_dir(k, create=True) for k in KINDS]


def industry_dict_dir() -> Path:
    """行业词库：build 的默认输出、行业表、acquire 候选表。"""
    return dict_dir("行业词库")


def doubao_dict_dir() -> Path:
    """豆包词库：导出与优化中间表。"""
    return dict_dir("豆包词库")


def wetype_dict_dir() -> Path:
    """微信词库：微信输入法的导出与中间表。"""
    return dict_dir("微信词库")


def creation_dict_dir() -> Path:
    """创作词库：harvest 的产出（金句/洞见、语音、会话语料、旧电脑词条）。"""
    return dict_dir("创作词库")


def research_dict_dir() -> Path:
    """研究词库：公开词库副本，只读对照，不整包 apply。"""
    return dict_dir("研究")


def backup_dict_dir() -> Path:
    """备份：按时间戳分子目录的词库快照。"""
    return dict_dir("备份")


def merged_dict_dir() -> Path:
    """整合：optimize 的合并/纠错产物，import 的输入。"""
    return dict_dir("整合")


def default_out_dir() -> Path:
    """没给 --out 时的总落点。IME_LEXICON_OUT 可覆盖。"""
    env = os.environ.get("IME_LEXICON_OUT")
    return Path(env) if env else lexicon_home()


# ══ 外部输入：别人的目录，本技能不定义它的位置 ══════════════════════════
#
# 素材库归内容工作流、画像归知乎技能、旧电脑词库是历史备份——都不是本技能的目录，
# 没有「按我们的规矩创建」这回事，所以只能：显式指定优先，其次按签名找。
# 签名 = 「这个目录里应该有什么」，比写死位置耐搬：本机这几个目录都搬过家。

_ROOTS = (HOME, HOME / "Documents")
_MAX_DEPTH = 6  # 本机这几个目录最深到 5 层，留一层余量
_PRUNE = frozenset({
    "Library", "Applications", "node_modules", "Pods", "DerivedData",
    "venv", "__pycache__", "site-packages", "dist", "build",
})
_COPY_MARK = ("副本", "备份", "backup", "archive", "历史", "已收录", "_old", "_bak")

_INDEX: list[tuple[Path, frozenset[str]]] | None = None


def _index() -> list[tuple[Path, frozenset[str]]]:
    """浅层目录清单 + 每个目录的子项名。整进程只遍历一次。

    顺手记子项名是为了省掉「每个目录都 glob 一次」：那样一万个目录就是一万次 readdir，
    实测把探测拖到 1 秒；记下来之后签名匹配只是一次查表。
    """
    global _INDEX
    if _INDEX is None:
        found: list[tuple[Path, frozenset[str]]] = []
        for root in _ROOTS:
            if not root.is_dir():
                continue
            base = len(root.parts)
            for cur, dirs, files in os.walk(root):
                here = Path(cur)
                kids = frozenset(files) | frozenset(dirs)
                if len(here.parts) - base >= _MAX_DEPTH:
                    dirs[:] = []
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _PRUNE]
                found.append((here, kids))
        _INDEX = found
    return _INDEX


def _has_sig(here: Path, kids: frozenset[str], sig: str) -> bool:
    """here 里有没有 sig 这个相对路径。单段名查表，带 / 的先看首段再 stat。"""
    if "/" in sig:
        head, _rest = sig.split("/", 1)
        return head in kids and (here / sig).exists()
    if "*" in sig:
        return any(fnmatch.fnmatch(n, sig) for n in kids)
    return sig in kids


def _discover(sig: str | None = None, *, name: str | None = None) -> Path | None:
    """找「含 sig 这个相对路径的目录」（sig 支持 * 通配），或只按目录名 name 找。

    命中多个时：路径里不含副本/备份字样的优先，其次路径浅的优先，最后按路径排序，
    保证同一台机器上结果稳定。找不到返回 None，由调用方决定退回哪个占位默认值。
    """
    if sig is None and name is None:
        raise ValueError("sig 和 name 至少要给一个")
    hits: list[Path] = []
    for here, kids in _index():
        if name is not None and here.name != name:
            continue
        if sig is not None and not _has_sig(here, kids, sig):
            continue
        hits.append(here)
    if not hits:
        return None
    hits.sort(key=lambda p: (any(m in str(p) for m in _COPY_MARK), len(p.parts), str(p)))
    return hits[0]


def material_dir() -> Path:
    """素材库根目录（内容工作流的地盘，只读）。

    认法：有 harvest 要读的那份金句文件；退一步只要有 `05_核心资产库`。本机另有一份
    「完整副本」同样命中，靠副本字样排后自动让开。TAXUE_MATERIAL_DIR / IME_MATERIAL_DIR 可覆盖。
    """
    env = _env_path("TAXUE_MATERIAL_DIR", "IME_MATERIAL_DIR")
    if env:
        return env
    for sig in ("05_核心资产库/01_金句弹药库_v2.md", "05_核心资产库"):
        hit = _discover(sig)
        if hit:
            return hit
    return HOME / "Documents/ime-lexicon/素材库"


def persona_path() -> Path:
    """画像 json（知乎技能维护，只读 domains/keywords）。TAXUE_PERSONA / IME_PERSONA 可覆盖。

    位置是技能目录下的 profiles/，这是约定不是发现；技能名改过
    （zhihu-cli → taxueseek-zhihu-cli），所以按通配找，不写死目录名和文件名。
    """
    env = _env_path("TAXUE_PERSONA", "IME_PERSONA")
    if env:
        return env
    for base in (HOME / ".agents/skills", HOME / ".claude/skills", HOME / ".zcode/skills"):
        for prof in sorted(base.glob("*zhihu*/profiles")):
            js = sorted(p for p in prof.glob("*.json") if p.is_file())
            if js:
                return js[0]
    return HOME / "Documents/ime-lexicon/persona.json"


def legacy_dict_dir() -> Path:
    """旧电脑 Win 词库备份（历史文件，位置由用户摆，本技能只读）。

    认法：有 `*微软拼音词库*.txt`——harvest --legacy 要解的就是那个格式。
    TAXUE_LEGACY_DICT / IME_LEGACY_DICT 可覆盖。
    """
    env = _env_path("TAXUE_LEGACY_DICT", "IME_LEGACY_DICT")
    if env:
        return env
    return _discover("*微软拼音词库*.txt") or HOME / "Documents/ime-lexicon/旧电脑词库"


# ══ 输入法自己的本机数据 ════════════════════════════════════════════════


def doubao_account_dirs() -> list[Path]:
    root = HOME / "Library/Application Support/DoubaoIme/EngineUserDictAccounts"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "usr.dat").exists())


def doubao_usr_dat() -> Path | None:
    dirs = doubao_account_dirs()
    if dirs:
        return dirs[0] / "usr.dat"
    legacy = HOME / "Library/Application Support/DoubaoIme/EngineUserDict/usr.dat"
    return legacy if legacy.exists() else None


def doubao_user_dir() -> Path | None:
    dirs = doubao_account_dirs()
    if dirs:
        return dirs[0]
    legacy = HOME / "Library/Application Support/DoubaoIme/EngineUserDict"
    return legacy if legacy.is_dir() else None


def wetype_v5_dir() -> Path | None:
    root = HOME / "Library/Application Support/WeType/userDict/v5"
    if not root.is_dir():
        return None
    conf = root / "db_path.conf"
    if conf.exists():
        name = conf.read_text().strip()
        cand = root / name
        if cand.is_dir():
            return cand
    subs = [p for p in root.iterdir() if p.is_dir()]
    return subs[0] if subs else None


def wetype_hot_dir() -> Path | None:
    p = HOME / "Library/Application Support/WeType/userDict/user_hot_word"
    return p if p.is_dir() else None


def ime_running(engine: str) -> bool:
    import subprocess

    names = {
        "doubao": ["DoubaoIme"],
        "wetype": ["WeType"],
    }.get(engine, [])
    if not names:
        return False
    try:
        out = subprocess.check_output(["pgrep", "-lf", names[0]], text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # CalledProcessError：没匹配进程；FileNotFoundError：非 macOS 没有 pgrep
        return False
    return bool(out.strip())


def shandianshuo_dir() -> Path:
    """闪电说本机数据。应用自己的固定位置，不是用户摆的目录。TAXUE_SPEECH_DIR / IME_SPEECH_DIR 可覆盖。"""
    env = _env_path("TAXUE_SPEECH_DIR", "IME_SPEECH_DIR")
    return env if env else HOME / "Library/Application Support/Shandianshuo"


def xiguashuo_dir() -> Path:
    """西瓜说本机数据。同上。TAXUE_XIGUA_DIR / IME_XIGUA_DIR 可覆盖。"""
    env = _env_path("TAXUE_XIGUA_DIR", "IME_XIGUA_DIR")
    return env if env else HOME / "Library/Application Support/xigua-say"
