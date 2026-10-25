# 命令参考（自行操作时使用）

依赖：Python 3.9+ 标准库。脚本路径相对 skill 根目录。全部写操作要求新的输出路径；源文件不覆盖。不自动访问网络、不查找或下载书籍、不发送邮件。

## 无剧透检查

```bash
python3 scripts/ebook.py inspect '/path/书名.txt'
python3 scripts/ebook.py inspect '/path/书名.epub'
python3 scripts/ebook.py inspect '/path/书名.txt' --encoding gb18030
```

`short_chapter_candidates` 是1开始的章节位置，仅表示正文段落字符少于800，可能是合法短章、请假通知、被合并章节或缺失内容，必须人工判断。`declared_numbers` 保留原乱号，不自动修复。`--show-headings` 会输出原章名，只有用户接受可能的剧透时才用。

特殊章节标题示例（数字分组名 `number`；标题分组 `title` 可选）：

```bash
python3 scripts/ebook.py inspect '/path/书名.txt' --pattern '^Chapter (?P<number>\d+): (?P<title>.+)$'
```

## TXT → EPUB

```bash
python3 scripts/ebook.py build '/path/书名.txt' \
  --title '书名' --author '作者' --cover '/path/封面.png' \
  --output '/path/work/书名.epub'
```

每个非空源行作为一个段落，去掉首尾排版空白；不自动连接换行。如果 TXT 是固定宽度硬换行而非一行一段，先在工作副本完成可审计的段落重建，再转换。本工具有意不推测段落合并。

未识别为章节的前置文字写入“原文件前置内容”。作者留言、后记等也按所在位置保留；不内置任何特定小说的正文/番外边界。目录默认用“第N节”表示物理顺序，避免冒充已经校准的原章号；需要原题时用 `--full-toc`。

经可靠来源核实后，创建标题修正 JSON，例如：

```json
{"2": "第2章 核实后的标题"}
```

再传 `--headings-json /path/verified-headings.json`。这个文件只表示明确授权的标题改动，不会补写、删减或改动正文。保留修正来源和原题对照，报告不展示剧透标题。

## 迁移封面原图

```bash
python3 scripts/ebook.py cover '/path/原整理本.epub' \
  --from-epub '/path/用户指定封面的书.epub' \
  --output '/path/work/书名.epub'
```

或传 `--image '/path/封面.jpg'`。仅接受原图 PNG/JPEG，保持原始字节；不会裁切、生成或重新编码。

替换后会同步更新书内封面和封面入口，正文保持不变。如果书中写有旧封面的来源说明，也需要一并更新。此操作目前支持 EPUB 3；较早的 EPUB 2 可先用 Calibre 转换。

## 估算页码

```bash
python3 scripts/ebook.py paginate '/path/无页码.epub' \
  --chars 500 --exclude 'OEBPS/notes.xhtml' \
  --output '/path/work/书名.epub'
```

`--chars` 为每页目标非空白字符数；分页落在段落边界，实际页长会波动。封面、语义标注的扉页、主导航不参与；其他阅读文档默认参与。多次 `--exclude` 可排除附录或报告，使用 EPUB 包内的精确路径。需要把正文、番外和附录全部纳入时，不排除相应文档。

已有页码的书不会被重复分页。只想换封面时直接更换，无需重做页码。

## 额外检查（可选）

```bash
python3 scripts/ebook.py validate '/path/书名.epub'
python3 scripts/ebook.py validate '/path/书名.epub' --epubcheck '/path/epubcheck.jar'
python3 -m unittest discover -s tests -v
```

`validate` 检查书内文件和跳转链接。需要更详细的格式检查时，可安装 Java 和 EPUBCheck，再使用 `--epubcheck`。这些检查不能代替正文内容核对。

已有合法 EPUB 转其他格式时，可使用本机 Calibre 的 `ebook-convert`，但输出格式、设备兼容性与页码方案应分别确认。本仓库不捆绑转换器、EPUBCheck、插件或其依赖。
