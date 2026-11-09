# 命令参考（自行操作时使用）

基础依赖为 Python 3.9+。Jieba 大词库由 Agent 自动尝试安装，失败时降级继续。脚本路径相对 skill 根目录。格式互转原生支持 EPUB、DOCX、ODT、HTML 与 MOBI/AZW3，无需 Calibre；PDF 等其余格式才需要它。省略 `--output` 时默认保存到输入旁的 `整理后/书名.epub`（USB 为 `.azw3`）。已有同名文件时请另选输出路径。不自动访问网络、不查找或下载书籍、不发送邮件。

## 无剧透检查

```bash
python3 scripts/ebook.py inspect '/path/书名.txt'
python3 scripts/ebook.py inspect '/path/书名.epub'
python3 scripts/ebook.py inspect '/path/书名.txt' --encoding gb18030
```

结果会列出短章、跳号和乱码线索，结合内容判断即可。需要查看原章名时加 `--show-headings`，注意可能剧透。

特殊章节标题示例（数字分组名 `number`；标题分组 `title` 可选）：

```bash
python3 scripts/ebook.py inspect '/path/书名.txt' --pattern '^Chapter (?P<number>\d+): (?P<title>.+)$'
```

比较两份或更多版本：

```bash
python3 scripts/ebook.py compare '版本一.epub' '版本二.txt' '版本三.epub'
```

以第一份为基准，统计忽略空白后完全相同的段落；改写和重新分段会影响计数，不能用它直接判断缺文。`inspect` 还会报告疑似乱码的段落数和所在阅读文件路径，候选需要核实。

## 扫描件 OCR 文本整理

先在外面用 OCR 工具把逐页文字导成 TXT（本仓库不捆绑 OCR 引擎、不联网），
再用 `ocr-text` 清洗成一行一段的普通文本：

```bash
python3 scripts/ebook.py ocr-text '/path/页/*.txt' --output '/path/正文.txt'
python3 scripts/ebook.py build '/path/正文.txt' --title '书名' --author '作者'
python3 scripts/ebook.py proofread '/path/书名.epub' --job '/path/校对.json'
```

它会合并被扫描件硬换行切断的句子、剔除反复出现的页眉与页码、归一全角空格，
并保留章标题让 `build` 能正确分章。多个输入按自然页号排序
（`page2.txt` 排在 `page10.txt` 前），页与页之间绝不粘死。

- 诗歌、剧本这类换行有意义的文本加 `--no-join`，只归一空白不合并行。
- 章号格式不标准时用 `--pattern` 传自己的正则；章标题永远不参与合并。
- 不想删页眉页码时加 `--keep-running-heads`；`--min-repeat` 调整判定门槛。
- 省略 `--output` 时默认写到第一个输入旁的 `整理后/书名.txt`。

清洗只处理文字，不做版式重建：双栏、表格、公式的阅读顺序由 OCR 引擎决定。
错字交给 `proofread` 筛查后人工复核，不自动改。扫描件本身仍需先跑 OCR。

## TXT → EPUB

```bash
python3 scripts/ebook.py build '/path/书名.txt' \
  --title '书名' --author '作者' --cover '/path/封面.png' \
  --output '/path/work/书名.epub'
```

每个非空行转换为一段。如果原文件按固定宽度换行，先整理成一行一段。

前置文字、作者留言和后记按原位置保留。目录默认显示“第N节”，使用原章名时加 `--full-toc`。

经可靠来源核实后，创建标题修正 JSON，例如：

```json
{"2": "第2章 核实后的标题"}
```

用 `--headings-json /path/verified-headings.json` 应用标题修正。键是从 1 开始的章节位置。

## 转换其他格式

```bash
python3 scripts/ebook.py convert '原书.docx' --title '书名' --author '作者'
python3 scripts/ebook.py convert '原书.html' --output '整理后/书名.epub'
python3 scripts/ebook.py convert '原书.epub' --output '整理后/书名.azw3'
```

原生支持读入 EPUB、DOCX、ODT、HTML，写出 EPUB、MOBI、AZW3，不需要装 Calibre。
目标格式按 `--output` 的扩展名判断；`--format epub|azw3|mobi` 只在没有指定
`--output` 时生效。

`.mobi` 写纯 MOBI 6，`.azw3` 写纯 KF8 standalone，与 Calibre 的
`MOBIOutput` / `AZW3Output` 行为一致。AZW3 不带 MOBI 6 兼容段，
只支持 KF8 的设备能读，很老的只认 MOBI 6 的设备请改用 `.mobi`。

- **Word**：认得中英文标题样式（`标题 1`、`heading 1`），据此生成可跳转目录。
  列表转成 `ul`/`ol`；修订里删掉的文字不算正文。旧版 `.doc` 先用 Word 或
  LibreOffice 另存为 `.docx`。
- **ODT**：读取 LibreOffice 文档，标题层级、列表与图片一并带过去。
- **HTML**：按标题标签切分章节，标题里的编号会自动去掉；
  `class="chapter"` 的容器也当作一章。
- **EPUB**：可以重新整理并换成别的书名作者，目录与封面保留。
  目录支持 EPUB 3 的 nav 与老书的 NCX 两种。

其余格式（PDF、旧版 Word 等）需要 Calibre。装了 Calibre 时这些格式会自动交给它处理，
没装会明确说明。原生转换不改动原文件，也不覆盖已有输出。

PDF 适合尝试转换文字可选中的单栏文件。分页、段落、目录和表格可能需要重整；
纯扫描 PDF 先做 OCR，脚本不会自动识别图片中的文字。

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

或传 `--image '/path/封面.jpg'` 使用 PNG/JPEG 原图。

替换后会同步更新书内封面和封面入口，正文保持不变。如果书中写有旧封面的来源说明，也需要一并更新。EPUB 2 的书会先自动转成 EPUB 3 再操作，不需要另外装 Calibre。

## Kindle 传书准备

```bash
python3 scripts/ebook.py kindle '原书.epub' --method email --pages
python3 scripts/ebook.py kindle '原书.epub' --method usb
```

邮件版不需要估算页码时省略 `--pages`；已有页码目录会保留。USB 版用原生 AZW3 写出，再交给 Calibre 的“发送到设备”，或直接用 Send to Kindle。页码在 Calibre 的设备设置里开启。脚本只准备文件。

## 估算页码

```bash
python3 scripts/ebook.py paginate '/path/无页码.epub' \
  --chars 500 --exclude 'OEBPS/notes.xhtml' \
  --output '/path/work/书名.epub'
```

`--chars` 为每页目标非空白字符数；分页落在段落边界，实际页长会波动。封面、语义标注的扉页、主导航不参与；其他阅读文档默认参与。多次 `--exclude` 可排除附录或报告，使用 EPUB 包内的精确路径。需要把正文、番外和附录全部纳入时，不排除相应文档。

已有页码的书不会被重复分页。只想换封面时直接更换，无需重做页码。EPUB 2 的书会先自动转成 EPUB 3 再分页，不需要另外装 Calibre。

## 额外检查（可选）

```bash
python3 scripts/ebook.py validate '/path/书名.epub'
python3 scripts/ebook.py validate '/path/书名.epub' --epubcheck '/path/epubcheck.jar'
python3 -m unittest discover -s tests -t . -v
```

`validate` 检查书内文件和跳转链接。需要更详细的格式检查时，可安装 Java 和 EPUBCheck，再使用 `--epubcheck`。

测试用 `unittest` 跑：一部分起真实 CLI 断言生成的 EPUB / MOBI / AZW3，另一部分直接核验读写端函数。样本全部在运行时现场生成，不需要额外准备语料。

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

`--document` 使用 EPUB 内部的正文路径，可重复指定。上例使用本工具生成的常见路径，其他书的路径可能不同。可在 `inspect` 的 `reading_documents` 中查看路径，再按书内阅读顺序确认正文。试排时只传一个 `--document`，输出仍是整本书，可直接在阅读器里打开那一章检查。满意后选择全部正文文件并另存。预设调整段落、现有一级标题和带语义标记的注释。文字校订、空白块清理、添加缺失章号和复制样板书样式按 [精修流程](reading-workflow.md) 单独处理。EPUB 2 的书会先自动转成 EPUB 3 再排版。

## 整本书字典校对

可用 `proofread` 扫描疑点、`proofread-review` 分批复核、`proofread-apply` 应用已确认勘误。详见 [校对流程](proofreading.md)。

## 代码结构

命令都从 `scripts/ebook.py` 进入，内部按功能拆分：

```
scripts/
  ebook.py              命令行入口，只做参数解析和调度
  workshop/
    epub.py             EPUB 容器读写、校验、封面资源定位
    text.py             文本编码识别、章节切分、杂音规则
    render.py           书内页面生成与封面替换
    inspect_cmd.py      检查与比较
    build_cmd.py        TXT→EPUB 与格式互转
    convert/            原生格式互转管线
      book.py           中间表示
      epub_input.py     EPUB 读入
      epub_output.py    EPUB 写出
      docx_input.py     DOCX 读入
      odt_input.py      ODT 读入
      html_input.py     HTML 读入
      palmdoc.py        PalmDOC 压缩
      mobi.py           MOBI 头与 EXTH
      mobi_header.py    MOBI 头与索引记录
      kf8.py            KF8 分块与骨架
      mobi_writer.py    MOBI/AZW3 写出
      serializer.py     文本流序列化
    correct_cmd.py      应用已核实的勘误
    cover_cmd.py        更换封面
    layout_cmd.py       排版预设
    pages_cmd.py        估算页码与 Kindle 准备
    proofread_cmd.py    校对扫描、复核包、写入
    validate_cmd.py     结构校验
```

新增命令时在 `workshop/` 下建一个 `*_cmd.py`，实现 `register(sub)` 注册子命令即可，无需改动入口文件。
