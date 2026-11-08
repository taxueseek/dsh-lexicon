# dsh-lexicon

面向 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）的词库管家：自带 `ime_lexicon.py` 引擎，并把它桥接成模型工具，管理**豆包 / 微信输入法**个人词库——检测、导出、备份、行业术语搜索、建库、修正、staging 导入。

Python 引擎随包分发（`vendor/ime-lexicon/`），仍然是文件层操作的唯一真源；本插件只做参数校验与超时控制的调用。**导入永远不会写入正在使用的输入法**——`apply` 刻意不作为工具暴露，始终是用户手动确认的步骤。

## 安装

```sh
dsh plugin add @taxueseek/dsh-lexicon
# 或直接从 GitHub 安装
dsh plugin add taxueseek/dsh-lexicon
```

要求 PATH 里有 `python3`（引擎是纯标准库 Python，无需 pip 装依赖）。

## 工具

| 工具 | 用途 |
|---|---|
| `ime_detect` | 报告豆包 / 微信输入法词库状态与已用槽位。 |
| `ime_export` | 导出词库为 TSV（只读快照）。 |
| `ime_backup` | 带时间戳的词库备份。 |
| `ime_search` | 行业术语搜索——默认只出计划；`run: true` 才真正执行搜索（argo 优先，通用搜索兜底）。 |
| `ime_build` | 从原始术语清单生成行业词库 TSV。 |
| `ime_optimize` | 跨来源合并、去重、纠错词表。 |
| `ime_import` | 把优化后的 TSV 写入 staging 区（加 `replace` 整表替换，剔除已被改写/删除的旧词）——绝不触碰正在使用的词库。 |

## 纪律规则

- 先拷后读后写，staging 先行，apply 前输入法必须退出。
- 豆包个人库约 7500 槽（豆包 0.9.7 起扩容；一词一槽），导入必须带预算（`--budget`）。
- 核心词必须有拼音；多音进扩展；无拼音进大词库且禁止 apply。
- 消错替换：`ime_import` 加 `replace` 用优化表整表替换（不并入 live），剔除 optimize 已改写/删除的错误词；无拼音词仍禁止 apply。
- `ime_search --run` 走网络（argo → 通用兜底），可能需要几分钟。

## 配置

| 键 | 默认值 | 含义 |
|---|---|---|
| `scriptPath` | 随包的 `vendor/ime-lexicon/ime_lexicon.py` | 引擎脚本路径（可覆盖为自备副本） |
| `scriptTimeoutMs` | 120000 | 本地命令超时 |
| `searchTimeoutMs` | 300000 | `search --run`（网络）超时 |

## 安全说明

- 除 `ime_search --run` 的网络调用外，所有操作都在本机；导出和备份留在本地。
- 插件绝不 apply 到正在使用的输入法——这始终是手动步骤。

## License

MIT
