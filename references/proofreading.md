# 校对技术进一步更新与自动化 SOP

流程采用脚本全量检测，再由 AI 复核重点位置并修改明确错误。适合已有可读文字的 EPUB，原书另存保留。

## 扫描

在项目目录运行：

```sh
python3 scripts/ebook.py proofread 原书.epub --job work/校对.json
```

内置词表只是一份用于试跑的小词表。两本书试验使用固定版本 Jieba 词典，安装后通过参数启用：

```sh
python3 -m pip install -r requirements-proofread.in
python3 scripts/ebook.py proofread 原书.epub --lexicon jieba \
  --job work/校对.json --terms work/本书词语候选.json
```

程序检查 Jieba 版本和词典指纹，来源与许可证见 assets/proofread-resources.json。候选词按“至少出现 5 次且跨 3 个阅读文件”与前 100 个高频陌生词的并集生成，附带最多 3 处上下文和相近写法。助手确认后写入本书词典的 protected，并在 protected_evidence 中保留依据。重复出现本身不构成正确性证明。

正式校对可用 `--dictionary 通用词典.json` 和 `--book-dictionary 本书词典.json` 扩充。示例：

```json
{
  "words": ["干活", "千万人", "经验"],
  "protected": ["确认过的专名"],
  "confusions": {"千": "干", "干": "千"}
}
```

启用 Jieba 时，普通词命中降低候选优先级；已确认专名或正常词形仅豁免其内部的易混字候选，异常字符、多字符 OCR 和标点疑点继续检查。起步词表模式沿用旧规则。对白、方言和专名需要结合原书确认后加入。扫描覆盖阅读顺序内的段落、标题与列表脚注，嵌套内容只读取一次。发现未覆盖的正文文字时停止扫描，先修复提取范围。没有标记的段落仍可能有语义错误。

可选安装 KenLM 后，通过 `--language-model 本地字符模型.klm` 比较候选在上下文中的分数。此时普通词即使命中字典，替换后分数提升足够大也会进入候选。模型必须按空格分隔的字符训练，模型和词典需要自行准备并确认来源。内置规则不包含统计模型，也不能保证识别全部错字。

词典可增加 replacements 字段，按短语提供多字符 OCR 候选，例如 {"i兑": "说"}。本书词典可记录 source_sha256，防止未经核对直接套到其他底本。

重复运行同一任务会保留已有结果。原书或词典变化时，请使用新的任务路径。同一任务请串行执行。

## 按预算复核

```sh
python3 scripts/ebook.py proofread-review --job work/校对.json \
  --packet work/待复核.json --budget 20000
```

预算单位为 UTF-8 字节，限制一批待复核正文和指令的大小。它不是服务商实际 token 用量，助手额外提示词、回复和历史消息会另占上下文。任务文件保存在本地，包含原文片段，默认控制台只显示数量。

每批最多 30 个疑点组，每组最多 3 段不同的代表上下文。items 保存供判断的代表位置，groups 保存全部位置。相同词形在不同语境下不能直接等同，确有差异时继续展开。只导入代表位置的决定时，其余位置仍保留待复核状态。

让助手读取复核包，返回以下结构：

```json
[
  {
    "id": "复核包里的原始 id",
    "status": "suggested",
    "replacement": "干活",
    "reason": "上下文支持动作短语，建议核对原版"
  }
]
```

正常用法返回 `keep`。未能核实的项目可通过文件导入为 `unresolved`。导入结果：

```sh
python3 scripts/ebook.py proofread-review --job work/校对.json --decisions work/结果.json
```

再次导出会跳过已经处理的候选。每处候选都有独立位置，同一词在不同语境中会分别复核。

也可用 `--reviewer '["python3","自己的模型适配器.py"]'` 自动调用本地适配器。适配器从标准输入接收 JSON 复核包，向标准输出写 JSON 决策列表，负责模型选择、认证、实际 token 计量及 API 费用上限。自动复核可返回 keep、suggested 或 unresolved，默认超时 60 秒。用户授权修正明确错误时启用 --allow-ai-approval，适配器即可返回 approved，并必须说明依据。失败、非法响应时保留待处理状态，可重试。每次成功调用记录发送内容字节数和返回决策数。

## 应用确认过的改动

用户授权修正明显错误后，由 AI 逐处复核。明确的形近字、固定搭配或杂串可标为 approved，记录上下文判断依据。缺字、版本差异、多解问题标为 unresolved，留给用户查看具体原句。不要把 AI 判断写成已核对权威底本。

```sh
python3 scripts/ebook.py proofread-apply --job work/校对.json --output 整理后/书名.epub
```

程序检查原文件指纹、段落位置、原文和改动是否重叠，再生成副本。跨内嵌标签的改动需要单独处理。建议和待复核项不会写入。

本轮已在两本用户提供的书上完成试验，报告和私有词典保存在本地 work/two-book-trial。独立样本与放行抽查各为每书 100 个短段落，由当前助手复核。抽查与规则调整记录在任务材料里。自动扩大上下文、通用抽样命令和服务商级累计 token 预算仍需后续完善。

## 自动化执行与交付

使用当前助手时，由助手反复导出复核包、读取上下文、导入决策，直至没有 pending。相同词形但不同语境需要分别判断；明显错误直接进入批准清单。其他问题具体说明为什么不能确定。

有独立模型适配器时可连续处理重点位置：

```sh
python3 scripts/ebook.py proofread-review --job work/校对.json \
  --reviewer '["python3","模型适配器.py"]' \
  --allow-ai-approval --until-done --max-batches 100 \
  --report work/具体勘误.md
python3 scripts/ebook.py proofread-apply --job work/校对.json --output 整理后/书名.epub
python3 scripts/ebook.py validate 整理后/书名.epub
```

每批保存进度，无进展或适配器失败时停止并保留任务。达到批次上限后可重跑续接。重叠批准项需要助手比较并保留一个修改，跨标签的改动转为待确认。复核包的字节预算按批限制，累计 API 费用由适配器控制。

交付前核对所有正文差异均来自批准清单，检查目录、脚注和页码跳转。用户优先拿到标准书名的修正版。具体勘误报告展示原句、修改前后、定位与理由；正常内容不列出。已批准与已写入分开描述，避免把待执行的决定报告为已经修复。
