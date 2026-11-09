# 功能表

面向使用者：这个工具能做什么、怎么用、怎么验证。每一条都是可执行的行为，
不是内部实现。

## 一、读入格式 → 写出格式

| 读入 | EPUB | MOBI | AZW3 | 备注 |
| --- | --- | --- | --- | --- |
| TXT | `build` | — | — | 只走 `build`，不进格式互转管线 |
| EPUB | ✅ | ✅ | ✅ | 目录、封面、图片、样式表、元数据一并带过去 |
| DOCX | ✅ | ✅ | ✅ | 中英文标题样式都认得；段落级大纲级别、内容控件；`.doc` 需先另存 |
| ODT | ✅ | ✅ | ✅ | 标题层级、列表、图片、链接、分节 |
| HTML | ✅ | ✅ | ✅ | 按标题标签切章；行内格式与图片保留；`class="chapter"` 容器算一章 |
| PDF / 旧 `.doc` | 需 Calibre | 需 Calibre | 需 Calibre | 没装 Calibre 时明确说明缺什么 |

目标格式由 `--output` 扩展名决定；`--format epub|azw3|mobi` 只在未指定 `--output` 时生效。

转换保真度：正文、标题层级、目录树、图片（字节一致）、样式表、封面、
出版信息（出版社/日期/版权等）、书籍语言。转换不改动原文件。

## 二、命令

| 命令 | 做什么 | 关键选项 |
| --- | --- | --- |
| `inspect` | 查空章、跳号、乱码线索 | `--show-headings` `--pattern` `--encoding` |
| `compare` | 比多份版本的章节结构与相同段落 | `--encoding` `--pattern` |
| `ocr-text` | 清洗逐页 OCR 文本，供 `build` 分章 | `--output` `--no-join` `--pattern` |
| `build` | TXT → EPUB，保留前置文字/留言/后记 | `--title` `--author` `--cover` `--headings-json` `--full-toc` |
| `convert` | 格式互转 | `--output` `--format` `--title` `--author` |
| `correct` | 应用已核实的勘误 | `--edits` |
| `cover` | 换封面（图片或搬另一本 EPUB 的） | `--image` `--from-epub` |
| `paginate` | 估算页码写进 EPUB | `--chars` `--exclude` |
| `kindle` | 备 Kindle 文件 | `--method email|usb` `--pages` |
| `layout` | 套中文小说排版预设 | `--preset cn-novel` `--document` `--margin` `--line-height` `--indent` `--paragraph-gap` |
| `validate` | 查书内文件与链接 | `--epubcheck` |
| `proofread` / `proofread-review` / `proofread-apply` | 错字筛查 → 分批复核 → 按锚点写入 | `--job` `--packet` `--decisions` `--report` |

章号同时认阿拉伯数字与中文数字（`第5章`、`第十五章`、`第二百零八章`）。

`cover`、`paginate`、`layout` 需要 EPUB 3；遇到 EPUB 2 的书会先自动转成 EPUB 3 再操作，
不需要装 Calibre。

## 三、贯穿全流程的契约

| 契约 | 含义 |
| --- | --- |
| 保留原文件 | 任何命令都不改输入文件 |
| 不覆盖 | 目标已存在时报错退出，不静默覆盖 |
| 原子写出 | 中断不留半个文件在目标路径 |
| 默认输出路径 | 输入旁的 `整理后/书名.<扩展名>`，文件名用标准书名 |
| 默认无剧透 | `inspect`/`compare` 默认不展示章名与正文片段 |
| 可读报错 | 错误打成 `Error: ...` 一行退出码 1，不吐堆栈 |
| 原生优先 | 装了 Calibre 也先走原生；原生失败才回退 |
| 跨平台 | macOS / Linux / Windows 都能跑：不依赖硬链接、权限位与 sh 脚本 |
| 中文输出 | 终端是 GBK 或 UTF-8 都能正确显示中文，不抛 UnicodeEncodeError |
| 安全文件名 | 书名里的非法字符、Windows 保留名（CON、PRN…）、超长名都会被处理 |

## 四、格式正确性判据

| 输出 | 怎么算对 |
| --- | --- |
| EPUB | 包内 `mimetype` 正确；清单与书脊一一对应；跳转链接不悬空；再读回来正文不丢 |
| MOBI | 记录 0 的 MOBI 头字段自洽；文本记录能按读端规则解压还原；目录索引条目数与 IDXT 对得上；目录标题走 CNCX 池；guide 带 href |
| AZW3 | 纯 KF8 standalone：无 BOUNDARY；chunk/skel/NCX 三张索引能被读端解析；SKEL 声明的 chunk 数等于 CHUNK 表条目数；按读端逻辑重建的文本流与源逐字节相同；索引按 64 KiB 分片（大书不溢出） |
| 安全 | 实体炸弹（含 UTF-16 编码）拒绝；ZIP 越界路径拒绝；解压后大小有上限 |
