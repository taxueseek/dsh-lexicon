#!/usr/bin/env python3
"""个人词库文件层导入导出 + 建库 / 修正。

  python3 ime_lexicon.py detect
  python3 ime_lexicon.py export --engine doubao|wetype|all
  python3 ime_lexicon.py import --engine doubao --from FILE --staging DIR
  python3 ime_lexicon.py apply  --engine doubao --staging DIR   # IME 必须退出
  python3 ime_lexicon.py backup
  python3 ime_lexicon.py search --topic 锂电池
  python3 ime_lexicon.py search --topic 锂电池 --run --out /tmp/术语.txt
  python3 ime_lexicon.py search --topic 锂电池 --engine generic --run
  python3 ime_lexicon.py build --topic 锂电池 --from 词表.txt
  python3 ime_lexicon.py optimize --from a.tsv --from b.tsv --out DIR
  python3 ime_lexicon.py harvest                       # 金句/洞见 → 创作词库
  python3 ime_lexicon.py harvest --speech              # 闪电说转写/纠错 → 语音词库
  python3 ime_lexicon.py guide --topic 投资理财        # 动笔前出用词包
  python3 ime_lexicon.py acquire --topic 投资理财      # 画像+素材缺口 → 搜索计划

永远先拷后改。apply 才会动正在用的文件。harvest/guide/acquire 只读素材、只出表，不 apply。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import budget as lexbudget  # noqa: E402
import bridge as lexbridge  # noqa: E402
import build as lexbuild  # noqa: E402
import doubao  # noqa: E402
import optimize as lexopt  # noqa: E402
import paths  # noqa: E402
import search as lexsearch  # noqa: E402
import tsvutil  # noqa: E402
import wetype  # noqa: E402
from clean_dict import clean_industry  # noqa: E402
from doubao import Item  # noqa: E402


def cmd_detect(_: argparse.Namespace) -> int:
    du = paths.doubao_usr_dat()
    wu = paths.wetype_v5_dir()
    print(f"doubao_usr\t{'found' if du else '-'}")
    print(f"doubao_running\t{paths.ime_running('doubao')}")
    if du:
        items = doubao.decode_file(du)
        caps = doubao.header_caps(du)
        dup_slots = len(items) - len({(it.word, it.pinyin) for it in items})
        print(f"doubao_items\t{len(items)}")
        print(f"doubao_unique\t{len({it.word for it in items})}")
        print(f"doubao_dup_slots\t{dup_slots}")
        if items and dup_slots / len(items) > 0.15:
            print(f"note\t重复率 {dup_slots / len(items):.0%}，建议跑 dedup 一键去重")
        print(f"doubao_slots\t{caps['slots']}")
        print(f"doubao_used\t{caps['used']}")
        print(f"doubao_word_cap\t{caps['word_cap']}")
        print("doubao_slot_note\t一条词占一槽，不是汉字数；槽数随豆包版本变（5000/7500/10000），以上面实读为准")
    print(f"lexicon_home\t{paths.lexicon_home()}")
    print(f"wetype_v5\t{'found' if wu else '-'}")
    print(f"wetype_running\t{paths.ime_running('wetype')}")
    if wu:
        witems = wetype.decode_dir(wu)
        print(f"wetype_items\t{len(witems)}")
        print(f"wetype_unique\t{len({it.word for it in witems})}")
    return 0


def cmd_init(ns: argparse.Namespace) -> int:
    """把词库布局立起来：工作区下 `词库/<类型>/`，缺就建。新环境跑一次即可。"""
    home = paths.lexicon_home()
    print(f"lexicon_home\t{home}")
    for d in paths.ensure_dict_dirs():
        print(f"kind\t{d.name}\t{d}")
    return 0


def _out_base(ns: argparse.Namespace, engine: str) -> Path:
    """导出落点：给了 --out 就写那儿，否则落到词库根下对应类型目录（缺就建）。"""
    if ns.out:
        return Path(ns.out)
    return paths.dict_dir("豆包词库" if engine == "doubao" else "微信词库", create=True)


def _dump_items_tsv(items: list[Item], dest: Path) -> None:
    """豆包全字段导出。word/pinyin 里混进制表换行会错列，先压成空格。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as f:
        f.write("word\tpinyin\tfreq\tptime\tstamp\tflags\tsyll\n")
        for it in items:
            word = " ".join(it.word.split())
            py = " ".join(it.pinyin.split())
            syll = ",".join(str(x) for x in it.syll)
            f.write(f"{word}\t{py}\t{it.freq}\t{it.ptime}\t{it.stamp}\t{it.flags}\t{syll}\n")


def cmd_export(ns: argparse.Namespace) -> int:
    engines = ["doubao", "wetype"] if ns.engine == "all" else [ns.engine]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for eng in engines:
        if eng == "doubao":
            src = Path(ns.src) if ns.src else paths.doubao_usr_dat()
            if not src or not src.exists():
                print("doubao usr.dat 不存在", file=sys.stderr)
                return 2
            work = Path("/tmp/ime-lexicon-ro") / stamp / "doubao"
            work.mkdir(parents=True, exist_ok=True)
            copy = work / "usr.dat"
            shutil.copy2(src, copy)
            items = doubao.decode_file(copy)
            dest = _out_base(ns, "doubao") / f"个人词库_{stamp}.tsv"
            _dump_items_tsv(items, dest)
            print(f"doubao\t{len(items)}\t{dest}")
        elif eng == "wetype":
            srcd = Path(ns.src) if ns.src else paths.wetype_v5_dir()
            if not srcd:
                print("WeType v5 目录不存在", file=sys.stderr)
                return 2
            dest = _out_base(ns, "wetype") / f"个人词库_{stamp}.tsv"
            info = wetype.export_dir(srcd, dest)
            print(f"wetype\t{info['count']}\t{dest}")
    return 0


def _load_incoming(path: Path) -> list[Item]:
    if path.suffix == ".jsonl":
        items: list[Item] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            syll = o.get("syll") or []
            if isinstance(syll, str):
                syll = [int(x) for x in syll.split(",") if x]
            items.append(
                Item(
                    word=o["word"],
                    pinyin=o.get("pinyin") or "",
                    freq=int(o.get("freq") or 1),
                    ptime=int(o.get("ptime") or 0),
                    stamp=int(o.get("stamp") or 0),
                    flags=int(o.get("flags") or 0),
                    syll=list(syll),
                )
            )
        return items
    rows = tsvutil.load(path)
    out: list[Item] = []
    for r in rows:
        syll = [int(x) for x in r.syll.split(",") if x]
        extra = r.extra
        out.append(
            Item(
                word=r.word,
                pinyin=r.pinyin,
                freq=r.freq,
                ptime=int(extra.get("ptime") or 0),
                stamp=int(extra.get("stamp") or 0),
                flags=int(extra.get("flags") or 0),
                syll=syll,
            )
        )
    return out


def _rows_from_items(items: list[Item], source: str) -> list[tsvutil.Row]:
    return [
        tsvutil.Row(
            word=it.word,
            pinyin=it.pinyin,
            freq=it.freq,
            source=source,
            syll=",".join(str(x) for x in it.syll),
        )
        for it in items
    ]


def _items_from_rows(rows: list[tsvutil.Row], by_key: dict[tuple[str, str], Item]) -> list[Item]:
    out: list[Item] = []
    for r in rows:
        it = by_key.get(r.key())
        if it is None:
            syll = [int(x) for x in r.syll.split(",") if x]
            it = Item(word=r.word, pinyin=r.pinyin, freq=r.freq, syll=syll)
        else:
            it.freq = r.freq
        out.append(it)
    return out


def cmd_import(ns: argparse.Namespace) -> int:
    try:
        incoming = _load_incoming(Path(ns.src_file))
    except (ValueError, UnicodeDecodeError, KeyError, json.JSONDecodeError) as e:
        print(
            f"读不了 {ns.src_file}：{e}。只支持 UTF-8 的 TSV/CSV/JSONL，其他格式先转换再喂",
            file=sys.stderr,
        )
        return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    staging = Path(ns.staging) if ns.staging else Path("/tmp/ime-lexicon-staging") / f"{ns.engine}_{stamp}"
    if ns.engine == "doubao":
        live = Path(ns.live) if ns.live else paths.doubao_usr_dat()
        if not live:
            print("找不到豆包 usr.dat", file=sys.stderr)
            return 2
        if not live.exists():
            print(f"live 不存在：{live}", file=sys.stderr)
            return 2
        staging.mkdir(parents=True, exist_ok=True)
        base = staging / "usr.dat.src"
        shutil.copy2(live, base)
        existing = doubao.decode_file(base)
        smap = doubao.build_syll_map(existing)
        filled: list[Item] = []
        skipped = 0
        blocked = 0
        for it in incoming:
            row = tsvutil.Row(word=it.word, pinyin=it.pinyin, freq=it.freq, source="import")
            if not lexbuild.apply_ok(row) and not it.pinyin:
                print(f"skip\t{it.word}\t大词库/无拼音，禁止 apply", file=sys.stderr)
                blocked += 1
                continue
            if not it.syll:
                try:
                    it.syll = doubao.encode_syllables(it.pinyin or it.word, it.word, smap)
                except ValueError as e:
                    print(f"skip\t{it.word}\t{e}", file=sys.stderr)
                    skipped += 1
                    continue
            filled.append(it)
        if ns.replace:
            # 整表替换（消错）：optimize 后的表作为目标库，不并入 live，
            # 从而剔除 live 里已被 optimize 改写/删除的旧词（如 agnet、噪声 lijigang）。
            if ns.budget:
                in_rows = _rows_from_items(filled, "import")
                clipped, stats = lexbudget.clip(
                    in_rows, limit=ns.budget, prefer_personal=not ns.no_prefer_personal
                )
                by_key = {(it.word, it.pinyin): it for it in filled}
                merged = _items_from_rows(clipped, by_key)
                print(f"budget\t{stats['limit']}")
                print(f"kept_personal\t{stats['kept_personal']}")
                print(f"added\t{stats['added']}")
                print(f"dropped\t{stats['dropped']}")
            else:
                # --replace 同样按 (word, pinyin) 去重：重复键只留频次最高那条，
                # 否则引擎自己攒出来的重复槽会原样带进 rebuild（实测 3 条同键写 3 槽）
                ded: dict[tuple[str, str], Item] = {}
                for it in filled:
                    k = (it.word, it.pinyin)
                    if k not in ded or it.freq > ded[k].freq:
                        ded[k] = it
                merged = list(ded.values())
            print("replace\t整表替换：不并入 live，优化表即目标库")
        elif ns.budget:
            exist_rows = _rows_from_items(existing, "doubao")
            in_rows = _rows_from_items(filled, "import")
            clipped, stats = lexbudget.clip(
                exist_rows + in_rows, limit=ns.budget, prefer_personal=not ns.no_prefer_personal
            )
            by_key = {(it.word, it.pinyin): it for it in existing + filled}
            merged = _items_from_rows(clipped, by_key)
            print(f"budget\t{stats['limit']}")
            print(f"kept_personal\t{stats['kept_personal']}")
            print(f"added\t{stats['added']}")
            print(f"dropped\t{stats['dropped']}")
        else:
            merged = doubao.merge_items(existing, filled)
        dest = staging / "usr.dat"
        info = doubao.rebuild(base, merged, dest)
        # 回读校验
        check = doubao.decode_file(dest)
        print(f"staging\t{dest}")
        print(f"incoming\t{len(incoming)}")
        print(f"encoded\t{len(filled)}")
        print(f"skipped\t{skipped}")
        print(f"blocked\t{blocked}")
        print(f"merged\t{info['count']}")
        print(f"reread\t{len(check)}")
        if len(check) != info["count"]:
            print(f"error\t回读 {len(check)} != 写入 {info['count']}，staging 不可信，不要 apply", file=sys.stderr)
            return 4
        return 0
    if ns.engine == "wetype":
        live = Path(ns.live) if ns.live else paths.wetype_v5_dir()
        if not live or not live.is_dir():
            print("找不到 WeType v5", file=sys.stderr)
            return 2
        # 与豆包分支同款准入闸：微信键要求 ASCII 拼音，无拼音中文词直接注入必崩
        witems: list[wetype.WItem] = []
        skipped = 0
        for it in incoming:
            py = (it.pinyin or it.word).strip()
            if not py.isascii():
                print(f"skip\t{it.word}\t微信键需要 ASCII 拼音（pinyin={it.pinyin!r}）", file=sys.stderr)
                skipped += 1
                continue
            witems.append(wetype.WItem(word=it.word, pinyin=it.pinyin, freq=it.freq, kind="import"))
        try:
            info = wetype.stage_import(live, staging, witems)
        except ValueError as e:
            print(f"error\t{e}", file=sys.stderr)
            return 2
        print(f"staging\t{info['staging']}")
        print(f"plan\t{info['plan']}")
        print(f"incoming\t{info['incoming']}")
        print(f"skipped\t{skipped}")
        print(f"written\t{info['written']}")
        print(f"reread\t{info['reread']}")
        if info["missing"]:
            print(f"missing\t{','.join(info['missing'])}")
            return 4
        print("note\t已写入 staging WAL；apply 前输入法必须退出")
        return 0
    print("未知引擎", file=sys.stderr)
    return 2


def cmd_dedup(ns: argparse.Namespace) -> int:
    """一键去重：detect → 原位裁剪 → staging 校验；--apply 且输入法已退出才写 live。"""
    live = Path(ns.live) if ns.live else paths.doubao_usr_dat()
    if not live or not live.exists():
        print("找不到豆包 usr.dat", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = Path("/tmp/ime-lexicon-ro") / stamp / "dedup"
    work.mkdir(parents=True, exist_ok=True)
    base = work / "usr.dat"
    shutil.copy2(live, base)  # 先拷后读
    items = doubao.decode_file(base)
    uniq = len({(it.word, it.pinyin) for it in items})
    dup_slots = len(items) - uniq
    print(f"before\t{len(items)}\tunique={uniq}\tdup_slots={dup_slots}")
    if dup_slots == 0:
        print("note\t无重复，无需去重")
        return 0
    staging = Path(ns.staging) if ns.staging else Path("/tmp/ime-lexicon-staging") / f"doubao_dedup_{stamp}"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base, staging / "usr.dat.src")  # 保持 apply 兼容布局
    info = doubao.prune_inplace(staging / "usr.dat.src", staging / "usr.dat")
    check = doubao.decode_file(staging / "usr.dat")
    keys = [(it.word, it.pinyin) for it in check]
    if len(keys) != len(set(keys)):
        print("error\tstaging 仍有重复，不要 apply", file=sys.stderr)
        return 4
    caps = doubao.header_caps(staging / "usr.dat")
    print(f"after\t{info['kept']}\tfreed={info['dropped']}\tused={caps['used']}")
    print(f"staging\t{staging / 'usr.dat'}")
    if ns.apply:
        return cmd_apply(argparse.Namespace(engine="doubao", staging=str(staging), live=str(live)))
    print("note\t确认后运行: ime_lexicon.py apply --engine doubao --staging " + str(staging))
    return 0


def cmd_apply(ns: argparse.Namespace) -> int:
    if paths.ime_running(ns.engine):
        print(f"{ns.engine} 正在运行，拒绝覆盖。先退出输入法再 apply。", file=sys.stderr)
        return 3
    staging = Path(ns.staging)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if ns.engine == "doubao":
        live = Path(ns.live) if ns.live else paths.doubao_usr_dat()
        src = staging / "usr.dat"
        if not live or not src.exists():
            print("缺少 live 或 staging/usr.dat", file=sys.stderr)
            return 2
        try:
            doubao.decode_file(src)  # apply 前先确认 staging 可解析，防坏文件覆盖 live
        except ValueError as e:
            print(f"staging/usr.dat 损坏，拒绝 apply：{e}", file=sys.stderr)
            return 2
        bak = live.with_name(f"usr.dat.bak_{stamp}")
        shutil.copy2(live, bak)
        shutil.copy2(src, live)
        print(f"backup\t{bak}")
        print(f"applied\t{live}")
        return 0
    if ns.engine == "wetype":
        live = Path(ns.live) if ns.live else paths.wetype_v5_dir()
        if not live or not staging.is_dir():
            print("缺少 live 或 staging", file=sys.stderr)
            return 2
        # live 自 staging 之后若推进过日志（输入法又运行过），
        # 注入词会落在旧 log 里被引擎无视，apply 却报成功——先拦下
        stag_logs = sorted(staging.glob("*.log"))
        if stag_logs:
            live_logs = sorted(live.glob("*.log"))
            if not live_logs or live_logs[-1].name != stag_logs[-1].name:
                print(
                    f"live 日志与 staging 不一致（staging: {stag_logs[-1].name}，"
                    f"live: {live_logs[-1].name if live_logs else '无'}），staging 已过期，请重新 import",
                    file=sys.stderr,
                )
                return 4
        bak = live.parent / f"{live.name}.bak_{stamp}"
        shutil.copytree(live, bak)
        # 只覆盖 ldb/log/manifest，保留 LOCK 处理
        for p in staging.iterdir():
            if p.name in {"inject.jsonl", "LOCK"}:
                continue
            dest = live / p.name
            if p.is_file():
                shutil.copy2(p, dest)
        print(f"backup\t{bak}")
        print(f"applied\t{live}")
        return 0
    return 2


def cmd_backup(ns: argparse.Namespace) -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(ns.out) if ns.out else paths.dict_dir("备份", create=True) / stamp
    du = paths.doubao_usr_dat()
    ddir = paths.doubao_user_dir()
    wu = paths.wetype_v5_dir()
    if not (du or wu):
        print("豆包和微信输入法词库都没找到，未备份任何东西", file=sys.stderr)
        return 2
    base.mkdir(parents=True, exist_ok=True)
    if du and ddir:
        dest = base / "豆包输入法"
        dest.mkdir(exist_ok=True)
        for name in ("usr.dat", "usr_side_dict.dat", "common_phrase_dict.dat"):
            p = ddir / name
            if p.exists():
                shutil.copy2(p, dest / name)
        items = doubao.decode_file(dest / "usr.dat" if (dest / "usr.dat").exists() else du)
        _dump_items_tsv(items, dest / "个人词库.tsv")
        print(f"doubao\t{len(items)}\t{dest}")
    if wu:
        dest = base / "微信输入法"
        shutil.copytree(wu, dest / "v5", dirs_exist_ok=True)
        info = wetype.export_dir(wu, dest / "个人词库.tsv")
        print(f"wetype\t{info['count']}\t{dest}")
    print(f"backup\t{base}")
    return 0


def cmd_search(ns: argparse.Namespace) -> int:
    topic = (ns.topic or "").strip()
    if not topic:
        print("search 需要 --topic", file=sys.stderr)
        return 2
    engine = ns.engine or "auto"
    backend = engine if engine != "auto" else lexsearch.detect_backend()
    print(f"topic\t{topic}")
    print(f"schema\t{lexsearch.detect_schema(topic)}")
    print(f"backend\t{backend}")
    if backend == "none":
        print("note\t无 argo / ddgs；代理用宿主通用搜索跑下面 3 路，结果存文件再 --from")
    for step in lexsearch.query_plan(topic, backend=None if engine == "auto" else engine):
        print(f"query\t{step['query']}")
        print(f"cmd\t{step['cmd']}")
        if step.get("fallback"):
            print(f"fallback\t{step['fallback']}")
    words: list[str] = []
    if ns.src_files:
        files = [Path(p) for p in ns.src_files]
        for p in files:
            if not p.exists():
                print(f"找不到 {p}", file=sys.stderr)
                return 2
        words.extend(lexsearch.extract_from_files(files))
    if ns.run:
        try:
            got, reports = lexsearch.run_search(topic, engine=engine)
        except FileNotFoundError as e:
            print(f"search\t{e}", file=sys.stderr)
            return 2
        for rec in reports:
            print(f"{rec['backend']}\t{rec['status']}\t{rec['terms']}\t{rec['query']}")
        words.extend(got)
        words = clean_industry(words)
    if not ns.run and not ns.src_files:
        return 0
    dest = Path(ns.out) if ns.out else None
    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        print(f"search\t{len(words)}\t{dest}")
    else:
        print(f"search\t{len(words)}")
        for w in words:
            print(w)
    return 0


def cmd_build(ns: argparse.Namespace) -> int:
    files = [Path(p) for p in (ns.src_files or [])]
    for p in files:
        if not p.exists():
            print(f"找不到 {p}", file=sys.stderr)
            return 2
    try:
        rows = lexbuild.build(
            files, ns.terms or [], topic=ns.topic, prose=ns.prose, seeds=ns.seeds or []
        )
    except (ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 2
    topic = ns.topic or "custom"
    dest = Path(ns.out) if ns.out else paths.industry_dict_dir() / f"行业词库_{topic}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    layers = lexbuild.layer_rows(rows)
    flat = [r for layer in lexbuild.LAYERS for r in layers[layer]]
    ok = [r for r in flat if lexbuild.apply_ok(r)]
    if dest.suffix.lower() == ".tsv":
        tsvutil.dump(dest, flat)
    else:
        dest.write_text("\n".join(r.word for r in flat) + ("\n" if flat else ""), encoding="utf-8")
    stem = dest.with_suffix("")
    tsvutil.dump(Path(f"{stem}_核心.tsv"), layers["核心"])
    tsvutil.dump(Path(f"{stem}_扩展.tsv"), layers["扩展"])
    tsvutil.dump(Path(f"{stem}_大词库.tsv"), layers["大词库"])
    print(f"build\t{len(flat)}\t{dest}")
    for cat, n in lexbuild.category_counts(rows, topic=topic).items():
        print(f"cat\t{cat}\t{n}")
    for name in lexbuild.LAYERS:
        print(f"layer\t{name}\t{len(layers[name])}")
    print(f"apply_ok\t{len(ok)}")
    if layers["大词库"]:
        print("note\t大词库无拼音或未注音，禁止作为 apply 源")
    return 0


def cmd_optimize(ns: argparse.Namespace) -> int:
    files = [Path(p) for p in ns.src_files]
    rows: list[tsvutil.Row] = []
    for p in files:
        if not p.exists():
            print(f"找不到 {p}", file=sys.stderr)
            return 2
        rows.extend(tsvutil.load(p))
    drop = set(ns.drop or [])
    kept, changed, review = lexopt.optimize(
        rows,
        drop=drop,
        auto=not ns.no_auto,
        drop_fragments=ns.drop_fragments,
        profile=ns.profile,
    )
    if ns.budget:
        kept, stats = lexbudget.clip(
            kept, limit=ns.budget, prefer_personal=not ns.no_prefer_personal
        )
    out = Path(ns.out) if ns.out else paths.dict_dir("整合", create=True) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    tsvutil.dump(out / "词库.tsv", kept)
    tsvutil.dump(out / "更正记录.tsv", changed)
    tsvutil.dump(out / "待确认.tsv", review)
    print(f"in\t{len(rows)}")
    print(f"out\t{len(kept)}\t{out / '词库.tsv'}")
    print(f"fixed\t{len(changed)}")
    print(f"review\t{len(review)}")
    print(f"profile\t{ns.profile}")
    if ns.budget:
        print(f"budget\t{stats['limit']}")
        print(f"kept_personal\t{stats['kept_personal']}")
        print(f"added\t{stats['added']}")
        print(f"dropped\t{stats['dropped']}")
    return 0


def cmd_harvest(ns: argparse.Namespace) -> int:
    """金句/洞见 → 创作词库；--speech 走闪电说转写/纠错 → 语音词库；--sessions 走会话语料。不 apply。"""
    if ns.sessions:
        rows = lexbridge.harvest_sessions()
        dest = Path(ns.out) if ns.out else paths.dict_dir("创作词库", create=True) / "会话语料.tsv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tsvutil.dump(dest, rows)
        print(f"harvest\t{len(rows)}\t{dest}")
        if not rows:
            print("skip\t无 session-digger 索引（SESSION_DIGGER_DATA_DIR 或 ~/.claude/.session-digger）")
        return 0 if rows else 2
    if ns.speech:
        rows = lexbridge.harvest_speech()
        dest = Path(ns.out) if ns.out else paths.dict_dir("创作词库", create=True) / "语音词库.tsv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tsvutil.dump(dest, rows)
        print(f"harvest\t{len(rows)}\t{dest}")
        if not rows:
            print("skip\t无闪电说 config/transcriptions（TAXUE_SPEECH_DIR）")
        return 0 if rows else 2
    if ns.legacy:
        rows = lexbridge.harvest_legacy()
        dest = Path(ns.out) if ns.out else paths.dict_dir("创作词库", create=True) / "旧电脑词库.tsv"
        # 顺带落下微软拼音全文解析，供核对；质量筛仍只进旧电脑词库
        ms = paths.legacy_dict_dir() / "2017 Win10微软拼音词库.txt"
        if ms.exists():
            try:
                from ms_pinyin import parse_file as parse_ms

                raw = parse_ms(ms)
                dump = paths.dict_dir("创作词库", create=True) / "旧电脑_微软拼音.tsv"
                dump.write_text(
                    "word\tpinyin\tfreq\tsource\tsyll\tnote\n"
                    + "".join(
                        f"{w}\t{py}\t{rk}\tlegacy:微软拼音\t\t\n" for w, py, rk in raw
                    ),
                    encoding="utf-8",
                )
                print(f"ms_pinyin\t{len(raw)}\t{dump}")
            except (ValueError, OSError) as e:
                print(f"skip\t微软拼音 {e}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tsvutil.dump(dest, rows)
        print(f"harvest\t{len(rows)}\t{dest}")
        if not rows:
            print("skip\t无旧电脑词库（TAXUE_LEGACY_DICT）")
        return 0 if rows else 2
    files = [Path(p) for p in (ns.src_files or [])]
    if not files:
        base = paths.material_dir()
        cands = [base / rel for rel in lexbridge.CORE_FILES]
        files = []
        for p in cands:
            if p.exists():
                files.append(p)
            else:
                print(f"skip\t{p}")
    if not files:
        print("harvest\t无素材文件（TAXUE_MATERIAL_DIR 下没有核心 md）", file=sys.stderr)
        return 2
    rows = lexbridge.harvest(files)
    dest = Path(ns.out) if ns.out else paths.dict_dir("创作词库", create=True) / "创作词库.tsv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tsvutil.dump(dest, rows)
    print(f"harvest\t{len(rows)}\t{dest}")
    return 0


def cmd_guide(ns: argparse.Namespace) -> int:
    """动笔前出用词包：use / voice / gap。只读词表，不 apply。"""
    topic = (ns.topic or "").strip()
    if not topic:
        print("guide 需要 --topic", file=sys.stderr)
        return 2
    files = [Path(p) for p in (ns.src_files or [])]
    if not files:
        files = _default_lexicon_files(topic)
    rows: list[tsvutil.Row] = []
    for p in files:
        if not p.exists():
            print(f"skip\t{p}")
            continue
        rows.extend(tsvutil.load(p))
    if not rows:
        print("guide\t没有可用词表", file=sys.stderr)
        return 2
    r = lexbridge.guide(topic, [rows])
    print(f"schema\t{r['schema']}")
    print(f"use\t{','.join(r['use'])}")
    print(f"voice\t{','.join(r['voice'])}")
    print(f"gap\t{','.join(r['gap'])}")
    if ns.out:
        pack = [
            tsvutil.Row(word=w, source=f"guide:{topic}:use", freq=8)
            for w in r["use"]
        ] + [
            tsvutil.Row(word=w, source=f"guide:{topic}:voice", freq=8)
            for w in r["voice"]
        ]
        dest = Path(ns.out)
        tsvutil.dump(dest, pack)
        print(f"pack\t{len(pack)}\t{dest}")
    return 0


def _default_lexicon_files(topic: str) -> list[Path]:
    creation = paths.creation_dict_dir()
    return [
        creation / "创作词库.tsv",
        creation / "语音词库.tsv",
        creation / "会话语料.tsv",
        creation / "旧电脑词库.tsv",
        *lexbridge.industry_files(topic),
        paths.merged_dict_dir() / "整合词库.tsv",
    ]


def cmd_acquire(ns: argparse.Namespace) -> int:
    """画像 + 素材好词 − 已有词表。默认只出计划；--run 才搜，不覆盖行业表，不 apply。"""
    topic = (ns.topic or "").strip()
    if not topic:
        print("acquire 需要 --topic", file=sys.stderr)
        return 2
    files = [Path(p) for p in (ns.src_files or [])]
    if not files:
        files = _default_lexicon_files(topic)
    rows: list[tsvutil.Row] = []
    for p in files:
        if not p.exists():
            print(f"skip\t{p}")
            continue
        rows.extend(tsvutil.load(p))
    r = lexbridge.acquire(topic, [rows])
    print(f"schema\t{r['schema']}")
    print(f"have\t{','.join(r['have'])}")
    print(f"gap\t{','.join(r['gap'])}")
    for q in r["queries"]:
        print(f"seek\t{q}")
    dest = Path(ns.out) if ns.out else paths.industry_dict_dir() / f"_acquire_{topic}.txt"
    extra: list[str] = []
    if ns.run:
        try:
            got, reports = lexsearch.run_search(topic, engine=ns.engine or "auto")
        except (FileNotFoundError, OSError) as e:
            print(f"skip\tsearch {e}")
            got, reports = [], []
        for rec in reports:
            print(f"{rec.get('backend')}\t{rec.get('status')}\t{rec.get('terms')}\t{rec.get('query')}")
        extra = [w for w in clean_industry(got) if w not in set(r["gap"])]
        if extra:
            print(f"found\t{','.join(extra[:40])}")
        elif ns.run:
            print("skip\tsearch 0 词")
    if ns.out or ns.run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        words = list(r["gap"]) + extra
        dest.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        print(f"acquire\t{len(words)}\t{dest}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="豆包 / 微信输入法 个人词库文件层导入导出")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="建出词库布局（工作区下 词库/<类型>/），新环境跑一次")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("detect")
    p.set_defaults(func=cmd_detect)
    p = sub.add_parser("export")
    p.add_argument("--engine", choices=["doubao", "wetype", "all"], default="all")
    p.add_argument("--out", default="")
    p.add_argument("--src", default="")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("import")
    p.add_argument("--engine", choices=["doubao", "wetype"], required=True)
    p.add_argument("--from", dest="src_file", required=True)
    p.add_argument("--staging", default="")
    p.add_argument("--live", default="")
    p.add_argument("--budget", type=int, default=0, help="豆包槽位上限；先锁个人库再按频次补")
    p.add_argument("--no-prefer-personal", action="store_true")
    p.add_argument("--replace", action="store_true", help="整表替换：优化表作为目标库，不并入 live，消除 live 已改写的旧词")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("apply")
    p.add_argument("--engine", choices=["doubao", "wetype"], required=True)
    p.add_argument("--staging", required=True)
    p.add_argument("--live", default="")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("dedup", help="豆包重复槽一键去重（原位裁剪，默认只到 staging）")
    p.add_argument("--engine", choices=["doubao"], default="doubao")
    p.add_argument("--apply", action="store_true", help="输入法已退出时直接写入 live")
    p.add_argument("--staging", default="")
    p.add_argument("--live", default="")
    p.set_defaults(func=cmd_dedup)
    p = sub.add_parser("backup")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_backup)
    p = sub.add_parser("search")
    p.add_argument("--topic", required=True)
    p.add_argument("--from", dest="src_files", action="append", default=[])
    p.add_argument("--run", action="store_true", help="真搜：有 argo 用 argo，否则通用搜索")
    p.add_argument("--engine", choices=["auto", "argo", "generic"], default="auto")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("build")
    p.add_argument("--topic", default="custom")
    p.add_argument("--from", dest="src_files", action="append", default=[])
    p.add_argument("--term", dest="terms", action="append", default=[])
    p.add_argument("--seed", dest="seeds", action="append", default=[], help="thuocl:财经 / thuocl:IT")
    p.add_argument("--prose", action="store_true", help="按正文抽短词，不按一行一词")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("optimize")
    p.add_argument("--from", dest="src_files", action="append", required=True)
    p.add_argument("--out", default="")
    p.add_argument("--drop", action="append", default=[])
    p.add_argument("--no-auto", action="store_true")
    p.add_argument("--drop-fragments", action="store_true")
    p.add_argument("--profile", choices=["personal", "industry"], default="personal")
    p.add_argument("--budget", type=int, default=0, help="按频次留前 N 条；重复键留个人库")
    p.add_argument("--no-prefer-personal", action="store_true")
    p.set_defaults(func=cmd_optimize)
    p = sub.add_parser("harvest")
    p.add_argument("--from", dest="src_files", action="append", default=[])
    p.add_argument("--out", default="")
    p.add_argument("--speech", action="store_true", help="闪电说转写/纠错 → 语音词库")
    p.add_argument("--sessions", action="store_true", help="session-digger 会话语料 → 会话语料.tsv")
    p.add_argument("--legacy", action="store_true", help="旧电脑 Win 搜狗/讯飞/工作词 → 旧电脑词库")
    p.set_defaults(func=cmd_harvest)
    p = sub.add_parser("guide")
    p.add_argument("--topic", required=True)
    p.add_argument("--from", dest="src_files", action="append", default=[])
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_guide)
    p = sub.add_parser("acquire")
    p.add_argument("--topic", required=True)
    p.add_argument("--from", dest="src_files", action="append", default=[])
    p.add_argument("--out", default="")
    p.add_argument("--run", action="store_true")
    p.add_argument("--engine", choices=["auto", "argo", "generic"], default="auto")
    p.set_defaults(func=cmd_acquire)
    ns = ap.parse_args()
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
