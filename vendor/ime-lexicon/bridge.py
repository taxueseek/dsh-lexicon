"""素材库 ↔ 创作词库 ↔ 画像。确定性，不调 LLM。

筛子反过来：先拒 AI 味套话，再留声音/话题/完整术语，缺口交给 acquire 去搜。
harvest / guide / acquire 只读素材和画像，不写回输入法。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import paths
from search import detect_schema, query_plan
from tsvutil import Row, merge_rows

# 金句行：- **"内容"**；洞见行：- 洞见："内容"。
_QUOTE = re.compile(r'^-\s*\*\*"([^"]+)"\*\*\s*$')
_INSIGHT = re.compile(r'^-\s*洞见："([^"]+)"\s*$')
_TOPIC_LINE = re.compile(r"^- 话题：(.+)$")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+\-/.]{1,15}")

# guide 默认读的两份核心 md。不扫全库、不扫归档。
CORE_FILES = (
    "05_核心资产库/01_金句弹药库_v2.md",
    "05_核心资产库/03_洞见观点库_v2.md",
)

# 来自 chinese-write-checker/references/writing-standards.md 第一节，不整文件复制。
_SLOP = frozenset(
    {
        "首先",
        "其次",
        "此外",
        "最后",
        "综上所述",
        "值得注意的是",
        "综合来看",
        "总体而言",
        "不难发现",
        "显而易见",
        "众所周知",
        "毋庸置疑",
        "深度赋能",
        "至关重要",
        "不可或缺",
        "显著提升",
        "令人深思",
        "发人深省",
        "意义深远",
        "不可忽视",
        "令人振奋",
        "令人感动",
        "未来可期",
        "携手共进",
        "共创辉煌",
    }
)

# 功能字：切开长句，不在连续中文里滑窗。
_FUNC_CHAR = set(
    "要却而把被让的如你我他她它到是在了着过就都也才又很最更会能可"
    "与和或但且则即其此某各每何所之于为从对向以给跟比连"
    "不没无只还再已并及乃若因由当将同这那吗呢吧啊嘛呀哇"
    "一得很先"
)

# 二字停用：当切分点，本身不进词表（话题标签除外）。
_STOP2_SPLIT = frozenset(
    {
        "适合",
        "用来",
        "一个",
        "不是",
        "没有",
        "因为",
        "所以",
        "如果",
        "可以",
        "这个",
        "那个",
        "什么",
        "怎么",
        "还是",
        "或者",
        "以及",
        "就是",
        "只是",
        "然后",
        "但是",
        "而且",
        "不过",
        "其实",
        "当然",
        "已经",
        "不会",
        "不能",
        "不要",
        "最好",
        "本身",
        "所谓",
        "这种",
        "那种",
    }
)
_STOP2_SORTED = tuple(sorted(_STOP2_SPLIT, key=len, reverse=True))

_GENERIC2 = frozenset(
    {
        "最好",
        "本身",
        "金钱",
        "模式",
        "坚持",
        "表达",
        "用来",
        "适合",
        "可以",
        "这个",
        "一个",
        "没有",
        "不是",
        "我们",
        "他们",
        "因为",
        "所以",
        "如果",
        "还是",
        "什么",
        "怎么",
        "这样",
        "那样",
        "自己",
        "以及",
        "或者",
        "就是",
        "只是",
        "然后",
        "但是",
        "而且",
        "不过",
        "其实",
        "当然",
        "已经",
        "正在",
        "一直",
        "非常",
        "特别",
        "很多",
        "一些",
        "这些",
        "那些",
        "大家",
        "别人",
        "有人",
        "所有",
        "任何",
        "每个",
        "不会",
        "不能",
        "不要",
        "看看",
        "试试",
        "本质",
        "所谓",
        "这种",
        "那种",
        "策略",
        "问题",
        "判断",
        "普通人",
        "好东西",
        "大部分",
        "有内容",
        "确定",
        "够五斗",
        "复杂精美",
        "策略往往",
        "专家说话",
        "梦里依稀",
        "活生生",
        "服务员",
        "判断标准",
        "简单枯燥",
        "话说清楚",
        "想清楚",
        "模板化",
        "发推特",
        "上班",
        "创造美食",
        "工具",
        "专家说话滴水",
    }
)

_LEFTOVER_PREFIX = set("个式置务")
_VERB_LEAD = set("留看写说用做去来拿走想吃讲谈")
_LOCATIVE = set("中里上下时后前")
_LOCATIVE2 = ("手中", "手里", "身上", "心中")
_BODY_BAD_START = set("上去用生终直多堆先")
# 作者金句里的整段专属短语，切开后会碎，整词收回。
_KEEP_PHRASES = (
    "滴水不漏",
    "五斗米折腰",
    "满嘴跑火车",
    "风险收益比",
    "人话三要素",
)


def harvest_quotes(text: str) -> list[str]:
    """抽引号/加粗里的句子。只认列表项里的 **"…"** 和 洞见："…"，不要整段正文。"""
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if "~~" in s:
            continue
        m = _QUOTE.match(s) or _INSIGHT.match(s)
        if m:
            out.append(m.group(1).strip())
    return out


def is_slop(word: str) -> bool:
    return word.strip() in _SLOP


def _keep_term(word: str, *, allow_2: bool = False) -> bool:
    """术语闸门。套话、功能字残片、二字泛词默认丢掉。"""
    w = word.strip()
    if not w or is_slop(w):
        return False
    if re.search(r"[，。！？、：；]", w):
        return False
    cjk = _CJK_RUN.findall(w)
    cjk_n = sum(len(x) for x in cjk)
    if cjk_n:
        if not (2 <= cjk_n <= 6):
            return False
        if w[0] in _LEFTOVER_PREFIX:
            return False
        if w.endswith(("的", "了", "是", "在", "吧", "吗", "呢", "啊")):
            return False
        if w in _GENERIC2:
            return False
        if any(ch.isdigit() for ch in w):
            return False
        if cjk_n == 2 and not allow_2:
            return False
        return True


def _body_ok(word: str) -> bool:
    """正文抽出的词比话题标签更严：纯汉字、无切边、不带数字。

    拉丁词（AI / ETF / GPT）不走这里：自动抽取层一律不收拉丁词，
    缩写只能从纠错规则、行业表、画像关键词进来，避免从正文捞到垃圾。
    """
    w = word.strip()
    if not _CJK_RUN.fullmatch(w):
        return False
    if w[0] in _BODY_BAD_START:
        return False
    return True


def extract_chunks(text: str) -> list[str]:
    """按标点、二字停用、功能字切开。不滑窗、不贪心 8 字切。"""
    out: list[str] = []
    for clause in re.split(r"[，。！？、：；,.!?;:\s]+", text or ""):
        clause = clause.strip()
        if not clause:
            continue
        for stop in _STOP2_SORTED:
            clause = clause.replace(stop, "|")
        buf: list[str] = []
        cur: list[str] = []
        for ch in clause:
            if ch == "|" or ch in _FUNC_CHAR:
                if cur:
                    buf.append("".join(cur))
                    cur = []
            else:
                cur.append(ch)
        if cur:
            buf.append("".join(cur))
        for part in buf:
            w = part.strip()
            for loc in _LOCATIVE2:
                if w.endswith(loc) and len(w) > len(loc):
                    w = w[: -len(loc)]
                    break
            if len(w) >= 3 and w[-1] in _LOCATIVE:
                w = w[:-1]
            if w and w[0] in _VERB_LEAD and len("".join(_CJK_RUN.findall(w))) > 3:
                w = w[1:]
            if w:
                out.append(w)
    return out


def mantra_set(texts: list[str]) -> set[str]:
    """口头禅：金句/洞见里同一术语出现 ≥3 次，且不是套话。"""
    c: Counter[str] = Counter()
    for t in texts:
        for q in harvest_quotes(t):
            for w in extract_chunks(q):
                if _keep_term(w, allow_2=True) and w not in _GENERIC2:
                    c[w] += 1
    return {w for w, n in c.items() if n >= 3}


def terms_from_text(
    text: str, *, source: str, mantras: set[str] | None = None
) -> list[Row]:
    """话题标签 + 切开的完整术语。二字只收标签或口头禅。"""
    mantras = mantras or set()
    out: list[Row] = []
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if "~~" in s:
            continue
        m = _QUOTE.match(s) or _INSIGHT.match(s)
        if not m:
            continue
        quote = m.group(1).strip()
        note = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            t = lines[j].strip()
            if not t:
                break
            tm = _TOPIC_LINE.match(t)
            if tm:
                note = tm.group(1).strip()
                break
        if note:
            for tag in re.split(r"[、，,/\s]+", note):
                tag = tag.strip()
                if _keep_term(tag, allow_2=True):
                    out.append(Row(word=tag, source=source, freq=8, note=note))
        for phrase in _KEEP_PHRASES:
            if phrase in quote and _keep_term(phrase, allow_2=True):
                out.append(Row(word=phrase, source=source, freq=8, note=note))
        for w in extract_chunks(quote):
            allow_2 = w in mantras
            if not _keep_term(w, allow_2=allow_2):
                continue
            if not allow_2 and not _body_ok(w):
                continue
            out.append(Row(word=w, source=source, freq=8, note=note))
    return merge_rows(out)


def harvest(paths_: list[Path]) -> list[Row]:
    """读指定 md，合并抽词。source 按文件名定 material:金句 / material:洞见。"""
    texts: list[str] = []
    files: list[tuple[Path, str]] = []
    for p in paths_:
        if not p.exists():
            continue
        kind = "洞见" if "洞见" in p.name else "金句" if "金句" in p.name else "素材"
        body = p.read_text(encoding="utf-8")
        texts.append(body)
        files.append((p, kind, body))
    mantras = mantra_set(texts)
    out: list[Row] = []
    for p, kind, body in files:
        out.extend(
            terms_from_text(
                body,
                source=f"material:{kind}",
                mantras=mantras,
            )
        )
    return merge_rows(out)


# 口语里能当词库/创作底盘的短词。其余二字默认丢掉。
_SPEECH_CORE2 = frozenset(
    {
        "投资",
        "基金",
        "赚钱",
        "投顾",
        "财报",
        "指数",
        "估值",
        "素材",
        "券商",
        "港股",
        "股票",
        "红利",
        "壁垒",
        "波动",
        "行情",
    }
)
_SPEECH_CORE3 = frozenset(
    {
        "提示词",
        "素材库",
        "公众号",
        "现金流",
        "阅读量",
        "创业板",
        "德鲁克",
        "阿德勒",
        "少数派",
        "克劳德",
        "浏览器",
        "基金经理",
    }
)
_SPEECH_JUNK_PREFIX = ("么", "些", "来", "较", "们")
_SPEECH_JUNK_SUB = (
    "时候",
    "来说",
    "来看",
    "哪些",
    "失败",
    "表情",
    "请检查",
    "虽然说",
    "非常好",
    "大部分",
    "大家分享",
    "继续任务",
    "继续上述",
    "介绍页面",
)
_INDEX = re.compile(
    r"中证[A-Za-z0-9]{1,6}|沪深\d{3}|沪港深\d{3}|恒生(?:科技|指数)|创业板"
)
# 听错/切错，禁止进语音词库。
_SPEECH_BAD = frozenset(
    {
        "踏雪寻熊",
        "格鲁克",
        "头棒喝",
        "性原理",
        "两个技",
        "搜索技",
    }
)


def parse_norm_rules(text: str) -> list[tuple[str, str]]:
    """闪电说 text_normalization_rules：A => B。"""
    out: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        a, b = line.split("=>", 1)
        src, dst = a.strip(), b.strip()
        if not src or not dst or src == dst:
            continue
        out.append((src, dst))
    return out


def _speech_known() -> set[str]:
    """行业表 + 创作词库：口语里对得上的才算已验证术语。"""
    known: set[str] = set()
    for p in list(paths.industry_dict_dir().glob("行业词库_*.txt")) + [
        paths.creation_dict_dir() / "创作词库.tsv"
    ]:
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            w = ln.split("\t")[0].strip()
            if w and w != "word":
                known.add(w)
    return known


def _speech_keep(word: str, n: int, known: set[str] | None = None) -> bool:
    """只留能当词库和创作底盘的口语词。纠错规则不走这里。"""
    known = known if known is not None else set()
    if is_slop(word) or word.endswith("失败"):
        return False
    if any(word.startswith(p) for p in _SPEECH_JUNK_PREFIX):
        return False
    if any(s in word for s in _SPEECH_JUNK_SUB):
        return False
    if word in _SPEECH_BAD:
        return False
    if word in {
        "有点问题",
        "检查错误",
        "下载文件夹",
        "有攻击性",
        "继续改进",
        "联网搜索",
        "解决问题",
        "问题解决",
        "官方实践",
        "解决方案",
    }:
        return False
    if _INDEX.fullmatch(word):
        return n >= 2
    if word.startswith("踏雪") and 4 <= len(word) <= 6:
        return n >= 2
    if word in _SPEECH_CORE2 or word in _SPEECH_CORE3:
        return n >= 3
    if word in known and len(word) >= 3:
        return n >= 2
    cjk_n = sum(len(x) for x in _CJK_RUN.findall(word))
    if 4 <= cjk_n <= 6 and n >= 3 and _body_ok(word):
        if word.endswith(("出来", "起来", "下去", "分享", "阅读", "测试", "思考")):
            return False
        return True
    return False


def harvest_speech(root: Path | None = None) -> list[Row]:
    """闪电说：纠错规则 + 转写高频词。不读 wav，不 apply。"""
    base = root or paths.shandianshuo_dir()
    out: list[Row] = []
    known: set[str] | None = None  # 行业表+创作词库，闪电说/西瓜说共用一份，读一次
    cfg_p = base / "config.json"
    if cfg_p.exists():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        rules = parse_norm_rules(str((cfg or {}).get("text_normalization_rules") or ""))
        for src, dst in rules:
            if 2 <= len(dst) <= 16:
                out.append(
                    Row(
                        word=dst,
                        source="speech:纠错",
                        freq=12,
                        note=f"{src}=>{dst}",
                    )
                )
    tx_p = base / "transcriptions.json"
    if tx_p.exists():
        try:
            data = json.loads(tx_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        recs = data.get("records") if isinstance(data, dict) else data
        c: Counter[str] = Counter()
        if isinstance(recs, list):
            for rec in recs:
                if not isinstance(rec, dict):
                    continue
                t = (rec.get("text") or "").strip()
                if not t:
                    continue
                for m in _INDEX.finditer(t):
                    c[m.group(0)] += 1
                for phrase in _KEEP_PHRASES:
                    if phrase in t:
                        c[phrase] += 1
                for w in extract_chunks(t):
                    c[w] += 1
        if known is None:
            known = _speech_known()
        for w, n in c.items():
            if _speech_keep(w, n, known):
                out.append(Row(word=w, source="speech:闪电说", freq=min(n, 20)))
    out.extend(_harvest_xigua(known=known))
    return merge_rows(out)


def _harvest_xigua(
    root: Path | None = None, *, known: set[str] | None = None
) -> list[Row]:
    """西瓜说 history。体量远小于闪电说，同一套质量筛。"""
    base = root or paths.xiguashuo_dir()
    fb = base / "xigua-sqlite-fallback.json"
    if not fb.exists():
        return []
    try:
        data = json.loads(fb.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    hist = data.get("history") if isinstance(data, dict) else None
    if not isinstance(hist, list):
        return []
    if known is None:
        known = _speech_known()
    c: Counter[str] = Counter()
    for rec in hist:
        if not isinstance(rec, dict):
            continue
        t = (rec.get("text") or rec.get("audioTranscribe") or "").strip()
        if not t:
            continue
        for m in _INDEX.finditer(t):
            c[m.group(0)] += 1
        for phrase in _KEEP_PHRASES:
            if phrase in t:
                c[phrase] += 1
        for w in extract_chunks(t):
            c[w] += 1
    return [
        Row(word=w, source="speech:西瓜说", freq=min(n, 20))
        for w, n in c.items()
        if _speech_keep(w, n, known or set())
    ]


# —— 会话语料（session-digger 联动）——
# 只读 session-digger 索引的 messages_fts 表（role='USER' 人写正文），不写原始数据。
# 数据契约：这张 FTS5 表是 session-digger 自己的对外检索面；若 schema 变更，此处降级为空。
_SESSION_XML = re.compile(
    r"<\s*/?\s*command\s*-\s*[a-z]+\s*>|<\s*/?\s*system-reminder\s*>"
)
_SESSION_TAG = re.compile(r"<[a-z][a-z0-9_-]*>[^<]{0,120}</[a-z][a-z0-9_-]*>")
_SESSION_PATH = re.compile(r"/Users/\S+|https?://\S+|~/\S+")
# 代码块/行内代码是「复制」不是「打字」，标识符不进词库；正文里的工具名才值得收
_SESSION_CODE = re.compile(r"```.*?```|`[^`\n]{1,100}`", re.S)
# session-digger 的 FTS 存储在 CJK 字符间插了空格（unicode61 预处理），
# 中文原本无空格：两个 CJK 字/标点之间的空白一律是伪空格，合并
_CJK_CTX = r"[\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e\u2014\u2026]"
_SESSION_CJK_SPACE = re.compile(rf"(?<={_CJK_CTX})\s+(?={_CJK_CTX})")
# 会话正文是 markdown/代码密集文本：反引号、星号、斜杠等会把中文词粘死，先全剥成空格；
# 字母数字间的连字符是工具名（session-digger），原地保留
_SESSION_STRIP = re.compile(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])|[^\w\s]+")
_SESSION_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+\-_.]{1,23}")
_SESSION_GENERIC = frozenset(
    """
    the and for with this that from into your you yours are was were have has had
    not but can could should would will shall may might must do does did done
    its his her their our my me him them us who whom which what when where why how
    all any some each every both few more most other such only own same so than
    too very just also now then once here there again still yet ever never
    about above after again against before below between during through under
    over out up down off further while because until since although though
    get got make made take took see saw use used using user users want wanted
    need needed try tried run ran running work worked working word words
    call calls called give gave find found tell told ask asked say said
    look looked looking good great bad well nice best better worse
    time times new old first last next long short little much many
    thing things way ways part parts case cases line lines number
    file files name names data info list list item items value values
    type types key keys test tests main index src lib bin docs readme
    http https localhost com org www json yaml xml toml csv tsv db
    app apps code error errors failed fail success true false none null
    change changes changed update updates updated version command commands
    message messages args tool tools title text total size start stop
    check checked wait waiting write written read reading question
    api key path dir folder group chat session problem question answer
    tmp temp etc var usr opt home foo bar baz node python script
    workspace workflow settings servers scripts providers models modules
    directories standards references operations properties processing
    completed specified required existing expected remaining replacing
    described delegated dedicated achieved allowed actions asked scan scans
    documents desktop downloads library application support darwin arm64 dist
    jan feb mar apr jun jul aug sep oct nov dec defaults instructions
    benchmark boolean builtin combo context runtime matches runs merged
    omitted engines checks chars passes steps state sources string
    """
    .split()
)


def session_index_db(root: Path | None = None) -> Path | None:
    """session-digger 索引位置：显式参数 → SESSION_DIGGER_DATA_DIR → 默认数据目录。"""
    if root:
        return root
    env = os.environ.get("SESSION_DIGGER_DATA_DIR")
    base = Path(env) if env else Path.home() / ".claude" / ".session-digger"
    db = base / "index.db"
    return db if db.is_file() else None


def _clean_session_text(text: str) -> str:
    text = _SESSION_XML.sub(" ", text)  # 只剥标记壳，保留命令参数正文
    text = _SESSION_CODE.sub(" ", text)  # 围栏/行内代码是复制来的，不是打字
    text = re.sub(r"\\+n", " ", text)  # 粘贴文本里 \n、\\n 等转义字面量（否则劈出 nprint 这类伪词）
    text = _SESSION_CJK_SPACE.sub("", text)  # 存储端插进 CJK 字间的伪空格
    text = _SESSION_TAG.sub(" ", text)
    text = _SESSION_PATH.sub(" ", text)
    text = _SESSION_STRIP.sub(lambda m: "-" if m.group(0) == "-" else " ", text)
    return text.replace("_", " ")


def _load_dict_words() -> set[str]:
    """系统英语词典（macOS 自带）。用作「普通英文词」的系统性判据。"""
    p = Path("/usr/share/dict/words")
    try:
        return {
            w.strip().lower()
            for w in p.read_text(encoding="utf-8", errors="ignore").splitlines()
            if w.strip()
        }
    except OSError:
        return set()


_SYSTEM_WORDS: set[str] | None = None


def _dict_stems(tok: str) -> list[str]:
    """web2 只收词头：复数 / -ing / -ed 屈折形式还原后再查。"""
    out = [tok]
    if tok.endswith("ies") and len(tok) > 4:
        out.append(tok[:-3] + "y")
    if tok.endswith("es") and len(tok) > 3:
        out.append(tok[:-2])
    if tok.endswith("s") and len(tok) > 3:
        out.append(tok[:-1])
    if tok.endswith("ing") and len(tok) > 5:
        out.append(tok[:-3])
        out.append(tok[:-3] + "e")
    if tok.endswith("ed") and len(tok) > 4:
        out.append(tok[:-2])
        if len(tok) > 5 and tok[-3] == tok[-4]:  # cancelled → cancel
            out.append(tok[:-3])
    return out


def _is_common_english(tok: str) -> bool:
    global _SYSTEM_WORDS
    if _SYSTEM_WORDS is None:
        _SYSTEM_WORDS = _load_dict_words()
    if not _SYSTEM_WORDS:  # 系统词典缺失（部分 Linux）：退回停用表兜底
        return False
    return any(s in _SYSTEM_WORDS for s in _dict_stems(tok))


def _session_token_ok(tok: str) -> bool:
    if not (3 <= len(tok) <= 24) or tok.isdigit():
        return False
    if tok in _SESSION_GENERIC:
        return False
    if len(tok) >= 6 and any(c.isdigit() for c in tok):
        return False  # y5lwbry3apk / zwum56jxw 这类粘贴进来的随机 ID
    if tok.isalpha() and _is_common_english(tok):
        return False  # 普通英文词系统词库已有，个人词库只收词典外的造词
    if tok[0] == "n" and len(tok) >= 3 and (
        tok[1:] in _SESSION_GENERIC or _is_common_english(tok[1:])
    ):
        return False  # nimport/ndo：转义换行被存储端吃掉反斜杠后的粘连
    return True


def harvest_sessions(
    root: Path | None = None, *, min_token_freq: int = 5
) -> list[Row]:
    """会话语料 → 个人词库候选。只读 index.db 的 USER 正文，不 apply。

    中文词走闪电说同款质量闸；拉丁技术 token 防粘贴刷频（跨会话广度）。
    大语料（≥2000 条消息）上绝对频次没有区分度，地板阈值之外再按
    频次 × 广度排名截头（300 中文 + 150 技术词），产出当候选短名单，
    由代理过目再 import，不是整包入库。
    """
    db = session_index_db(root)
    if not db:
        return []
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT session_id, text FROM messages_fts "
                "WHERE role='USER' AND text IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        # 库不存在 / 被占用 / schema 变更：会话语料路降级为空，不影响其他源
        return []
    known = _speech_known()
    home_name = Path.home().name.lower()  # 用户名不进词库（纪律 8：不泄漏本机路径）
    c: Counter[str] = Counter()
    c_sessions: dict[str, set[str]] = {}
    tok: Counter[str] = Counter()
    tok_sessions: dict[str, set[str]] = {}
    for sid, t in rows:
        text = _clean_session_text(t)
        if not text.strip():
            continue
        for m in _INDEX.finditer(text):
            c[m.group(0)] += 1
            c_sessions.setdefault(m.group(0), set()).add(sid)
        for phrase in _KEEP_PHRASES:
            if phrase in text:
                c[phrase] += 1
                c_sessions.setdefault(phrase, set()).add(sid)
        for w in extract_chunks(text):
            c[w] += 1
            c_sessions.setdefault(w, set()).add(sid)
        for m in _SESSION_TOKEN.finditer(text):
            low = m.group(0).lower()
            tok[low] += 1
            tok_sessions.setdefault(low, set()).add(sid)
    big = len(rows) >= 2000
    ch_n, ch_breadth, top_ch = (4, 3, 300) if big else (3, 2, 100)
    tok_n, tok_breadth, top_tok = (
        (max(min_token_freq, 5), 4, 150) if big else (min_token_freq, 3, 60)
    )
    ch_cands = sorted(
        (
            (w, n, len(c_sessions.get(w, ())))
            for w, n in c.items()
            if n >= ch_n
            and len(c_sessions.get(w, ())) >= ch_breadth
            and _speech_keep(w, n, known)
        ),
        key=lambda x: (-x[1], -x[2], x[0]),
    )
    tok_cands = sorted(
        (
            (w, n, len(tok_sessions.get(w, ())))
            for w, n in tok.items()
            if n >= tok_n
            and len(tok_sessions.get(w, ())) >= tok_breadth
            and w != home_name
            and _session_token_ok(w)
        ),
        key=lambda x: (-x[1], -x[2], x[0]),
    )
    out = [
        Row(word=w, source="sessions:会话", freq=min(n, 20))
        for w, n, _ in ch_cands[:top_ch]
    ]
    out += [
        Row(word=w, source="sessions:技术词", freq=min(n, 20))
        for w, n, _ in tok_cands[:top_tok]
    ]
    return merge_rows(out)


# 旧电脑现场词：养殖/兽医/产线。细胞词库（scel）不收。
_LEGACY_MARK = re.compile(
    r"猪|苗|奶|疫|饲|产子|产仔|断奶|日龄|配种|消毒|分娩|保育|保温|肌注|预产|吨袋|筛分|坩埚|压实|洗锅|技术仓|粉末|振实|粘度"
)


def _legacy_ok(word: str) -> bool:
    if word in _SPEECH_BAD or is_slop(word):
        return False
    if re.search(r"(.)\1{2,}", word):
        return False
    if word.endswith(("的", "了", "吧", "吗", "呢", "啊", "是", "在")):
        return False
    if word.startswith(("的", "及", "号", "头", "对", "每", "未", "刚", "与")):
        return False
    n = len(word)
    if n >= 4 and word[: n // 2] == word[n // 2 :]:
        return False
    cn = sum(len(x) for x in _CJK_RUN.findall(word))
    return 2 <= cn <= 6


def harvest_legacy(root: Path | None = None) -> list[Row]:
    """Win/旧电脑：搜狗带频次导出 + 讯飞用户词 + 工作词。不读 scel/bin。"""
    base = root or paths.legacy_dict_dir()
    out: list[Row] = []
    work = base / "工作词库.txt"
    if not work.exists():
        work = base / "词库" / "工作词库.txt"
    if work.exists():
        for raw in work.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = re.match(r"^([\u4e00-\u9fff]{2,8})", raw.strip())
            if m and _legacy_ok(m.group(1)):
                out.append(Row(word=m.group(1), source="legacy:工作", freq=10))
    sogou = base / "词库" / "搜狗词库2021_8.txt"
    if sogou.exists():
        for raw in sogou.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            parts = raw.rstrip("\r").split("\t")
            if len(parts) < 3:
                continue
            w = parts[0].strip()
            try:
                fr = int(parts[2])
            except ValueError:
                continue
            if fr < 5 or not _legacy_ok(w) or not _LEGACY_MARK.search(w):
                continue
            out.append(Row(word=w, source="legacy:搜狗2021", freq=min(fr, 20)))
    ms = base / "2017 Win10微软拼音词库.txt"
    if ms.exists():
        try:
            from ms_pinyin import parse_file as parse_ms

            for w, _py, rank in parse_ms(ms):
                if rank < 1 or not _legacy_ok(w) or not _LEGACY_MARK.search(w):
                    continue
                out.append(
                    Row(word=w, source="legacy:微软拼音", freq=min(max(rank, 5), 20))
                )
        except (ValueError, OSError):
            pass
    ifly = base / "词库小狼毫.txt"
    if ifly.exists():
        for raw in ifly.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            w = raw.strip()
            if not w or w.startswith("#") or w.startswith("###"):
                continue
            if _LEGACY_MARK.search(w) and _legacy_ok(w) and 3 <= len(w) <= 6:
                out.append(Row(word=w, source="legacy:讯飞", freq=8))
    return merge_rows(out)


def topic_keywords(topic: str, schema: str) -> set[str]:
    """话题关键词：话题原文 + schema 名 + 2-4 字重叠窗口（如 投资理财 → 投资/理财）。"""
    kws = {topic, schema}
    for chunk in _CJK_RUN.findall(topic):
        for n in (2, 3, 4):
            for i in range(len(chunk) - n + 1):
                kws.add(chunk[i : i + n])
    return {k for k in kws if k and k.strip()}


def industry_files(topic: str) -> list[Path]:
    """按 topic 模糊匹配 行业词库/行业词库_*.txt。"""
    kws = topic_keywords(topic, detect_schema(topic))
    out: list[Path] = []
    for p in sorted(paths.industry_dict_dir().glob("行业词库_*.txt")):
        name = p.stem[len("行业词库_") :]
        if any(k in name for k in kws):
            out.append(p)
    return out


def _material_quotes() -> list[str]:
    base = paths.material_dir()
    out: list[str] = []
    for rel in CORE_FILES:
        p = base / rel
        if p.exists():
            out.extend(harvest_quotes(p.read_text(encoding="utf-8")))
    return out


def load_persona(path: Path | None = None) -> dict[str, object]:
    """只取 expertise_domains 的 name/keywords/topics。缺文件当空。"""
    p = path or paths.persona_path()
    if not p.exists():
        return {"domains": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"domains": []}
    domains: list[dict[str, object]] = []
    for d in raw.get("expertise_domains") or []:
        if not isinstance(d, dict):
            continue
        kws: list[str] = []
        for key in ("keywords", "topics"):
            for x in d.get(key) or []:
                if isinstance(x, str) and 2 <= len(x) <= 8 and not is_slop(x):
                    kws.append(x)
        domains.append({"name": str(d.get("name") or ""), "keywords": kws})
    return {"domains": domains}


def persona_keywords(topic: str, *, path: Path | None = None) -> list[str]:
    """命中话题的画像领域词。不回传 user_id。"""
    schema = detect_schema(topic)
    kws = topic_keywords(topic, schema)
    out: list[str] = []
    for d in load_persona(path)["domains"]:
        name = str(d.get("name") or "")
        words = list(d.get("keywords") or [])
        hay = name + " " + " ".join(str(x) for x in words)
        if any(k in hay for k in kws) or any(k in topic for k in (name,) if k):
            if 2 <= len(name) <= 8 and not is_slop(name):
                out.append(name)
            out.extend(str(x) for x in words)
    return list(dict.fromkeys(out))


def guide(
    topic: str, lexicons: list[list[Row]], *, limit: int = 40
) -> dict[str, object]:
    """出用词包。

    use：word/note/source 含话题关键词的行业或创作词；
    voice：source 含 material 的惯用词；
    gap：话题金句里抽出、词库没有的术语，最多 20。
    """
    schema = detect_schema(topic)
    kws = topic_keywords(topic, schema)
    rows = merge_rows([r for group in lexicons for r in group])
    known = {r.word for r in rows}
    use: list[str] = []
    voice: list[str] = []
    for r in rows:
        src = r.source or ""
        if not _keep_term(r.word, allow_2=True):
            continue
        hay = f"{r.word} {r.note} {src}"
        hit = any(k in hay for k in kws)
        if "material:" in src or src.startswith("speech:"):
            if hit:
                voice.append(r.word)
            continue
        if hit:
            use.append(r.word)
    gap: list[str] = []
    for q in _material_quotes():
        if not any(k in q for k in kws):
            continue
        for phrase in _KEEP_PHRASES:
            if phrase in q and phrase not in known and phrase not in gap:
                gap.append(phrase)
        for w in extract_chunks(q):
            if (
                _keep_term(w)
                and _body_ok(w)
                and w not in known
                and w not in gap
            ):
                gap.append(w)
    return {
        "schema": schema,
        "use": list(dict.fromkeys(use))[:limit],
        "voice": list(dict.fromkeys(voice))[:limit],
        "gap": gap[:20],
    }


def acquire(
    topic: str,
    lexicons: list[list[Row]],
    *,
    persona: Path | None = None,
) -> dict[str, object]:
    """画像 + 素材好词 − 已有词表 = 缺口；queries 给 search/build。"""
    schema = detect_schema(topic)
    rows = merge_rows([r for group in lexicons for r in group])
    known = {r.word for r in rows}
    persona_kws = persona_keywords(topic, path=persona)
    kws = topic_keywords(topic, schema)
    material_terms: list[str] = []
    for q in _material_quotes():
        if not any(k in q for k in kws):
            continue
        for phrase in _KEEP_PHRASES:
            if phrase in q:
                material_terms.append(phrase)
        for w in extract_chunks(q):
            if _keep_term(w, allow_2=w in persona_kws) and (
                w in persona_kws or _body_ok(w)
            ):
                material_terms.append(w)
    gap: list[str] = []
    for w in persona_kws + material_terms:
        allow_2 = w in persona_kws
        if not _keep_term(w, allow_2=allow_2):
            continue
        if w not in known and w not in gap:
            gap.append(w)
    have = [w for w in persona_kws if w in known]
    queries: list[str] = []
    for step in query_plan(topic):
        q = str(step.get("query") or "")
        if q and q not in queries:
            queries.append(q)
    for w in gap[:3]:
        q = f"{w} 专业术语"
        if q not in queries:
            queries.append(q)
    return {
        "schema": schema,
        "have": have[:40],
        "gap": gap[:40],
        "queries": queries[:8],
    }
