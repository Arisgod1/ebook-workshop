# 命令参考（自行操作时使用）

依赖：Python 3.9+ 标准库。脚本路径相对 skill 根目录。转换其他格式或生成 AZW3 需安装 Calibre。省略 `--output` 时默认保存到输入旁的 `整理后/书名.epub`（USB 为 `.azw3`）。已有同名文件时请另选输出路径。不自动访问网络、不查找或下载书籍、不发送邮件。

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

比较两份或更多版本：

```bash
python3 scripts/ebook.py compare '版本一.epub' '版本二.txt' '版本三.epub'
```

以第一份为基准，统计忽略空白后完全相同的段落；改写和重新分段会影响计数，不能用它直接判断缺文。`inspect` 还会报告疑似乱码的段落数和所在阅读文件路径，候选需要核实。

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

## 转换其他格式

```bash
python3 scripts/ebook.py convert '原书.docx' --title '书名' --author '作者'
python3 scripts/ebook.py convert '原书.pdf' --title '书名' --author '作者'
```

支持格式取决于本机 [Calibre](https://manual.calibre-ebook.com/generated/en/ebook-convert.html)。可省略书名和作者，沿用转换器读取到的信息。完成后检查书名、作者、目录跳转与内容。

- **Word**：用“标题 1”“标题 2”等样式标记章节，Calibre 可据此生成目录。旧版 `.doc` 先用 Word 或 LibreOffice 另存为 `.docx`。
- **PDF**：适合尝试转换文字可选中的单栏文件。分页、段落、目录和表格可能需要重整；纯扫描 PDF 先做 OCR，脚本不会自动识别图片中的文字。

## 应用已核实的勘误

将以下结构保存为 UTF-8 的 `勘误.json`，每条写明正文文件和确认过的替换文字：

```json
[{"document": "OEBPS/c0001.xhtml", "old": "比i兑法", "new": "比说法"}]
```

```bash
python3 scripts/ebook.py correct '原书.epub' --edits '勘误.json'
```

每条原文必须在指定文件的段落中单独匹配一次，且不能跨内嵌标签。遇到多处相同文字可加长原文范围；无法唯一定位时整批不写入。

## 迁移封面原图

```bash
python3 scripts/ebook.py cover '/path/原整理本.epub' \
  --from-epub '/path/用户指定封面的书.epub' \
  --output '/path/work/书名.epub'
```

或传 `--image '/path/封面.jpg'`。仅接受原图 PNG/JPEG，保持原始字节；不会裁切、生成或重新编码。

替换后会同步更新书内封面和封面入口，正文保持不变。如果书中写有旧封面的来源说明，也需要一并更新。此操作目前支持 EPUB 3；较早的 EPUB 2 可先用 Calibre 转换。

## Kindle 传书准备

```bash
python3 scripts/ebook.py kindle '原书.epub' --method email --pages
python3 scripts/ebook.py kindle '原书.epub' --method usb
```

邮件版不需要估算页码时省略 `--pages`；已有页码目录会保留。USB 版交给 Calibre 的“发送到设备”，在设备设置中按需开启页码。脚本只准备文件。

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

本仓库不捆绑 Calibre 或 EPUBCheck。

## 套用中文小说排版

```sh
python3 scripts/ebook.py layout /path/to/book.epub \
  --preset cn-novel \
  --document OEBPS/c0001.xhtml \
  --document OEBPS/c0002.xhtml \
  --output /path/to/book-layout.epub
```

可加 `--margin 6 --line-height 1.7 --indent 2 --paragraph-gap 0.4`，分别表示左右各 6%、1.7 倍行距、两字缩进与上下各 0.4em 的段间距。

`--document` 使用 EPUB 内部的正文路径，可重复指定。上例使用本工具生成的常见路径，其他书的路径可能不同。可在 `inspect` 的 `reading_documents` 中查看路径，再按书内阅读顺序确认正文。试排时只传一个 `--document`，输出仍是整本书，可直接在阅读器里打开那一章检查。满意后，可以把试排版作为输入，选择更多章节并另存。预设调整段落、现有一级标题和带语义标记的注释。文字校订、空白块清理、添加缺失章号和复制样板书样式按 [精修流程](reading-workflow.md) 单独处理。
