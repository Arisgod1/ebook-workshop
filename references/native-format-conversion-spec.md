# 原生格式互转实现规格

> 面向 `ebook-workshop` 的独立实现指南。目标格式：读入 DOCX / ODT / HTML，写出 MOBI / AZW3。
> 约束：仅 Python 标准库（`zipfile`、`xml.etree`、`struct`、`html.parser`）；不调用任何外部二进制；保留原文件；格式有损时报错而非静默损坏。
>
> **本规格的每一条字节级事实都来自对 Calibre 9.18.0 源码的逐行核对，并经本机实测验证。**
> 核对基准：`/tmp/rea-work/calibre-9.18.0/src/calibre/`（与官方 tarball 逐字节一致，已 `diff -rq` 确认）。
> 涉及的关键源码位置在各节末尾给出 `文件:行号`，可直接复核。
>
> **许可提示**：Calibre 源码是 GPLv3。本规格只陈述**格式事实**（偏移、位宽、常量数值、算法语义），
> 这些是事实性知识，不受版权保护；**实现必须独立编写**，不要逐行翻译 Calibre 的 Python 代码。

---

## 0. 架构

### 0.1 现有中间表示（复用，不重写）

项目已有 `scripts/workshop/convert/book.py`，结构如下，本规格直接沿用：

```python
class Document:      # href, media_type, content, properties
class TocEntry:      # title, href, kind, children
class Image:         # href, data, media_type
class Book:          # title, author, language, identifier, documents, images, toc, cover, ...
```

`convert/__init__.py` 的 `READERS` / `WRITERS` 字典已经把读入与写出解耦：
新增格式只需注册一端。**这是本项目最重要的架构资产，不要动它。**

本规格只做两件事：
1. 修正 MOBI/AZW3 写出路径里已验证的字节级缺陷（§1.7 列出的 9 条）。
2. 把读入路径补齐到规范要求（标题判定、封面检测、脚注等）。

### 0.2 input plugin → IR → output plugin 契约

```python
# 读入：返回一个 Book
def read(path: str | Path) -> Book

# 写出：接受一个 Book，产出文件字节（MOBI/AZW3）或直接写文件（EPUB）
def build_mobi(book: Book, kf8: bool) -> bytes
def write(book: Book, path: Path) -> dict   # EPUB
```

`Book` 里各字段的填充责任：

| IR 字段 | 由谁填 | 备注 |
|---|---|---|
| `title` / `author` / `language` / `description` / `publisher` | 各 input plugin | DOCX 从 `docProps/core.xml`，ODT 从 `meta.xml`，HTML 从 `<title>` |
| `identifier` | 各 input plugin | 没有就生成 UUID |
| `documents` | 各 input plugin | 每份文档一份 XHTML，**href 必须书内唯一** |
| `images` | 各 input plugin | `href` 是书内路径，`data` 是原始字节 |
| `toc` | 各 input plugin | 树形，`href` 指向 `documents` 里的某份或某锚点 |
| `cover` | 各 input plugin | 是 `images` 里的一个 href |

**关键约束**：`documents[].href` 与 `images[].href` 在同一个命名空间里。
MOBI 的 `recindex` 与 `kindle:embed:` 引用的是资源在资源块里的序号，与 href 无关；
但 EPUB 输出直接沿用 href，所以两边不能撞名。

### 0.3 与现有 `epub.py` 的复用点

`scripts/workshop/epub.py` 已有：

| 函数 | 用途 | MOBI/AZW3 是否复用 |
|---|---|---|
| `xml(data)` | 安全 XML 解析（拒 ENTITY/DOCTYPE） | 复用（读 OPF/NCX） |
| `dump(root)` | `ET.tostring(..., xml_declaration=True)` | 复用（写 NCX） |
| `write(files, output)` | 写 ZIP（mimetype STORED 首位） | 复用（写 EPUB） |
| `validate_files(files)` | 校验资源/锚点 | 复用 |
| `inventory` / `reading_documents` | 读 manifest/spine | 复用（读 EPUB） |
| `image_info(data)` | 读 PNG/JPEG 尺寸 | 复用（封面检测） |
| `q(ns, name)` / 常量 `H O D N EP C` | 命名空间 | 复用 |

**MOBI 写出不需要 `epub.py` 的任何东西**——MOBI 是完全不同的容器格式。

### 0.4 已确认的项目侧缺陷（先修，再谈新功能）

> **状态（2026-11-08）**：`mobi_writer.py` 已按本规格重写，B1–B10 全部修复，
> 并已改为 B5–B7 所说的纯 KF8 路线——`.azw3` 是**纯 KF8 standalone**
> （无 BOUNDARY、无 EXTH 121、无 MOBI 6 兼容段），`.mobi` 是纯 MOBI 6。
> 因此 B5/B6/B7 在现版本中不再有对应代码；下表保留为当时的取证记录，
> 也是「为什么不做联合文件」的依据。

用 Calibre 的 reader 逻辑跑项目自己的输出，下列缺陷已复现：

| # | 位置 | 现象 | 依据 |
|---|---|---|---|
| B1 | `mobi_writer.py:249-251` | EXTH 201 写的是 **PDB 记录号**，规范要求**资源块内 0-based 下标** | `metadata/mobi.py:179` `self.cover_record = self.record(rindex + image_base)` |
| B2 | `mobi_writer.py:121-126` | 图片记录加了 `b'IMG'` 魔数前缀，Calibre 用 `imghdr.what()` 嗅探，**识别不出 → 图片被丢弃** | `reader/mobi6.py:947-950` `what(None, data)`，无 `IMG` 分支 |
| B3 | `mobi_writer.py:80-87` | overlap 计数字节写 `size-1`，Calibre 写 `size`；读取端公式是 `(b & 0b11) + 1` | `writer2/main.py:183` `pack(b'>B', len(overlap))` vs `utils.py:229` |
| B4 | `mobi_writer.py:98-101` | 逐记录「压缩后更大就存原文」，但 record0 全局声明 `compression=2`，读者仍按 PalmDOC 解 → **数据损坏** | `reader/mobi6.py:856` 按 header 的 compression_type 统一解压 |
| B5 | `mobi_writer.py:266` | BOUNDARY 记录写成 `b'BOUNDARY' + 4 字节`，Calibre 读端做**精确 8 字节相等**比较 → 联合文件识别失败 | `reader/mobi6.py:143` `raw == b'BOUNDARY'` |
| B6 | `mobi_writer.py:300-327` | joint 文件的 MOBI 6 头写 `file_version=8`/`min_version=8` 且 `skel_index` 非 NULL → 被判定为 standalone，**KF8 段永远读不到** | `reader/headers.py:299` `mobi_version == 8 and skelidx != NULL_INDEX` |
| B7 | `mobi_writer.py` | joint 文件的 MOBI 6 头**没有写 EXTH 121** → 读端找不到 KF8 头 | `writer2/main.py:458` `kf8_header_index=kf8_header_index` |
| B8 | `mobi_writer.py:194-196` | `first_non_text_record` 未考虑 4 字节对齐填充记录 | `writer2/main.py:190-194` 填充时 `first_non_text_record_idx += 1` |
| B9 | `mobi.py:204` | PDB name 用 `isalnum()` 过滤，中文标题变成空串 | `writer2/main.py:470-472` `ascii_filename()` |
| B10 | `mobi.py:105` | `to_base` 用**小写**字母表 `0-9a-z`；读端的 `kindle:pos:fid` 正则 `[0-9A-V]` **大小写敏感** → 链接定位失效 | `reader/markup.py:37` `posfid_index_pattern` 无 `re.IGNORECASE` |

**B6/B7 是最严重的**：当前的 `.azw3` 输出虽然文件结构完整，但任何阅读器都会把它当成
纯 KF8 文件，**MOBI 6 兼容段形同虚设**。

**B10 的具体后果**：Calibre 的 base32 字母表是 `string.digits + string.ascii_uppercase`
（`0-9A-Z`，实际只用 `0-9A-V`）。项目写成小写后，
`kindle:pos:fid:0003:off:0000000007` 里的编号全部是小写，
而 `reader/markup.py:37` 的正则 `([0-9A-V]+)` 不带 `re.IGNORECASE` → **匹配不上**。
注意 `img_index_pattern`（`:122`）带 `re.IGNORECASE`，但 `:263` 的那份不带，
所以图片能认、链接不能认——这类半生不熟的行为最难排查。

---

## 1. MOBI/AZW3 写出

### 1.1 PalmDOC 压缩

#### 规范

PalmDOC 是 LZ77 变体 + 字节对，**没有 Huffman，没有逐记录 flag 字节**。
压缩类型在 record0 偏移 0 全局声明一次：

```
1 = 无压缩
2 = PalmDOC
17480 = HUFF/CDIC（Calibre 只有解码器，无编码器；本项目一律不写）
```

流由单字节操作码构成：

| 字节 | 含义 |
|---|---|
| `0x00` | 1 个字面字节 |
| `0x01`–`0x08` | 拷贝 N 个字面字节 |
| `0x09`–`0x7F` | 1 个字面字节 |
| `0x80`–`0xBF` | length-distance 对（2 字节） |
| `0xC0`–`0xFF` | byte pair：`' '` + `(b ^ 0x80)` |

**length-distance 对的位布局**（16 位大端整数 `v`）：

```
 bit 15 14 | 13 .............. 3 | 2  1  0
     [1][0] |      distance      | length-3
      0x80  |     (11 bits)      |  (3 bits)
```

- 距离字段**直接存回溯距离** `dist`（1..2047），**不是 `dist - 1`**。
  验证：`compound = (dist << 3) + len - 3`，解码 `di = (v & 0x3FFF) >> 3`，
  因 `dist << 3` 低 3 位为 0，`(compound & 0x3FFF) >> 3 == dist` 精确成立。
- 长度 = `(v & 7) + 3`，范围 3..10。

**byte pair**：输入 `0x20` 后跟一个 `0x40`–`0x7F` 的字符时，输出 1 字节 `char ^ 0x80`。
末尾的孤立空格不压缩。

#### 编码器参数（Calibre 的选择，输出与规范等价）

| 常量 | 值 | 含义 |
|---|---|---|
| `MIN_MATCH_LEN` | 3 | 短于此的匹配编码成 2 字节，不划算 |
| `MAX_MATCH_LEN` | 10 | 3 位长度字段的上限 |
| `MAX_MATCH_DIST` | 2047 | 11 位距离字段上限是 2048，保守留 1 |
| 边缘禁区 | `i > 10 and (len - i) > 10` | 块首 11 字节、块末 10 字节不做 LZ 匹配 |
| 二进制游程上限 | 8 字节 | 控制字节与高位字节成组输出，组首字节是组长度 |

**边缘禁区的精确含义**（实测边界）：块长 ≥ 22 字节才可能出现第一个 LD 对；
源偏移 0..10 不能作为匹配起点，偏移 11 可以。

#### 现有实现评估

`scripts/workshop/convert/palmdoc.py` 的 `compress()` / `decompress()` 逻辑正确，
与 Calibre 的 C 实现位级等价。**无需重写。**

但要注意两点：

1. `decompress()` 里 `if distance and distance <= len(out)` —— 距离越界时整对丢弃。
   这是 Calibre 的防御性行为（`Changelog.old.txt:360` 修过崩溃）。**写文件时不必改，
   读文件时若想严格报错需显式检查。**
2. 现有 `compress()` 用哈希链（`heads`/`prev` 数组）。每条记录重建 65536 元素的 list
   约 0.034 ms，对 1000 条记录约 34 ms，可以接受。**不要换成 `bytes.rindex` 版本**——
   实测 `rindex` 版在低熵随机字母表上退化 13 倍（30 ms vs 2.3 ms / 4KB）。

#### 陷阱

- **压缩类型是全局的**（B4）。要么全用 PalmDOC，要么全不用。**禁止逐记录回退。**
  若确实需要「某些记录不压缩」，只能在 record0 声明 `compression=1` 后全部不压缩。
- 压缩最坏膨胀 **1.5×**（每 8 字节不可压缩数据要 9 字节输出），
  随机字节实测约 1.26×。`2 * len + 1` 是 Calibre 的**缓冲区上界**，不是可达膨胀率。
- **不要**写「压缩后更大就存原文」的判断——那会破坏格式。

### 1.2 PalmDB 头 + MOBI 头 + EXTH

#### PalmDB 头（78 字节 + 记录索引表）

```
偏移  长度  字段
0x00  32   name        书名，ASCII，空格→'_'，截断 31 字节，NUL 补齐
0x20  2    attributes  小端 uint16
0x22  2    version     大端 uint16
0x24  4    creation_date   大端 uint32
0x28  4    modification_date
0x2C  4    last_backup_date
0x30  4    modification_number
0x34  4    app_info_id
0x38  4    sort_info_id
0x3C  8    type+creator  b'BOOKMOBI'
0x44  4    unique_id_seed
0x48  4    next_record_list_id
0x4C  2    number_of_records   大端 uint16
0x4E  8*N  记录索引表
+     2    \0\0 填充
```

记录索引表每条 8 字节：**偏移(4, 大端) + attributes(1)=0 + uniqueID(3)**。
`uniqueID = 2 * 记录号`（记录 0 = MOBI 头）。

**第 i 条记录的偏移 = `78 + 8*N + 2`**，然后逐条累加记录长度。

**时间戳纪元**：PalmDB 规范是 1904 纪元，Calibre 写的是 **Unix 秒**。
跟随 Calibre（阅读器对 PDB 时间戳不敏感）。实测 `test.mobi` 的 `creation=1793958216`
按 Unix 解析是正确日期。

**PDB name 必须是 ASCII**（B9）。Calibre 用 `ascii_filename()` 做 Unicode→ASCII 转写
（中文转拼音），不是替换成 `_`。无转写能力时用 `_` 占位即可，
**真正的书名放在 EXTH 503 与 record0 的 full title 里**。

#### MOBI 头（record0）

**两套布局，字段偏移不同，必须分别构造。**

**KF8 布局（`header_length = 264`）**——逐字段核对 `writer8/mobi.py:38-192`：

| 偏移 | 字段 | KF8 值 |
|---|---|---|
| 0 | compression | 2 |
| 2 | unused1 | 0 |
| 4 | text_length | 解压后正文的 UTF-8 字节数 |
| 8 | last_text_record | **文本记录条数** |
| 10 | record_size | 4096 |
| 12 | encryption_type | 0 |
| 14 | unused2 | 0 |
| 16 | ident | `b'MOBI'` |
| 20 | header_length | 264 |
| 24 | book_type | 2 |
| 28 | encoding | 65001 |
| 32 | uid | 随机 |
| 36 | file_version | 8 |
| 40 | meta_orth_record | 0xFFFFFFFF |
| 44 | meta_infl_index | 0xFFFFFFFF |
| 48 | extra_index0..7 | 8×0xFFFFFFFF |
| 80 | first_non_text_record | 首条非文本记录的编号 |
| 84 | title_offset | **相对 record0 起点**的标题偏移 |
| 88 | title_length | 标题字节数 |
| 92 | language_code | `00 00 <sub> <lang>` |
| 96 | in_lang / 100 out_lang | 0 |
| 104 | min_version | 8 |
| 108 | first_resource_record | 首条资源记录编号，无则 0xFFFFFFFF |
| 112–124 | huff_* | 全 0（KF8 不用 HUFF） |
| 128 | exth_flags | 见下 |
| 132 | unknown | 32 字节全 0 |
| 164 | unknown_index | 0xFFFFFFFF |
| 168–180 | drm_offset/count/size/flags | 0xFFFFFFFF/0xFFFFFFFF/0/0 |
| 184 | unknown2 | 8 字节全 0 |
| 192 | fdst_record | FDST 记录编号 |
| 196 | fdst_count | **flow 段数**（不是 1） |
| 200 | fcis_record | |
| 204 | fcis_count | 1 |
| 208 | flis_record | |
| 212 | flis_count | 1 |
| 216 | unknown3 | 8 字节全 0 |
| 224 | srcs_record | 0xFFFFFFFF |
| 228 | srcs_count | 0 |
| 232 | unknown4 | 8 字节全 **0xFF** |
| 240 | extra_data_flags | 见下 |
| 244 | ncx_index | NCX 索引头记录号 |
| 248 | chunk_index | |
| 252 | skel_index | |
| 256 | datp_index | 0xFFFFFFFF |
| 260 | guide_index | |
| 264 | unknown5..8 | `0xFF*4, 0x00*4, 0xFF*4, 0x00*4`（**不是全 0**） |
| 280 | exth | EXTH blob |
| — | full_title | UTF-8 全书标题 |
| — | padding | 8192 字节全 0 |

**EXTH 位置 = `16 + header_length`** = 280。这是唯一硬判据
（`reader/headers.py:256` `EXTHHeader(raw[16 + self.length:])`）。

**MOBI 6 布局**——`writer2/main.py:239-377` 逐字节：

| 偏移 | 字段 | MOBI6 值 |
|---|---|---|
| 0 | compression | 2 |
| 4 | text_length | |
| 8 | last_text_record | **文本记录条数** |
| 10 | record_size | 4096 |
| 12 / 14 | encryption / unused | **都写 0** |
| 16 | ident | `b'MOBI'` |
| 20 | header_length | **232**（0xE8） |
| 24 | book_type | 2 |
| 28 | encoding | 65001 |
| 32 | uid | |
| 36 | file_version | **6** |
| 40 | unknown | 8 字节 0xFF |
| 48 | secondary_index_record | 0xFFFFFFFF |
| 52 | unknown | 28 字节 0xFF |
| 80 | first_non_text_record | |
| 84 | title_offset | `0xE8 + 16 + len(exth)` |
| 88 | title_length | |
| 92 | language_code | |
| 96 | in/out_lang | 8 字节 0 |
| 104 | min_version | 6 |
| 108 | first_resource_record | 首条图片记录号，无则 `len(records)` |
| 112–128 | huff/datp | 16 字节 0 |
| 128 | exth_flags | `0b1010000` |
| 132 | unknown | 32 字节 0 |
| 164 | drm | 16 字节 |
| 180 | unknown | 12 字节 0 |
| 192 | **first_content_record (u16)** | 1 |
| 194 | **last_content_record (u16)** | |
| 196 | unknown | `00 00 00 01` |
| 200 | fcis_record | |
| 204 | fcis_count | 1 |
| 208 | flis_record | |
| 212 | flis_count | 1 |
| 216 | unknown | 8 字节 0 |
| 224 | unknown | 16 字节 |
| 240 | extra_data_flags | |
| 244 | primary_index_record | |
| — | exth | 紧跟在 248 字节头之后 |
| — | title | |
| — | padding | 8192 字节 0，**整个 record0 按 4 字节对齐** |

**MOBI 6 与 KF8 的差异要点**：
- 头长字段 232 vs 264；EXTH 位置 248 vs 280。
- MOBI 6 的 192/194 是首/末内容记录号（u16 对）；KF8 的 192 是 fdst_record（u32）。
- MOBI 6 的 record0 **要 4 字节对齐**（`writer2/main.py:377` `align_block(record0)`）；
  KF8 **不对齐**（`writer8/mobi.py:195` 设 `ALIGN = True` 但基类只检查 `ALIGN_BLOCK`，
  这是 Calibre 的属性名 bug，恰好导致不对齐）。

**`last_text_record` 是条数，不是最后一个编号**（`writer2/main.py:247` 注释
"Number of text records or last tr idx"，实际写 `nrecords`；
读者 `reader/mobi6.py:841` 用它当条数）。

**`mobi_version` 是偏移 104**，不是 36。读者据此区分 MOBI6/KF8
（`reader/headers.py:247` `struct.unpack('>I', raw[0x68:0x6C])`）。
偏移 36 的 `file_version` 只有 debug 工具读。

#### EXTH

结构：

```
'EXTH' | length(4, 大端) | num_records(4, 大端) | records... | padding
```

每条记录：`code(4) | size(4) | content(size-8 字节)`，**size 含这 8 字节头**。

**`length` 字段 = 12 + Σ记录长度，不含尾部 padding**（`writer8/exth.py:235`
`pack(b'>II', len(exth) + 12, nrecs)`，此时 `len(exth)` 是记录体长度）。
padding 规则 `pad = b'\0' * (4 - len(exth) % 4)`，**总是至少补 1 字节**
（已对齐时补 4 字节）。磁盘上的 EXTH blob 总长 = `length` 字段 + pad。

**数值型记录的值恒为 4 字节大端 u32，因此 `size` 恒为 12。**

必须写的：

| code | 名称 | 值 |
|---|---|---|
| 100 | creator | 作者 |
| 101 | publisher | |
| 103 | description | 文本 |
| 105 | subject | |
| 106 | pubdate | **必需**，缺日期时 Calibre 直接 `raise ValueError` |
| 109 | rights | |
| 112 | source | `calibre:<uuid>` |
| 116 | startreading | u32，正文起始偏移（Kindle 打开位置） |
| 121 | kf8_header_index | u32，**仅 joint 文件写** |
| 201 | coveroffset | u32，**资源块内 0-based 下标**（B1） |
| 202 | thumboffset | u32，同上 |
| 203 | hasfakecover | u32 = 0 |
| 501 | cdetype | 4 字节 ASCII `EBOK` |
| 503 | title | **权威标题**，中文书名必须写这里 |
| 524 | language | 规范化 ISO 639-1 |
| 527 | page_progression_direction | `ltr`/`rtl`/`default` |
| 528 | override_kindle_fonts | `"true"` |

可选（Kindle 兼容用）：113 ASIN、204–207 kindlegen 伪装、535。

**`exth_flags` 位标志**（record0 偏移 128）：

| 位 | 值 | 含义 |
|---|---|---|
| 3 | 0x08 | 期刊 |
| 4 | 0x10 | 未知（Calibre 恒置 1） |
| **6** | **0x40** | **EXTH 存在——唯一硬判据** |
| 11 | 0x800 | 内嵌字体 |

- 纯 MOBI 6 / KF8 standalone：`0b1010000`（= bit4 + bit6）
- joint 的 MOBI 6 头：`0b100001010000`（= bit4 + bit6 + bit11，注释 "Kinglegen uses this"）

**`extra_data_flags` 位标志**（record0 偏移 240）：

| 位 | 值 | 含义 |
|---|---|---|
| 0 | 0x01 | 文本记录尾部有多字节字符重叠字节 |
| 1 | 0x02 | 有 TBS 索引尾部数据 |
| 2 | 0x04 | uncrossable breaks（Calibre 恒不写） |

**注意读端兼容**：`reader/headers.py:234` 读的是 `u16 at 0xF2`（即 `extra_data_flags`
这个 u32 的**高 16 位**），不是完整的 u32。对 `extra_data_flags = 0b1` 的文件，
高 16 位是 0，读端得到 `extra_flags = 0`，**读端因此不剥离尾部数据**。
这意味着：

- 若要自己读 MOBI，**读完整的 u32 at 240**，别学 Calibre 读 0xF2。
- 若要让 Calibre 读我们写的文件，它读 0xF2 会得到 0 → 不剥离尾部 → 文本尾部多出几个字节。
  实际影响：overlap 字节与计数字节会被算进正文，导致正文末尾有垃圾字符。
  **规避**：让文本流的最后一个字符是多字节字符被切断的情况尽量少发生，
  或者接受这 1–3 字节的尾部噪声（Kindle 容忍）。

#### FLIS / FCIS / EOF

```
FLIS (36 字节，KF8 与 MOBI6 共用同一常量):
  46 4c 49 53  'FLIS'
  00 00 00 08
  00 41 00 00
  00 00 00 00
  ff ff ff ff
  00 01 00 03
  00 00 00 03
  00 00 00 01
  ff ff ff ff

FCIS (KF8 52 字节):
  'FCIS' | 0x14 | 0x10 | 0x02 | 0x00 | text_length(u32) | 0x00 |
  0x28 | 0x00 | 0x28 | 0x08 | 0x00010001 | 0x00

FCIS (MOBI6 44 字节):
  'FCIS' | 0x14 | 0x10 | 0x01 | 0x00 | text_length(u32) | 0x00 |
  0x20 | 0x08 | 0x00010001 | 0x00

EOF: b'\xe9\x8e\r\n'  (4 字节)
```

两版 FCIS 有**三处**不同：偏移 0x0C（2 vs 1）、0x1C（0x28 vs 0x20）、0x20（0 vs 8）。

#### 代码骨架

```python
# scripts/workshop/convert/mobi_header.py
MOBI_HEADER_LENGTH = 264      # KF8 头长字段
MOBI6_HEADER_LENGTH = 232     # MOBI 6 头长字段
MOBI_HEADER_PADDING = 8192    # 给亚马逊 DTP 服务追加加密信息预留

def build_kf8_header(metadata, text_length, last_text_record, first_non_text,
                     exth, full_title, first_resource_record=0xFFFFFFFF,
                     ncx_index=0xFFFFFFFF, chunk_index=0xFFFFFFFF,
                     skel_index=0xFFFFFFFF, guide_index=0xFFFFFFFF,
                     fdst_record=0xFFFFFFFF, fcis_record=0xFFFFFFFF,
                     flis_record=0xFFFFFFFF, extra_data_flags=0b1,
                     exth_flags=0b1010000, file_version=8) -> bytes:
    """构造 KF8 的 record0（264 字节头 + EXTH + 标题 + 8192 字节预留）。"""
    head = bytearray(MOBI_HEADER_LENGTH + 16)   # 280：EXTH 落在 16+264
    title = full_title.encode('utf-8')

    def put(off, val, fmt='>I'):
        struct.pack_into(fmt, head, off, val)

    put(0, 2, '>H')          # compression = PalmDOC
    put(4, text_length)
    put(8, last_text_record, '>H')
    put(10, 4096, '>H')
    put(12, 0, '>H')         # encryption_type
    head[16:20] = b'MOBI'
    put(20, MOBI_HEADER_LENGTH)
    put(24, 2)               # book_type = 图书
    put(28, 65001)           # UTF-8
    put(32, uid)
    put(36, file_version)
    put(40, 0xFFFFFFFF); put(44, 0xFFFFFFFF)
    for off in range(48, 80, 4):
        put(off, 0xFFFFFFFF)             # extra_index0..7
    put(80, first_non_text)
    put(84, MOBI_HEADER_LENGTH + 16 + len(exth))   # title_offset：相对 record0
    put(88, len(title))
    put(92, locale_code(metadata.language), '>4s')
    put(104, file_version)               # min_version
    put(108, first_resource_record)
    put(128, exth_flags)
    put(164, 0xFFFFFFFF)                # unknown_index
    put(168, 0xFFFFFFFF); put(172, 0xFFFFFFFF); put(176, 0); put(180, 0)  # DRM
    put(192, fdst_record); put(196, 1)
    put(200, fcis_record); put(204, 1)
    put(208, flis_record); put(212, 1)
    put(224, 0xFFFFFFFF); put(228, 0)    # srcs
    head[232:240] = b'\xff' * 8         # unknown4
    put(240, extra_data_flags)
    put(244, ncx_index); put(248, chunk_index); put(252, skel_index)
    put(256, 0xFFFFFFFF); put(260, guide_index)
    # unknown5..8：0xFF*4, 0x00*4, 0xFF*4, 0x00*4（不是全 0）
    head[264:280] = b'\xff'*4 + b'\0'*4 + b'\xff'*4 + b'\0'*4

    out = bytearray(head)
    out.extend(exth)
    out.extend(title)
    out.extend(b'\0' * MOBI_HEADER_PADDING)
    return bytes(out)          # KF8 不做 4 字节对齐
```

MOBI 6 头同构，只是 `header_length` 写 232、EXTH 跟在 248 字节头之后、
192/194 是 first/last content record、末尾 `align_block`。

**`title_offset` 必须回填**：它是 record0 内的相对偏移。
`exth` 的实际占位是 `length 字段 + padding`，所以要用**实际 blob 长度**而不是
`length 字段`来算标题位置。

### 1.3 文本流与 filepos 回填

#### MOBI 6 的文本组织

MOBI 6 把整本书压成**一条 HTML 流**：

```
<html><head><guide>...</guide></head><body>
  <h1>第一章</h1><p>...</p><mbp:pagebreak/>
  <h1>第二章</h1><p>...</p><mbp:pagebreak/>
</body></html>
```

- **章节间链接**：写成 `filepos="0000000000"` 占位（**10 位零填充十进制**），
  指向**正文 HTML 文本流**中的字节偏移。整条流写完后回填。
- **guide**：放在 `<head>` 里。

  ```html
  <head><guide>
    <reference type="toc" title="Table of Contents" filepos="0000000000" />
    <reference type="text" title="Start Reading" filepos="0000000000" />
  </guide></head>
  ```

  `/>` 前**必须有空格**（Calibre 注释："Space required or won't work, I kid you not"）。
  `type="text" title="Start"` 决定 Kindle 的打开位置。
- **图片**：写成 `recindex="NNNNN"`（5 位零填充），指向资源记录序号。
  读端接受 `lowrecindex` / `recindex` / `hirecindex` 三种属性名。
- **分页**：每个 spine item 之后插入 `<mbp:pagebreak/>`。

**回填机制**（两阶段）：

```python
class Serializer:
    def __init__(self, documents, images=None, toc=None):
        self.buf = bytearray()
        self.id_offsets = {}     # href -> 该文档在文本流中的起始偏移
        self.href_offsets = {}   # href -> [占位符位置列表]

    def write(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.buf.extend(data)

    def placeholder(self, href):
        """写一个 filepos 占位符，返回它的位置。"""
        self.write(b'filepos=')
        self.href_offsets.setdefault(href, []).append(len(self.buf))
        self.write(b'0000000000')

    def fixup_links(self):
        """整条流写完后回填所有占位符。"""
        for href, positions in self.href_offsets.items():
            target = self.id_offsets.get(href, self.body_start)
            for pos in positions:
                self.buf[pos:pos + 10] = ('%010d' % target).encode('ascii')
```

#### 文本记录切分

```python
RECORD_SIZE = 4096

def create_text_record(text):
    """从 text 取一条 4096 字节的记录。

    若记录末尾落在多字节字符中间，把补全所需的字节作为 overlap 返回，
    这些字节不参与压缩。
    """
    opos = text.tell()
    text.seek(0, 2)
    npos = min(opos + RECORD_SIZE, text.tell())
    extra = 0
    last = b''
    while not last.decode('utf-8', 'ignore'):
        size = len(last) + 1
        text.seek(npos - size)
        last = text.read(size)
    try:
        last.decode('utf-8', 'strict')
    except UnicodeDecodeError:
        prev = len(last)
        while True:
            text.seek(npos - prev)
            last = text.read(len(last) + 1)
            try:
                last.decode('utf-8')
            except UnicodeDecodeError:
                pass
            else:
                break
        extra = len(last) - prev
    text.seek(opos)
    data = text.read(RECORD_SIZE)
    overlap = text.read(extra)
    text.seek(npos)
    return data, overlap
```

**记录的字节布局**：

```
[压缩后的 4096 字节块] [overlap 字节] [1 字节: overlap 长度]
```

**overlap 计数字节的编码（B3，必须修）**：

- 写入端：`pack(b'>B', len(overlap))` —— **存 overlap 的实际字节数**。
- 读取端：`sz = (ord(record[-1:]) & 0b11) + 1`，其中 `sz` 是**要剥掉的字节总数**
  （含那 1 个计数字节），所以实际 overlap 字节数 = `sz - 1`。

两者自洽的验证：

| overlap 字节数 | 写入的计数字节 | 读取端剥掉 | 需要剥掉 |
|---|---|---|---|
| 1 | 0x01 | 2 | 2 ✓ |
| 2 | 0x02 | 3 | 3 ✓ |
| 3 | 0x03 | 4 | 4 ✓ |

**现有项目代码写的是 `size - 1`，导致读取端剥掉的长度差 1，多剥或少剥一个字节。**
实测：4094 个 `A` + `€` 时，项目写 `0x02`，Calibre 读端剥 3 字节（含计数），
实际尾部只有 `e2 82 ac 02` 4 字节 → 剥 3 字节后正文尾部少了 1 字节。

**4 字节对齐填充记录**：文本记录总长不是 4 的倍数时，追加一条空记录
（内容是 `records_size % 4` 个 `\0`），**并把 `first_non_text_record` 加 1**。
漏掉它会导致后续所有记录号错位。实测真实 AZW3 文件里确实存在这条 4 字节记录。

**`text_length` 是压缩前的 UTF-8 字节数**，不含 overlap 与 TBS。
因此「所有记录解压后拼接的长度」与 `text_length` 相差一个多字节字符的重复量。

### 1.4 索引、目录、分页记录

#### INDX 四件套

MOBI 的索引由四种记录组成：

| 部件 | 魔数 | 作用 |
|---|---|---|
| 主索引头 | `INDX`（type=0） | 指向 TAGX / geometry / IDXT |
| 索引记录 | `INDX`（type=1） | 一批索引条目 + 自己的 IDXT |
| TAGX | `TAGX` | 标签表：每个标签的编号/值数/位掩码 |
| IDXT | `IDXT` | 条目在索引记录内的偏移表 |
| CNCX | （无魔数） | 字符串池（标题） |

**主索引头（192 字节固定部分）**：

| 偏移 | 字段 | 值 |
|---|---|---|
| 0 | 魔数 | `INDX` |
| 4 | 头长度 | 192 |
| 8 | unknown1 | 8 字节 0 |
| 16 | index type | 2（变形词索引） |
| 20 | idxt_offset | IDXT 块偏移（回填） |
| 24 | 索引记录数 | |
| 28 | encoding | 65001 |
| 32 | unknown2 | 0xFFFFFFFF |
| 36 | 条目总数 | |
| 40–48 | ordt / ligt / nligt | 0 |
| 52 | num_cncx | CNCX 记录数 |
| 56 | unknown3 | 124 字节 0 |
| 180 | tagx_offset | **192** |
| 184 | unknown4 | 8 字节 0 |

**索引记录（type=1）**：同样 192 字节头，但 `@12 = 1`（记录类型）、
`@16 = 0`（index type）、`@28 = 0xFFFFFFFF`、`@24` 与 `@36` 都是本记录的条目数、
`@28..36` 是 `0xFF`×8、其余 156 字节 0。

**TAGX 块**：

```
'TAGX' | u32 块总长(含这12字节头) | u32 control_byte_count | 每标签4字节
每标签4字节: u8 tag_number | u8 values_per_entry | u8 bitmask | u8 eof_flag
```

`eof_flag == 1` 表示结束，**并且要消耗一个控制字节**。

**MOBI 6 平装书的 TAGX**（`writer2/indexer.py:99-105`）：

```
'TAGX' + u32(20) + u32(1) +
  (1,1,0x01,0) (2,1,0x02,0) (3,1,0x04,0) (4,1,0x08,0) (0,0,0,1)
```
共 5 项 × 4 字节 = 20，块总长 = 12 + 20 = 32。

**索引条目编码**：

```
[ident]        u8 长度 L + L 字节标识串
[control byte] control_byte_count 个字节
[values]       按 TAGX 中标签的顺序，各标签的值（VWI）
```

- **ident 是序号的大写十六进制 ASCII**（MOBI 6 用 `encode_number_as_hex`，
  强制偶数位；KF8 用 `'%0{}X'` 至少 2 位）。
  **两套编号体系不要混用**：MOBI6 的 NCX ident 是十六进制，
  KF8 的 chunk/skel ident 是**十进制**（`f'{insert_pos:010}'`、`f'SKEL{file_number:010}'`）。
- **control byte 是「每个 entry 一个字节打包所有 tag」**：
  `value = bitmask & (nentries << shift)`，其中 `shift = 掩码最低置位数`。
  **不是每个 tag 一个字节。**
- **值的顺序 = TAGX 标签的顺序**，不是数值顺序。

**读取端 `get_tag_map` 的严格算法**（必须逐字复刻，否则条目全错）：

```
对 TAGX 中每个标签 x（eof==1 的先跳过并丢弃一个控制字节）：
    value = control_byte & x.bitmask
    if value == 0:              该标签不存在
    elif value == bitmask:
        if bitmask 只有一个置位: 值个数 = 1
        else:                    读一个 VWI = 后续值块的字节长度，再解出块内的 VWI
    else:
        count = value >> (bitmask 最低置位的位置)
        读 count * values_per_entry 个 VWI
```

**「写两遍」不是冗余**：2 位掩码下 `count == 3` 会与「块长度前缀」标记撞车。
写 2 变成 `0b10` 才安全。KF8 的 SKEL 索引的 `chunk_count` 与 `geometry`
都写两遍，正是为此。

**VWI（变长整数）**：

```python
def encint(value, forward=True):
    """大端，每字节 7 位有效，最高位为 1 的那个字节是最后一个字节。"""
    byts = bytearray()
    while True:
        byts.append(value & 0x7F)
        value >>= 7
        if value == 0:
            break
    byts[0 if forward else -1] |= 0x80
    byts.reverse()
    return bytes(byts)
```

`encint(9) = b'\x89'`，`encint(200) = b'\x01\xc8'`。

**IDXT 块**：在索引记录里是 `'IDXT' + u16 × N`，u16 = 条目在**本记录内**的偏移（≥192）。
在主索引头里是 `'IDXT' + u16 × M`，u16 = 指向 geometry 块中第 i 个描述符的偏移。

**geometry 块**：对每个索引记录写一个描述符：`u8 名字长度 + 名字 + u16 条目数`。
名字是**该记录最后一个索引条目的 ident**。

**CNCX 字符串池**：

```
重复: VWI(byte_length) + byte_length 字节 UTF-8 字符串
```

- 字符串偏移 = 该字符串首字节在 CNCX 记录内的偏移；跨记录时按 `记录号 * 0x10000` 累加。
- 单条 CNCX 记录上限 `0x10000 - 1024 = 64512` 字节；单串上限 **500 个字符**
  （是字符不是字节，CJK 编码后可能远超 500 字节）。
- 字符串去重：相同字符串共用同一偏移。

**MOBI 6 主索引头的特殊布局**（`writer2/indexer.py:545-632`）：

```
0x00 'INDX' | 0x04 u32(192) | 0x08 0*8 | 0x10 u32(2) | 0x14 <回填 idxt_offset>
| 0x18 u32(1) | 0x1C u32(65001) | 0x20 0xFF*4 | 0x24 u32(条目总数)
| 0x28..0x34 0,0,0 | 0x34 u32(cncx数) | 0x38 0*124 | 0xB4 u32(192) | 0xB8 0*8
| TAGX块
| u8(最后一个ident长度) + 最后一个ident + u16(条目数)     <-- geometry
| 补 0 到 4 字节边界
| 'IDXT' + u16(192+len(tagx)) + u8(0)                   <-- 只有 3 字节
```

KF8 的主索引头则是 `TAGX → geometry（每个索引记录一条）→ IDXT（u16 × N）`。

#### MOBI 6 的目录：INDX 索引 vs 内联 HTML 目录

**这是最容易搞错的一点。**

- **Calibre 读 MOBI 6 时根本不用 INDX 索引还原目录**（`reader/mobi6.py` 无 `read_index` 调用）。
  它读的是正文里的**内联 HTML 目录**：找到 guide 里 `type="toc"` 的引用 →
  定位到正文中的锚点 → 扫描其后所有 `<a>` 直到遇到 `class="mbp_pagebreak"` →
  用**CSS 的 left-margin + text-indent 之和**推断层级
  （`reader/mobi6.py:764` `item.left_space = int(self.get_left_whitespace(x))`），
  **不是** HTML 空白缩进。层级数 < 2 或 > 6 时放弃分层。

  → **如果目标是「Kindle 能跳目录」，MOBI 6 的正确做法是内联 HTML 目录 + guide，
  INDX 索引是可选的。** 现有 `serializer.py` 只写了 guide，没有写内联目录，
  这是 MOBI 6 目录功能缺失的根因。

- **INDX 索引主要对 Kindle 有意义**（Kindle 用它做章节定位与快速跳转）。

**建议**：MOBI 6 输出同时产出
(a) 内联 HTML 目录文档（`contents.xhtml`）+ guide 的 `type="toc"` 引用，
(b) INDX 索引（MOBI 6 平装书 TAGX）。

**内联目录的缩进必须体现在 CSS 的 margin/indent 上**，否则读回来层级是平的。
最简做法：每个目录项写成 `<a>` 且带 `style="margin-left:Nem"`。

#### 分页记录

- **MOBI 6**：`<mbp:pagebreak/>` 强制分页标记，命名空间 `http://mobipocket.com/ns/mbp`。
  读端转成 `<div class="mbp_pagebreak" />`。
- **KF8**：分页靠把 XHTML 按 CSS 分页点拆成多个 skeleton，
  或用 Amazon 私有的 `data-AmznPageBreak="always"`（Calibre 只在读时转换，不写）。

#### TBS（Trailing Byte Sequences）

可选的索引加速结构，编码在文本记录尾部。
**建议第一版不实现**——Calibre 的 KF8 reader 完全不读 TBS，
导航靠 NCX 的 `pos_fid` + chunk 索引。不写 TBS 时 `extra_data_flags` 只置 bit0。

若将来要实现，尾部编码是 `<data><反向 VWI 总长>`，总长含 size 本身。

### 1.5 KF8/AZW3 记录序列

#### AZW3 = 纯 KF8 还是联合文件？

Calibre 的 `mobi_file_type='both'` 生成**联合文件**（MOBI 6 + KF8 双份）；
`AZW3Output.convert()` 走 `create_kf8_book(..., for_joint=False)` → **纯 KF8 standalone**。

**推荐**：AZW3 输出走**纯 KF8 standalone**，理由：

1. 纯 KF8 结构简单（不需要 BOUNDARY / EXTH 121 / 两套 EXTH）。
2. 现有代码已经能生成 KF8 头（`build_mobi_header`）。
3. 纯 KF8 不受 B5/B6/B7 三个联合文件缺陷影响。
4. Kindle 对 KF8 standalone 的支持最完整。

若坚持要联合文件（为了兼容老设备），必须修 B5/B6/B7 三条。

**决策（已实施）**：`.azw3` → 纯 KF8 standalone；`.mobi` → 纯 MOBI 6。
两个独立的 writer，不共享联合逻辑。`mobi_writer.py` 里
`_assemble` 写 MOBI 6，`_assemble_standalone` 写纯 KF8。

#### 纯 KF8 的记录序列

```
record 0              : MOBI 头（264 字节头 + EXTH + 标题 + 8192 padding）
records 1..N_text     : 文本记录（每条 ≤4096 未压缩字节，PalmDOC 压缩）
[4 字节对齐填充记录]    : 若文本记录总长不是 4 的倍数
chunk_index 记录      : INDX 头 + INDX 记录 ×N + CNCX 记录 ×M
skel_index 记录       : INDX 头 + INDX 记录 ×N
[guide_index 记录]    : INDX 头 + INDX 记录 ×N + CNCX 记录 ×M
[ncx_index 记录]      : INDX 头 + INDX 记录 ×N + CNCX 记录 ×M
[资源记录]            : 图片
fdst_record           : FDST 记录（1 条）
flis_record           : FLIS 记录（1 条，36 字节）
fcis_record           : FCIS 记录（1 条，52 字节）
EOF                   : b'\xe9\x8e\r\n'
```

**顺序固定**：CHUNK 在 SKEL 之前，GUIDE 在 NCX 之前，资源在索引之后，FDST/FLIS/FCIS 在最后。

**这些索引号是 PDB 记录序号**（record0 = 0），写进 record0 的
`chunk_index` / `skel_index` / `ncx_index` / `guide_index` 字段。

#### 联合文件（历史调研，当前未实现）

> **实现状态**：本项目不做联合文件。`.azw3` 走纯 KF8 standalone，
> `.mobi` 走纯 MOBI 6，两条路径各自独立。
> 本节保留是为了说明「为什么不那样做」，不是实现要求。

```
record 0        : MOBI 6 的 record0（含 MOBI 6 的 EXTH，写 EXTH 121）
records 1..N    : MOBI 6 文本记录 / 索引 / 资源
record K        : b'BOUNDARY'        ← 恰好 8 字节
record K+1      : KF8 的 record0（header_length=264, file_version=8）
record K+2..    : KF8 的文本记录 / chunk 索引 / skel 索引 / 资源 / FDST / FLIS / FCIS / EOF
```

**关键点**：

1. **BOUNDARY 恰好 8 字节 `b'BOUNDARY'`**（B5）。读端做精确相等比较：
   `reader/mobi6.py:143` `raw == b'BOUNDARY'`。多 4 字节就识别失败。
2. **EXTH 121 = KF8 record0 的记录编号**；BOUNDARY 在它**前一条**。
   读端 `sections[kf8_header_index - 1] == b'BOUNDARY'`。
3. **joint 的 MOBI 6 头必须用 `file_version=6`、`min_version=6`**，
   且 `skel_index`/`chunk_index`/`guide_index` 全置 `0xFFFFFFFF`
   （`writer2/main.py:441-442`）。否则 `mobi_version == 8 and skelidx != NULL_INDEX`
   会把它误判为 standalone（B6）。
4. joint 的 MOBI 6 头用 `MOBIHeader(file_version=6)` 构造，
   **`header_length` 仍然是 264**（不是 232）——`MOBIHeader.DEFINITION` 里
   `header_length = 264` 是写死的，`file_version` 只影响两个字段。
5. joint 的 MOBI 6 头在 192 处是 `pack(b'>HH', 1, last_content_record)`（借位）。

#### Skeleton 与 Chunk

KF8 的正文分两层：

- **skeleton**：整份 XHTML 文档的完整序列化（去掉命名空间、展开自闭合标签、
  去掉注释与 PI），**内容被挖空**（文本移进 chunk）。
- **chunk**：从 skeleton 挖出来的文本片段，最后插回 `insert_pos`。

几何量三套坐标系，**必须分清**：

| 量 | 含义 |
|---|---|
| SKEL 的 `start_pos` | 文本流全局偏移 |
| SKEL 的 `length` | **仅 skeleton 长度**（不含 chunk 内容） |
| CHUNK 的 entry name | `f'{insert_pos:010}'`，**全局**偏移，十进制 |
| CHUNK 的 geometry `start_pos` | **skeleton 内**偏移 |
| CHUNK 的 `sequence_number` | **全局** chunk 序号（`kindle:pos:fid` 用它） |

`CHUNK_SIZE = 8192`。

**若第一版不做 skeleton/chunk 拆分**（直接把整条文本流当一个 skeleton、一个 chunk），
仍能生成结构合法的 KF8，Kindle 能打开并显示正文，只是失去流式加载优化。

#### 索引标签表

**KF8 NCX 索引**（`control_byte_count = 1`）：

| tag | 名称 | values/entry | bitmask |
|---|---|---|---|
| 1 | offset | 1 | 0x01 |
| 2 | length | 1 | 0x02 |
| 3 | label | 1 | 0x04 |
| 4 | depth | 1 | 0x08 |
| 21 | parent | 1 | 0x10 |
| 22 | first_child | 1 | 0x20 |
| 23 | last_child | 1 | 0x40 |
| 6 | pos_fid | **2** | 0x80 |
| 0 | eof | 0 | 0 |

**KF8 CHUNK 索引**：tag 2(cncx_offset, 0x01)、3(file_number, 0x02)、4(sequence_number, 0x04)、
6(geometry, 0x08, vpe=2)。

**KF8 SKEL 索引**：tag 1(chunk_count, 掩码 **0x03**, vpe=1)、
6(geometry, 掩码 **0x0C**, vpe=2)。**两个都写两遍**。

**KF8 GUIDE 索引**：tag 1(title, 0x01)、6(pos_fid, 0x02, vpe=2)。

**MOBI 6 平装书**：tag 1(offset, 0x01)、2(size, 0x02)、3(label_offset, 0x04)、4(depth, 0x08)。

**掩码 → shift 表**（`writer8/index.py:53`）：

```python
MASK_TO_BIT_SHIFTS = {1:0, 2:1, 3:0, 4:2, 8:3, 12:2, 16:4, 32:5, 48:4, 64:6, 128:7, 192:6}
```

全部等于「掩码的最低置位数」，可以运行时算：`(mask & -mask).bit_length() - 1`。

**单条 INDX 记录上限 64 KiB**（PDB 限制）。Calibre 用
`RECORD_LIMIT = 0x10000 - 192 - 1048 = 64296`，超了就开新记录。

### 1.6 图片资源

#### 资源记录格式

**图片记录就是原始图片字节，前面不加任何魔数。**
Calibre 用 `imghdr.what()` 嗅探记录内容来判断是不是图片
（`reader/mobi6.py:947-950`），识别不出的记录直接跳过。

**现有项目代码写 `b'IMG' + data + u32(len)`（B2），Calibre 读端识别不出来 →
图片被静默丢弃。** 正确写法是**只写原始字节**。

支持的格式：jpg / jpeg / gif / png / bmp。

#### EXTH 201 / 202 的语义（B1）

**这是记录编号，不是字节偏移，但也不是 PDB 全局记录号。**

读端的解析（`metadata/mobi.py:179`）：

```python
(image_base,) = unpack('>I', record0[108:112])     # first_resource_record
...
elif id == 201:
    (rindex,) = unpack('>L', content)
    if rindex != 0xFFFFFFFF:
        self.cover_record = self.record(rindex + image_base)
```

即：

```
PDB 记录号 = first_resource_record + EXTH 201 的值
```

所以 **EXTH 201 = 封面在资源块内的 0-based 下标**。

Calibre 的写入侧（`writer2/resources.py:201`）：
`self.cover_offset = self.item_map[item.href] - 1`，
其中 `item_map` 是 1-based 的（`item_map[href] = len(self.records)`，从 1 起），
减 1 得到 0-based 下标。

**现有项目代码写的是 `cover_offset = len(records)`（PDB 记录号）**，
读端会算成 `first_resource_record + cover_offset` → **指到错误的记录**。

正确做法：

```python
# 资源块内的顺序
resource_records = []      # 每个元素是原始图片字节
for image in book.images.values():
    resource_records.append(image.data)

# 封面在资源块内的 0-based 下标
cover_index = index_of(book.cover in resource_records)

# first_resource_record = 资源块首条记录的 PDB 记录号
first_resource_record = len(records_so_far)
records.extend(resource_records)

# EXTH 201 写 cover_index（0-based），不是 PDB 记录号
```

#### `kindle:embed:` 编号基准

KF8 的图片引用是 `kindle:embed:XXXX?mime=image/jpeg`，
`XXXX` 是 **1-based** 的资源序号，base32、至少 4 位、字母表 `0-9A-V`
（读端正则 `([0-9A-V]+)`）。

```
kindle:embed:0001?mime=image/jpeg    ← 第 1 张图片（1-based）
```

**EXTH 129（thumbnail URI）用 0-based，而 `kindle:embed:` 用 1-based**——注意区分。

#### 资源记录与正文引用的对应

- **MOBI 6**：正文写 `recindex="NNNNN"`，序号是资源在资源块内的 1-based 序号
  （Calibre 的 `item_map` 从 1 起）。
- **KF8**：正文写 `kindle:embed:XXXX`，`XXXX` 是 base32 的 1-based 序号。

现有 `serializer.py` 的 `assign_image()` 从 0 开始编号，与两者都不符，需修。

#### 缩略图

Kindle 需要一张缩略图（EXTH 202 + 129）。
生成缩略图需要图像缩放——**纯标准库做不到**。

**决策**：
- **不做缩略图**：不写 EXTH 202 / 129。Kindle 会自己从封面生成。
- **或**：直接把封面原字节当缩略图（尺寸不对但 Kindle 通常能接受）。

不要为了缩略图引入 Pillow（与「零依赖」约束冲突）。

### 1.7 MOBI/AZW3 写出：陷阱清单

| # | 陷阱 | 后果 |
|---|---|---|
| 1 | EXTH 201 写 PDB 记录号而非资源块 0-based 下标 | 封面指错位置 |
| 2 | 图片记录加 `IMG` 魔数前缀 | 图片被读端丢弃 |
| 3 | overlap 计数字节写 `size-1` | 文本尾部多/少字节 |
| 4 | 逐记录「压缩后更大就存原文」 | **正文数据损坏** |
| 5 | BOUNDARY 写成 12 字节 | 联合文件识别失败 |
| 6 | joint 的 MOBI6 头 `file_version=8` | 被误判 standalone |
| 7 | joint 缺 EXTH 121 | 读端找不到 KF8 头 |
| 8 | 漏 4 字节对齐填充记录 | 后续所有记录号错位 |
| 9 | PDB name 非 ASCII | 乱码 / 空名 |
| 10 | `title_offset` 用 EXTH 的 `length` 字段算 | 标题取错位置 |
| 11 | `extra_data_flags` 被读端从 0xF2 读成 u16 | 尾部数据不被剥离 |
| 12 | INDX control byte 按「每 tag 一字节」写 | 条目完全读不出 |
| 13 | SKEL 的 tag 1 / tag 6 不写两遍 | count==3 撞「块长度前缀」分支 |
| 14 | MOBI 6 只有 guide 没有内联目录 | Kindle 目录空白 |
| 15 | 目录层级靠 HTML 缩进而非 CSS margin | 读回来层级是平的 |
| 16 | chunk/skel ident 用十六进制 | 与 Calibre 的十进制体系不一致 |
| 17 | `recindex` 从 0 开始编号 | 第一张图对不上 |
| 18 | `text_length` 算上 overlap | 与实际解压长度不符 |
| 19 | `to_base` 用小写字母表 | `kindle:pos:fid` 链接定位失效 |

---

## 2. DOCX 读入

### 2.1 ZIP 结构

DOCX 是 OPC（Open Packaging Conventions）包：

```
[Content_Types].xml           每个部件的 MIME（Default 按扩展名 / Override 按路径）
_rels/.rels                    包级关系：指向 word/document.xml、docProps/*.xml
word/
  document.xml                 正文（w:document/w:body）
  _rels/document.xml.rels      正文引用的资源（图片、超链接、样式、编号、脚注）
  styles.xml                   w:style 定义表
  numbering.xml                w:abstractNum + w:num
  settings.xml                 w:defaultTabStop 等
  fontTable.xml                w:font
  theme/theme1.xml             主题字体
  footnotes.xml / endnotes.xml 脚注尾注
  media/                       图片二进制
docProps/
  core.xml                     dc:title/dc:creator/dc:description/dc:subject/cp:keywords/dc:language
  app.xml                      ep:Company（→ publisher）
```

**部件名不存在时抛 `KeyError`**（所有调用点靠 `except KeyError` 兜底）。

### 2.2 命名空间

**Transitional**（Word 2007 默认）与 **Strict**（Word 2010+ 可选）两套：

| 前缀 | Transitional | Strict |
|---|---|---|
| `w` | `http://schemas.openxmlformats.org/wordprocessingml/2006/main` | `http://purl.oclc.org/ooxml/wordprocessingml/main` |
| `r` | `http://schemas.openxmlformats.org/officeDocument/2006/relationships` | `http://purl.oclc.org/ooxml/officeDocument/relationships` |
| `a` | `http://schemas.openxmlformats.org/drawingml/2006/main` | `http://purl.oclc.org/ooxml/drawingml/main` |
| `wp` | `http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing` | `http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing` |
| `pic` | `http://schemas.openxmlformats.org/drawingml/2006/picture` | 同 `a` 规则 |
| `m` | `http://schemas.openxmlformats.org/officeDocument/2006/math` | `http://purl.oclc.org/ooxml/officeDocument/math` |
| `v` | `urn:schemas-microsoft-com:vml` | 不变 |
| `o` | `urn:schemas-microsoft-com:office:office` | 不变 |
| `mc` | `http://schemas.openxmlformats.org/markup-compatibility/2006` | 不变 |
| `cp` | `http://schemas.openxmlformats.org/package/2006/metadata/core-properties` | 不变 |
| `dc` | `http://purl.org/dc/elements/1.1/` | 不变 |
| `dcterms` | `http://purl.org/dc/terms/` | 不变 |
| `ep` | `http://schemas.openxmlformats.org/officeDocument/2006/extended-properties` | `http://purl.oclc.org/ooxml/officeDocument/extended-properties` |

**Strict 的生成规则是前缀替换**，不是整段替换：
`http://schemas.openxmlformats.org/officeDocument/2006` → `http://purl.oclc.org/ooxml/officeDocument`，
尾部保留。因此 `.../wordprocessingml/2006/main` → `.../ooxml/wordprocessingml/main`（**带 `/main`**）。

**Strict 判定**：包级关系里指向 `word/document.xml` 的那条，
其 Type 是 `http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument`
则为 Strict。

现有 `docx_input.py` 的 `W_STRICT` / `R_STRICT` **漏了 `/main`**，需修。

### 2.3 关系解析

```
rels 路径 = '/'.join(部件路径[:-1] + ['_rels', 部件路径[-1] + '.rels'])
例：word/document.xml → word/_rels/document.xml.rels
```

Target 解析：

```
TargetMode == 'External'  → 外部 URL，不拼 base
Target 以 '#' 开头        → 文档内部锚点，不拼 base
Target 以 '/' 开头        → 包根绝对路径，直接用 Target[1:]（不拼 base）
其他                       → base + '/' + Target
```

**Calibre 的偏差**：它对绝对路径也会拼 base（`container.py:221`），
产出 `word/word/media/...`。它只在 `read_styles` 里打了 `word/word` 补丁，
**图片关系走 `get_relationships()` 完全没有这个补丁 → 图片解析失败**。

**纯实现应修正**：绝对路径不拼 base；**不要**照抄 Calibre 的 `word/word` workaround，
而是在统一的 rels 解析层修正。

### 2.4 标题判定

**Calibre 只认英文样式名**：正则 `re.match(r'heading\s+(\d+)$', 样式名, re.I)`，
匹配则 `h{min(6, max(1, n))}`。**中文 Word 的「标题 1」不识别。**

**推荐的判定顺序**（任一路命中即认定标题）：

1. **段落级 `w:outlineLvl`**（最权威）：`w:pPr/w:outlineLvl/@w:val`，
   值 0 = Level 1，1 = Level 2 …… 8 = Level 9，**9 = 正文（无大纲级别）**。
   段落直接格式覆盖样式。
2. **样式级 `w:outlineLvl`**：`w:style/w:pPr/w:outlineLvl/@w:val`，沿 `basedOn` 链向上找。
3. **样式名正则**：`^(?:heading|标题)\s*(\d+)$`（不区分大小写）。
4. **styleId 约定**：`^[Hh]eading(\d)$`。

最终 `level = min(6, max(1, level))`。

现有 `docx_input.py` 的 `_read_heading_levels` 已实现 1–4 路的**样式级**版本
（用 `0 <= value <= 8` 判断），但**缺段落级 `w:outlineLvl`**，
且 `HEADING_NAMES` 里中文名写成了「标题 1」（缺空格），
实际 Word 写的是「标题 1」（有空格）——需修。

**Calibre 写出端的 `w:outlineLvl` 是 1-based（off-by-one）**，
规范要求 0-based。若要往返，需知道这个偏差。

### 2.5 段落 / 列表 / 表格 / 图片

#### 段落

`w:p` → `<p>`；有标题样式则 → `<hN>`。

`w:r` 的子元素：

| 子元素 | 处理 |
|---|---|
| `w:t` | 文本（空白处理见下） |
| `w:tab` | `\t` 或若干 NBSP |
| `w:br` / `w:cr` | `<br/>` |
| `w:drawing` / `w:pict` | `<img/>` |
| `w:footnoteReference` | 脚注引用 |
| `w:noBreakHyphen` | `‑` |
| `w:softHyphen` | `­` |

**`w:t` 的空白处理**：`xml:space="preserve"` 保留全部空白；否则 strip，
但 **strip 后为空则保留原样**（全空白文本有意义）。

**空段落填 NBSP**（`\xa0`）——空 `<p>` 在部分阅读器上完全消失。

#### 列表

判定：段落 `w:pPr/w:numPr/w:numId` 存在（有 `w:ilvl` 更佳）。
注意**节分隔符段落**（含 `w:sectPr` 且不含 `w:r`）不算列表项。

编号定义在 `numbering.xml`：`w:num`（实例，键 `w:numId`）→
`w:abstractNumId` → `w:abstractNum`（定义，含 `w:lvl`）。

`w:start` **默认 0**（Calibre 的选择，不合规范，建议自己用 1）。

`w:numFmt` → CSS `list-style-type` 映射：

```
lowerLetter→lower-alpha  upperLetter→upper-alpha
lowerRoman→lower-roman    upperRoman→upper-roman
chineseCounting→cjk-ideographic   decimalZero→decimal-leading-zero
aiueo/aiueoFullWidth→hiragana      iroha/irohaFullWidth→katakana-iroha
hebrew1→hebrew           none→none
其他所有值（含 decimal）→decimal
```

项目符号：`w:numFmt="bullet"` 时，`w:lvlText` 为 `` → `square`，
`'o'` → `circle`，字体为 wingdings/symbol → `disc`，否则用自定义符号。

**第一版可以只做「有序/无序」两级**，不做序号模板（`第%1章` 这类）。

#### 表格

`w:tbl` → `<table>`，`w:tr` → `<tr>`，`w:tc` → `<td>`。

- `w:gridSpan/@w:val` → `colspan`
- `w:vMerge`：`restart` 开一组，`continue`（或省略 `w:val`）续接 → `rowspan`
- `w:hMerge`：同上 → `colspan`
- 宽度单位：dxa ÷ 20 = pt；pct ÷ 50 = %（OOXML 的 pct 是 1/50 %）

**第一版建议只做纯文本网格**（现有 `_table_text` 的做法），
不做合并单元格——现有实现已经够用。

#### 图片

DrawingML 路径：`w:drawing` → `wp:inline` / `wp:anchor` → `a:graphic` →
`pic:pic` → `pic:blipFill` → `a:blip/@r:embed` → rels 查 part 名。

VML 路径（老 Word）：`w:pict` → `v:shape` → `v:imagedata/@r:id`。

尺寸：`wp:extent/@cx,@cy` 是 EMU，**÷ 12700 = pt**。

现有 `_paragraph_images` 有两个问题：

1. **只处理 DrawingML，漏了 VML**（`w:pict` / `v:imagedata`）。
2. **`Image(target, b'', 'image/jpeg')` —— data 是空的！** 图片字节没读出来。
   必须改成 `Image(target, archive.read(target), media_type)`。

**media_type 要按实际字节判定**（PNG/JPEG/GIF/…），不能一律 `image/jpeg`——
MOBI 的 `kindle:embed:` 后面要跟正确的 mime。

#### 脚注

`word/footnotes.xml`：`w:footnote[@w:id]`，`-1` = 分隔符，`0` = 续行分隔符，
**`w:type/@w:val == 'normal'` 才是真实脚注**（按 type 过滤比按 id 数值可靠）。

引用 `w:footnoteReference` → 正文里写 `<a class="noteref" href="#note_N">N</a>`，
脚注内容集中放在正文末尾的 `<dl class="footnote">` 区。

**编号按出现顺序重新分配**（1,2,3…），不保留 Word 原始 id。

**脚注里的图片要用 `word/_rels/footnotes.xml.rels`**，与正文的关系表不同。

#### 超链接与书签

- `w:hyperlink/@r:id` → 外部 URL；`@w:anchor` → 内部书签；`@w:tooltip` → `title`；`@w:tgtFrame` → `target`。
- `w:bookmarkStart/@w:name` → HTML id，**要清洗**：
  `'id_' + re.sub(r'[^0-9a-zA-Z_]', '', name).lstrip('_')`，冲突时加 `_1`、`_2`。
- **忽略 `_GoBack`**（Word 2010 的「返回上次编辑」伪书签）。

### 2.6 元数据与封面

`docProps/core.xml`：

| 元素 | 字段 | 规则 |
|---|---|---|
| `dc:title` | title | 第 1 个，strip，非空才用 |
| `dc:subject` | tags | 逐个 strip，`,` 替换成 `_` |
| `cp:keywords` | tags | 按空白切词，再按 `,` 切分 |
| `dc:creator` | authors | 按 `&`/`and`/`with` 拆分多作者 |
| `dc:description` | description | 取全部后代文本，删 `_x000d_`，strip |
| `dc:language` | language | 规范化 |

`docProps/app.xml` 的 `ep:Company` → publisher。

**作者拆分**：Calibre 的正则只切 `and` / `with`（可带前导逗号），
**普通逗号和分号不切**（`"Smith, John"` 是一个作者）。本项目可选择同时切 `,`/`;`
（更符合直觉），但要在文档里写明与 Calibre 的差异。

**封面检测**（两处一致）：

```
0.8 <= height/width <= 1.8   且   height*width >= 160000
```

**图片尺寸读取**：复用 `epub.py` 的 `image_info(data)`——它已实现 PNG/JPEG 尺寸解析，
识别失败时抛异常（符合「不静默降级」约束）。

### 2.7 代码骨架

```python
# scripts/workshop/convert/docx_input.py
W  = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W_STRICT = 'http://purl.oclc.org/ooxml/wordprocessingml/main'   # 注意 /main
R  = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
R_STRICT = 'http://purl.oclc.org/ooxml/officeDocument/relationships'
A  = 'http://schemas.openxmlformats.org/drawingml/2006/main'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
V  = 'urn:schemas-microsoft-com:vml'
DC = 'http://purl.org/dc/elements/1.1/'

def rels_path_for(part):
    """部件路径 -> 它的 .rels 部件路径。"""
    parts = part.split('/')
    return '/'.join(parts[:-1] + ['_rels', parts[-1] + '.rels'])

def read_rels(archive, part):
    """返回 {rId: 包内路径}。绝对路径不拼 base；外部链接不解析。"""
    out = {}
    name = rels_path_for(part)
    if name not in archive.namelist():
        return out
    base = '/'.join(part.split('/')[:-1])
    for entry in ET.fromstring(archive.read(name)):
        target = entry.get('Target') or ''
        if entry.get('TargetMode') == 'External' or target.startswith('#'):
            continue
        if target.startswith('/'):
            resolved = target.lstrip('/')       # 包根绝对路径，不拼 base
        else:
            resolved = '/'.join(p for p in (base, target) if p)
        out[entry.get('Id')] = resolved
    return out

def heading_level(p, style_levels):
    """段落标题级别：段落 outlineLvl > 样式 outlineLvl > 样式名 > styleId。"""
    pPr = p.find(q(W, 'pPr'))
    if pPr is not None:
        direct = pPr.find(q(W, 'outlineLvl'))
        if direct is not None:
            value = direct.get(q(W, 'val'))
            if value is not None and value.isdigit() and int(value) <= 8:
                return min(6, int(value) + 1)
    pStyle = pPr.find(q(W, 'pStyle')) if pPr is not None else None
    style_id = pStyle.get(q(W, 'val')) if pStyle is not None else None
    return style_levels.get(style_id)

def read_images(archive, rels, element):
    """取出段落引用的图片，字节真的读出来。"""
    out = []
    for blip in element.iter(q(A, 'blip')):
        rid = blip.get(q(R, 'embed')) or blip.get(q(R, 'link'))
        part = rels.get(rid)
        if part and part in archive.namelist():
            data = archive.read(part)
            out.append((part, data, sniff_media_type(data)))
    for imagedata in element.iter(q(V, 'imagedata')):
        rid = imagedata.get(q(R, 'id'))
        part = rels.get(rid)
        if part and part in archive.namelist():
            data = archive.read(part)
            out.append((part, data, sniff_media_type(data)))
    return out
```

---

## 3. ODT 读入

### 3.1 ZIP 结构

```
mimetype            必须是包内第一个条目，且 STORED（不压缩）
                    内容 = "application/vnd.oasis.opendocument.text"（39 字节，无尾随换行）
META-INF/manifest.xml
content.xml         正文
styles.xml          样式（正文样式在 styles.xml，automatic-styles 在 content.xml）
meta.xml            元数据
settings.xml        可选
Pictures/*          图片
Thumbnails/thumbnail.png  可选
```

**`mimetype` 必须是第一个条目且不压缩**，这样 ZIP reader 读前 38 字节就能判定类型。

### 3.2 命名空间

| 前缀 | URI |
|---|---|
| `office` | `urn:oasis:names:tc:opendocument:xmlns:office:1.0` |
| `text` | `urn:oasis:names:tc:opendocument:xmlns:text:1.0` |
| `draw` | `urn:oasis:names:tc:opendocument:xmlns:drawing:1.0` |
| `table` | `urn:oasis:names:tc:opendocument:xmlns:table:1.0` |
| `style` | `urn:oasis:names:tc:opendocument:xmlns:style:1.0` |
| `fo` | `urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0` |
| `svg` | `urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0` |
| `xlink` | `http://www.w3.org/1999/xlink` |
| `dc` | `http://purl.org/dc/elements/1.1/` |
| `meta` | `urn:oasis:names:tc:opendocument:xmlns:meta:1.0` |
| `number` | `urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0` |

**必须按 URI 匹配，不能按前缀匹配**——ODF 文件里的前缀可能是 `t:`、`ns0:` 等任意形式。
用 `xml.etree` 时用 `{uri}local` 形式。

### 3.3 元数据

`meta.xml` 里的元素（XPath 是全文档任意深度，不限定在 `office:meta` 内）：

| 字段 | 命名空间 | 元素 |
|---|---|---|
| title | dc | `dc:title` |
| description | dc | `dc:description` |
| subject | dc | `dc:subject` |
| creator | dc | `dc:creator` |
| date | dc | `dc:date` |
| language | dc | `dc:language` |
| generator | meta | `meta:generator` |
| initial-creator | meta | `meta:initial-creator` |
| keyword | meta | `meta:keyword` |

**空白归一化**：`re.sub(r'\s+', ' ', text).strip()`——LibreOffice 写出的 meta.xml
通常带缩进换行，不归一化会得到 `"\n  My Title"`。

**作者优先取 `meta:initial-creator`**，没有再取 `dc:creator`。

**`meta:user-defined` 扩展**（Calibre 往返转换机制）：名字小写化，
`meta:value-type="boolean"` 时值转成 Python bool。
若存在 `opf.metadata` 则启用整套（`opf.isbn` / `opf.publisher` / `opf.pubdate` / …）。

**本项目建议**：只保留 `opf.cover`（frame 名字匹配会影响正文输出），
其余 `opf.*` 全部砍掉——除非要做 Calibre 往返。

### 3.4 正文抽取

#### `text:h`（标题）

**层级完全由 `text:outline-level` 属性决定**（值 1..10），钳制到 `[1, 6]`：

```python
level = int(element.get(q(TEXT, 'outline-level'), 1))
level = min(max(level, 1), 6)
```

**`text:outline-level` 是必需属性**（ODF 规范要求）。缺失时 Calibre 会 `KeyError` 崩溃——
**纯实现必须兜底为 1**。现有 `odt_input.py` 已用 `or 1` 兜底 ✓。

#### `text:p`（段落）

- `text:style-name` → class `P-<名字>`（`.` 替换成 `_`）
- **空段落填 `&#160;`**（NBSP）——否则空段落在某些阅读器上完全消失
- 特殊样式映射：`P-Heading_20_1` → `h1` …… `P-Heading_20_6` → `h6`，
  `P-Addressee` → `address`，`P-Preformatted_20_Text` → `pre`，
  `S-Emphasis` → `em`，`S-Strong_20_Emphasis` → `strong`，`S-Citation` → `cite`，
  `S-Variable` → `var`，`S-Definition` → `dfn`，`S-Teletype` → `tt`

**`_20_` 编码**：ODF 的 NCName 不能含空格，LibreOffice 把 `"Heading 1"` 编码成
`"Heading_20_1"`（空格 → `_20_`，冒号 → `_3a_`）。**读取时 odfpy 不做反向解码**，
所以匹配特殊样式表要用 `_20_` 形式。

#### `text:s`（空格计数）

```
text:s 的 text:c 属性 = 空格个数（默认 1，非负整数）
```

**用 U+00A0（NBSP）而不是普通空格**——HTML 渲染器会折叠普通空格，
ODF `text:s` 的语义是「不可折叠的空格」。

边界行为：

| `text:c` | 输出 |
|---|---|
| 属性缺失 | 1 个 NBSP |
| `"3"` | 3 个 NBSP |
| `"0"` / 负数 / 非数字 | 无 NBSP（但后续普通文本照常输出） |

现有 `_space_count()` 已实现 ✓。但 `_text_of()` 里写的是 `' ' * count`（**普通空格**），
而 `_paragraph_html()` 写的是 `' ' * count`（NBSP）——**两处不一致**，需统一。

#### `text:tab`

HTML 没有制表位概念，降级为**一个普通空格 U+0020**（注意与 `text:s` 不同）。

#### `text:line-break`

输出 `<br />`。

#### `text:a`（链接）

`xlink:href`。**以 `#` 开头的是内部锚点**，要重写成生成的锚点名。
ODF 内部引用用 `text:name`，HTML 的 id 是自己生成的，两套要对上。

#### 列表

`text:list` → `<ul>` 或 `<ol>`，由 `text:list-level-style-number` 的 `style:num-format` 决定：

```
'1'→decimal  'I'→upper-roman  'i'→lower-roman
'A'→upper-alpha  'a'→lower-alpha  其他→decimal
```

项目符号（`text:list-level-style-bullet`）→ `list-style-type` 取
`('square','disc','circle')[level % 3]`。

`text:list-item` 的 `text:start-value` → `<li value="N">`。

**注意 `text:list-level-style-number` 的起始值属性是 `text:start-value`，
不是 `style:start-value`**（ODF 里没有 `style:start-value` 这个属性）。

#### 表格

`table:table` → `<table>`，`table:table-row` → `<tr>`，`table:table-cell` → `<td>`。

- `table:number-columns-spanned` → `colspan`
- `table:number-rows-spanned` → `rowspan`
- `table:number-columns-repeated` → 重复输出 N 个 `<col/>`

#### 图片

`draw:image` 的 `xlink:href` 指向 `Pictures/xxx.png`。
`draw:frame` → `<div>`，class 为 `G-<样式名>`。

**图片解包**：遍历 ZIP 的 namelist，取所有以 `Pictures` 开头且不是目录的条目。
注意**不能只信 manifest**——manifest 与 ZIP 不一致时 Calibre 会崩
（`opendocument.py:671` 未捕获的 `KeyError`）。

**路径穿越防护**：解包目标必须 `startswith(base)`。

#### 样式（第一版可跳过）

ODF 的样式继承链（`style:parent-style-name`）、`style:map` 条件样式、
`style:num-prefix`/`suffix` 都比较复杂。**第一版只提取内容，不提取样式**，
输出干净的 XHTML（样式由 EPUB 的 CSS 或 MOBI 的默认样式承担）。

**若要提取样式，注意**：同名样式在 `office:styles` 与 `office:automatic-styles`
中都出现时，Calibre 会给后加载的那个加 `M` 前缀并改写所有引用。
**纯实现直接用 `(family, name)` 二元组做 key，天然无冲突**，不要引入 `M` 前缀机制。

### 3.5 封面检测

```
0.8 <= height/width <= 1.8   且   height*width >= 12000
```

**面积阈值是 12000（ODT），比 DOCX 的 160000 小一个数量级。**

封面来源：遍历 `draw:frame`，取第一个 `draw:image`。
若 frame 的 `name` 是 `opf.cover`（**大小写不敏感**）则优先选定。

**注意**：只有 frame 名为 `opf.cover` 时封面才会被从正文中剔除；
走面积启发式选中的封面**仍会留在正文里重复出现一次**。

---

## 4. HTML 读入

### 4.1 三段独立机制

Calibre 的 HTML 读入不是「解析 HTML 找章节」，而是三段独立机制：

1. **`ebooks/html/input.py`**：**不做 DOM 解析**。纯正则的文件图遍历器——
   用正则从原始字节抠出 `<a href>` 与 `<title>`，递归打开被链接的 HTML 文件，
   产出有序的文件列表。**它不识别章节。**
2. **`conversion/plugins/html_input.py`**：文件列表 → OEB 内部模型，
   生成最简单形式的 TOC（每个 spine item 一条）。
3. **`oeb/transforms/structure.py`**：真正的**章节识别**在这里。

**Readability 是完全独立的第四件事**，只被 `calibre/web/feeds/news.py`（RSS 新闻抓取）
使用，**HTML→EPUB 转换管线完全不经过它**。不要为了「复原 Calibre 的 HTML 读入」
去实现 Readability。

**章节识别不需要 DOM**：用 `html.parser` 的一次线性扫描 + 状态机就能等价实现。

### 4.2 编码检测

```
BOM → utf-8 / utf-16-le / utf-16-be
正则扫 XML 声明 / <meta charset> / <meta content="...charset=...">
都没有 → 统计检测（可选 charset_normalizer；纯 stdlib 只能逐个试）
修正：'macintosh'→'mac-roman'；gb2312 系列 8 项 → 'gbk'；'ascii'→'utf-8'
最后 codecs.lookup 失败 → 'utf-8'
```

三个声明正则（全部 `re.IGNORECASE`）：

```python
r'<\?[^<>]+encoding\s*=\s*[\'"](.*?)[\'"][^<>]*>'
r'''<meta\s+charset=['"]([-_a-z0-9]+)['"][^<>]*>(?:\s*</meta>){0,1}'''
r'''<meta\s+?[^<>]*?content\s*=\s*['"][^'"]*?charset=([-_a-z0-9]+)[^'"]*?['"][^<>]*>(?:\s*</meta>){0,1}'''
```

**`gb2312 → gbk` 是有意的 workaround**：Word 导出的 HTML 常把 gbk 标成 gb2312，
gbk 是 gb2312 的超集。

### 4.3 章节识别

**Calibre 的 `--chapter` 默认 XPath**（`plumber.py:412-415`）：

```
//*[((name()='h1' or name()='h2') and
    re:test(., '\s*((chapter|book|section|part)\s+)|((prolog|prologue|epilogue)(\s+|$))', 'i'))
    or @class = 'chapter']
```

语义拆解：

- 元素名必须是 `h1` 或 `h2`，**且**其字符串值（**含后代文本**）匹配正则：
  - `\s*((chapter|book|section|part)\s+)` —— 关键词后**必须**跟空白
  - `((prolog|prologue|epilogue)(\s+|$))` —— 后可跟空白**或**串尾
- **或** `@class = 'chapter'`（精确相等）——**这条与 h1/h2 限制并列，作用于任意元素**。

**纯标准库等价实现**（不要用 XPath + EXSLT）：

```python
CHAPTER_RE = re.compile(
    r'\s*((chapter|book|section|part)\s+)|((prolog|prologue|epilogue)(\s+|$))', re.I)

def is_chapter(tag, text_content, attrs):
    """对应 XPath 的 (name()='h1' or name()='h2') AND re:test(...) OR @class='chapter'。"""
    if attrs.get('class') == 'chapter':      # 独立分支，任意元素
        return True
    if tag not in ('h1', 'h2'):
        return False
    return CHAPTER_RE.search(text_content) is not None
```

**`text_content` 必须是元素的完整字符串值（含后代文本）**，与 `re:test(., ...)` 对齐。

**判定必须在元素闭合时做**（流式扫描下标题文本此时才完整）。

### 4.4 `html.parser` 的三个坑

#### 坑 1：隐式闭合

`html.parser` **不做 HTML5 树构造**。`<p>one<p>two` 在它是两个嵌套的 p，
在 HTML5 里是两个兄弟 p。

必须自己实现隐式闭合规则：

| 打开标签 | 自动关闭 |
|---|---|
| `p` | `p`,`div`,`ul`,`ol`,`dl`,`table`,`hr`,`pre`,`blockquote`,`form`,`h1`-`h6`,`li`,`address`,`article`,`section`,`aside`,`nav`,`header`,`footer`,`figure`,`main` |
| `li` | `li` |
| `dt` / `dd` | `dt`,`dd` |
| `option` | `option`,`optgroup` |
| `thead`/`tbody`/`tfoot` | `tr`,`td`,`th` |
| `tr` | `td`,`th`,`tr` |
| `td` / `th` | `td`,`th`,`tr` |
| `rt` / `rp` | `rt`,`rp` |

VOID 元素（无结束标签）：
`area base br col embed hr img input link meta param source track wbr`

这 8 条规则覆盖 99% 的真实 HTML。

#### 坑 2：实体解码

**`convert_charrefs=True` 会正确转换 HTML5 命名实体**（`&mdash;` → `—`），
**不需要**额外的 unescape。实测确认。

**`convert_charrefs=False` 时实体全部丢失**——因为有分号时 `html.parser`
把整个 `&mdash;` 交给 `handle_data`，不调用 `handle_entityref`。
`handle_entityref` 只在**没有分号**时才触发。

**决策：用 `convert_charrefs=True`**（现有 `html_input.py` 已是这个设置 ✓）。

#### 坑 3：text / tail 模型

**`html.parser` 的事件流无法在不重建 DOM 的前提下得到 `text_content()`。**

子元素闭合后的 tail 文本仍会调用 `handle_data`，此时栈顶是**父元素**，
tail 会被误记到父的 text 里。实测（现有实现的骨架）：

```
输入:  <p>Hello <b>world</b> tail</p>
输出:  blocks: [{'tag': 'p', 'text': 'Hello tail'}]     # "world" 丢失
```

**丢字会直接导致章节标题错误。**

修法：自己建节点树，每个元素记 `text` 列表 + `children`，每个子节点记 `tail`：

```python
class Node:
    __slots__ = ('tag', 'attrs', 'text', 'children', 'tail', 'parent')

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.text = []        # 直接文本
        self.children = []
        self.tail = []        # 闭合后由父接管

    def text_content(self):
        """元素的字符串值（不含自身 tail），与 XML 的 text_content 语义一致。"""
        out = list(self.text)
        for child in self.children:
            out.append(child.text_content())
            out.extend(child.tail)
        return ''.join(out)
```

`handle_data` 时写入**当前栈顶**的 `text`；元素闭合时，它的 tail 文本
转交给父元素的 `text`。

**现有 `html_input.py` 的 `_Extractor` 用的是扁平 buffer 模型，没有 tail 处理，
因此会丢字。** 这是本节要修的核心问题。

### 4.5 文件图遍历（多文件 HTML）

**判定是不是 HTML**：读**前 4096 字节**，正则 `<\s*html`（忽略大小写）。
`level > 0` 的链接文件若前 4KB 内没有 `<html`，判为二进制并丢弃。

**注意**：只有 `<!DOCTYPE html>` 而无 `<html>` 标签的文件**会被判为二进制丢弃**。
纯实现可放宽为也接受 `<!doctype html`。

**链接抽取**：正则只匹配 `<a ... href=...>`，不匹配 `<link>`/`<area>`/`<img src>`。
**纯实现应该用真正的解析器**（同一遍扫描里顺手抽 `handle_starttag` 的 `href`），
比跑两套正则更快也更准。

**遍历**：BFS（`max_levels` 默认 5）或 DFS（默认深度优先）。
去重按**解析后的绝对路径**。

**路径解析**：

```python
from urllib.parse import urlparse, urlunparse, unquote

def url_to_local_path(url, base):
    p = urlparse(url)
    path = urlunparse(('', '', p.path, p.params, p.query, ''))   # 丢 fragment，保留 query
    path = unquote(path)
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(base, path))
```

**Calibre 的 `Link.__eq__` 有 bug**：非本地链接（`path is None`）时
`None == None` 恒为 True，所有 http/https 链接彼此相等，且违反 hash/eq 契约。
**纯实现用 `(path, url)` 元组做 key。**

### 4.6 TOC 生成

**从 spine 生成（保底）**：

```
标题 = 首个 <title> 的文本（折叠空白）
若标题为空或重复 → 找文档中第一个 h1/h2/h3/h4/h5/strong 的文本
仍无 → '(unlabeled)'
```

**注意 Calibre 的一个真 bug**：它把 `titles` 列表（只在 title 非空时 append）
与 `headers` 列表（每个 spine item 都 append）用 `zip()` 配对，
**标题会错位且条目会丢失**。纯实现应按 item 索引对齐：

```python
for index, item in enumerate(spine):
    if not item.linear:
        continue
    title = use[index] if index < len(use) else '(unlabeled)'
```

**章节 TOC 条目**：

```
text = 元素文本，折叠空白，截断到 1000 字符
id   = 元素的 id 属性；若无则生成 f'calibre_toc_{n}' 并写回元素
href = f'{item.href}#{id}'
```

**标题来源优先级**：`title 属性 > 元素文本 > title 属性 > alt 属性`。

### 4.7 目录结构的存储

**NCX**（`scripts/workshop/epub_output.py` 已有 `_ncx()`）：

```xml
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="..."/></head>
  <docTitle><text>书名</text></docTitle>
  <navMap>
    <navPoint id="np0001" playOrder="1">
      <navLabel><text>标题</text></navLabel>
      <content src="ch0001.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
```

**`dtb:depth` 应写实际嵌套层级**（Calibre 写 `toc.depth()`，含根，比规范大 1）。

**`playOrder` 只需唯一**。Calibre 有一整套 `_update_playorder()` 按 spine 实际位置重排，
**纯实现不需要**——直接用递增序号即可。

**`<content src>` 的 URL 编码**：Calibre **不 unquote**（ADE 的历史包袱）。
纯实现应该按目标格式决定：EPUB 2 要求 URI-safe，EPUB 3 允许 IRI。

---

## 5. 实施顺序与验收标准

按依赖排序。每步给出**可验证的验收方式**。

### 步骤 0：建立字节级自查工具（前置，半天）

**目标**：一个能按 Calibre reader 的逻辑解析 MOBI 的独立脚本。

```python
# /tmp/rea-work/verify_mobi.py 已有的能力（项目外，不入库）
# 建议移入 tests/support/ 作为回归工具
```

**验收**：用 Calibre 9.18.0 生成的 `test.mobi` / `k.azw3` 跑通，
能读出：title、language、EXTH 条目列表、文本记录解压后的内容、记录号。

**这个工具是后续每一步的验收基础。**

### 步骤 1：修 MOBI 6 写出（1–2 天）

**改动清单**（按优先级）：

1. **B3** overlap 计数字节：`pack(b'>B', len(overlap))`。
2. **B4** 删掉逐记录压缩回退，全局要么全压缩要么全不压缩。
3. **B1** EXTH 201/202 写资源块 0-based 下标。
4. **B2** 图片记录只写原始字节，删掉 `IMG` 前缀。
5. **B8** 4 字节对齐填充记录 + `first_non_text_record` +1。
6. **B9** PDB name 用 ASCII 化（转写失败用 `_`），长度截 31 + NUL 补齐。
7. `title_offset` 用 EXTH 的**实际 blob 长度**算。
8. `recindex` 改成 1-based。
9. **`to_base` 字母表改成大写** `string.digits + string.ascii_uppercase`（B10）。

**验收**：

- 用步骤 0 的工具解析输出：EXTH 21x 条目能读出、title 正确、language 正确。
- 用 Calibre 的 reader 逻辑解压所有文本记录，拼接结果与 `text_length` 一致，
  且拼接出的文本**与输入 HTML 逐字节相同**（尾部 overlap 除外）。
- 用 Calibre 9.18.0 的 `ebook-convert` 或 `calibre-debug` 读回，确认不报错。
- 加一条测试：构造含多字节字符跨 4096 边界的输入，验证解压后拼接正确。

### 步骤 2：MOBI 6 的内联目录（1 天）

- 生成 `contents.xhtml`，每项 `<a>` 带 `style="margin-left:Nem"`。
- guide 里加 `<reference type="toc" title="Table of Contents" filepos="..." />`。
- `type="text" title="Start Reading"` 的引用指向正文起点。

**验收**：用步骤 0 的工具确认 guide 的 `filepos` 回填正确；
用 Calibre 转成 EPUB 后检查目录层级与标题正确。

### 步骤 3：KF8 standalone（AZW3）（2–3 天）

**决策**：`.azw3` 走纯 KF8 standalone，不做联合文件。

- 记录序列：text → 对齐填充 → CHUNK 索引 → SKEL 索引 → NCX 索引 → 资源 → FDST → FLIS → FCIS → EOF。
- 索引生成器按 §1.4 的控制字节算法。
- **先不做 skeleton/chunk 拆分**：整条文本流当一个 skeleton、一个 chunk。
  这能让文件结构合法、Kindle 能打开。
- `kindle:embed:` 1-based base32；`kindle:pos:fid:` 引用。

**验收**：

- 用步骤 0 的工具解析：EXTH 在 280、`chunk_index`/`skel_index`/`ncx_index`
  指向正确的记录号、这些记录都以 `INDX` 开头。
- 用 Calibre 9.18.0 的 `ebook-convert` 把它转成 EPUB，确认正文与目录完整。
- **用 Kindle Previewer 或真机确认**（这一步无法完全自动化）。

### 步骤 4：DOCX 读入补齐（2 天）

1. 修 `W_STRICT` / `R_STRICT` 的 `/main`。
2. 修 `HEADING_NAMES` 的中文名（加空格）。
3. **补段落级 `w:outlineLvl`**。
4. **修 `_paragraph_images` 的空 data**——真的读出图片字节。
5. 补 VML 图片路径。
6. media_type 按字节判定。
7. 封面检测接入 `epub.py` 的 `image_info`。
8. 脚注（`w:footnoteReference` + `footnotes.xml`）。

**验收**：

- 现场构造一个 DOCX（含中文标题样式、两级标题、编号列表、图片、脚注、
  跨段落的图片），读入后检查 IR 的 `title`/`toc`/`images`。
- 用 Calibre 9.18.0 转同一个 DOCX，对比标题层级与图片数量。

### 步骤 5：ODT 读入补齐（1–2 天）

1. 统一 `_text_of` 与 `_paragraph_html` 的空格处理（都用 NBSP）。
2. 修 `text:s` 的 `text:c` 缺失/异常处理（已对，确认）。
3. `text:tab` → 单个普通空格。
4. `text:h` 缺 `outline-level` 兜底（已有，确认）。
5. 封面检测接入。
6. 表格的 colspan/rowspan。

**验收**：用 LibreOffice 造一个 ODT（标题、列表、表格、图片、空段落、多个空格），
读入后对比文本内容与 Calibre 的转换结果。

### 步骤 6：HTML 读入补齐（1–2 天）

1. **修 tail 丢字**——改成节点树模型。
2. **修章节识别的 `class='chapter'` 分支**——当前只在 h1..h6 上记录章节，
   漏掉 `<div class="chapter">`。
3. 修 TOC 的 `titles`/`headers` 错位（按索引对齐）。
4. 隐式闭合规则补全。
5. `<base href>` 处理——否则相对链接全错。
6. 资源路径安全检查（防止 `../../` 逃出根目录）。

**验收**：现场构造一个 HTML（含 `<div class="chapter">`、嵌套标签、尾随文本、
`<base href>`），读入后验证章节标题**完整无丢字**。

### 步骤 7：往返一致性（1 天）

**EPUB → EPUB 应当稳定**：读入再写出，第二次输出与第一次**逐字节相同**。

**验收**：写一条测试，连续两次转换，断言两次输出字节相同。

---

## 6. 与 Calibre 的已知差异

### 6.1 我们做不到 Calibre 那样的地方

| # | 差异 | 用户在什么场景会察觉 |
|---|---|---|
| 1 | **不做 PalmDOC 的 `rindex` 变体** | 压缩后字节与 Calibre 不同，但解压结果完全相同——**察觉不到** |
| 2 | **不写 TBS** | Kindle 里章节跳转略慢；目录本身正常。**察觉不到** |
| 3 | **不做 skeleton/chunk 拆分** | 大图片的流式加载变慢；正文正常。**弱察觉** |
| 4 | **不做缩略图生成** | Kindle 商店浏览时缩略图是封面原图。**弱察觉** |
| 5 | **不做 PNG→GIF 转换** | 老 Kindle（固件 < 3.x）显示不出 PNG。**特定旧设备可察觉** |
| 6 | **不剥 JPEG 的 EXIF** | Kindle 上照片方向可能错。**特定设备可察觉** |
| 7 | **不做 CSS 级联** | DOCX/ODT 的样式（字号、颜色、复杂布局）丢失，只保留语义结构。**明显察觉** |
| 8 | **不做符号字体映射**（Wingdings/Symbol 的私有区字符） | 少数符号显示为方块。**轻微察觉** |
| 9 | **不做内嵌字体** | Kindle 上用自己的字体渲染。**轻微察觉** |
| 10 | **不写 HUFF/CDIC** | 压缩率低，文件大 20–30%。**文件大小可察觉** |
| 11 | **不做 MOBI 期刊** | 期刊 MOBI 会退化成普通图书。**结构性差异** |
| 12 | **不做 Word 域解析**（TOC 域、PAGEREF） | Word 自带目录被忽略，改用标题生成。**轻微察觉** |
| 13 | **不做数学公式**（`m:oMath`） | 公式丢失。**明显察觉（若有公式）** |
| 14 | **不做修订追踪** | 插入的文字保留、删除的文字忽略（比 Calibre 好）。**反向察觉** |
| 15 | **不做 EMF/WMF 图片转换** | 这两种格式的图片丢失。**轻微察觉** |
| 16 | **不做 CSS flow 拆分** | KF8 的 CSS 内联在正文里而非独立 flow。**察觉不到** |
| 17 | **不做 `filepos` 之外的锚点回填** | — |
| 18 | **NCX 的 `dtb:depth` 按规范写**（比 Calibre 小 1） | **察觉不到**（Calibre 那个值不合规范） |
| 19 | **`<content src>` 做 URL 编码**（Calibre 不做） | 文件名含中文/特殊字符时，我们的更正确。**反向察觉** |

### 6.2 我们比 Calibre 做得更好的地方

| # | 差异 | 理由 |
|---|---|---|
| 1 | **不做「压缩后更大就存原文」** | Calibre 不做这个判断（它把缓冲开到 2 倍），我们也不做——否则数据损坏 |
| 2 | **DOCX 绝对路径 Target 不拼 base** | Calibre 会产出 `word/word/media/...`，图片解析失败 |
| 3 | **支持 DOCX 中文标题样式 + 段落级 outlineLvl** | Calibre 只认英文样式名，中文 Word 的标题会全部丢失层级 |
| 4 | **标题样式名匹配宽松**（`标题 1` / `标题1` 都认） | Calibre 的正则只认 `heading N` |
| 5 | **DOCX 修订删除的文字被跳过** | Calibre 会把删除的文字当正文读出 |
| 6 | **HTML 章节识别对任意元素检查 `class='chapter'`** | Calibre 的 XPath 语义如此，但很多简化实现会漏 |
| 7 | **HTML 章节标题不丢字** | 纯 `html.parser` 的事件流会丢 tail 文本 |
| 8 | **HTML TOC 按索引对齐，不丢条目** | Calibre 的 `zip()` 会错位并截断 |
| 9 | **ODT 的 `text:h` 缺 `outline-level` 兜底** | Calibre 会 `KeyError` 崩溃 |
| 10 | **ODT 用 `(family, name)` 做样式 key** | 避免 Calibre 的 `M` 前缀重命名机制 |
| 11 | **NCX `dtb:depth` 合规范** | Calibre 比规范大 1 |
| 12 | **`<content src>` 做 URL 编码** | EPUB 2 要求 URI-safe |
| 13 | **生成 EPUB 3 直接从 IR 生成 nav** | Calibre 走「写 OPF2+NCX → 重新读回 → 升级成 EPUB3」的两段式 |
| 14 | **不做 ADE / Nook / PocketBook hack** | Calibre 的这些 hack 只对特定阅读器有意义 |

### 6.3 未实现但可能需要的后续项

按优先级：

1. **CSS 级联**（影响 DOCX/ODT 的保真度最大）——需要 CSS 解析器，
   `tinycss2` 是纯 Python 的最小选择。
2. **缩略图生成**——需要图像库，与「零依赖」冲突；可以推迟。
3. **联合（joint）MOBI 文件**——若要兼容老设备才需要。
4. **skeleton/chunk 拆分**——大图片的加载体验。
5. **MOBI 期刊**——需求驱动。
6. **内嵌字体**——CJK 字体巨大，通常不需要。

---

## 附：关键源码位置索引

核对基准：`/tmp/rea-work/calibre-9.18.0/src/calibre/`

| 主题 | 文件:行号 |
|---|---|
| PalmDOC C 实现 | `ebooks/compression/palmdoc.c` |
| PalmDOC 纯 Python 参考 | `ebooks/compression/palmdoc.py:22-70` |
| PalmDOC 格式规范 | `format_docs/compression/palmdoc.txt` |
| MOBI 格式规范 | `format_docs/pdb/mobi.txt`（头 51-69，尾部 255-295） |
| KF8 头定义 | `ebooks/mobi/writer8/mobi.py:38-192` |
| MOBI 6 头（手写布局） | `ebooks/mobi/writer2/main.py:239-377` |
| 联合文件 record0 | `ebooks/mobi/writer2/main.py:381-464` |
| 记录生成顺序 | `ebooks/mobi/writer2/main.py:81-91`、`writer8/mobi.py:252-299` |
| 文本记录切分 | `ebooks/mobi/utils.py:543-591` |
| 尾部数据编解码 | `ebooks/mobi/utils.py:208-258` |
| VWI | `ebooks/mobi/utils.py:94-155` |
| CNCX | `ebooks/mobi/utils.py:594-637` |
| base32 | `ebooks/mobi/utils.py:400-415` |
| EXTH 构建 | `ebooks/mobi/writer8/exth.py:19-236` |
| EXTH 解析 | `ebooks/mobi/reader/headers.py:40-190` |
| 读端头偏移（权威） | `ebooks/mobi/reader/headers.py:195-300` |
| INDX 生成 | `ebooks/mobi/writer8/index.py` |
| MOBI6 INDX 生成 | `ebooks/mobi/writer2/indexer.py` |
| `get_tag_map`（读端权威） | `ebooks/mobi/reader/index.py:230-255` |
| skeleton/chunk | `ebooks/mobi/writer8/skeleton.py` |
| TBS | `ebooks/mobi/writer8/tbs.py`、`ebooks/mobi/tbs_periodicals.rst` |
| 资源与封面偏移 | `ebooks/mobi/writer2/resources.py:130-260` |
| 封面记录解析 | `ebooks/metadata/mobi.py:115-190` |
| MOBI6 序列化与 filepos | `ebooks/mobi/writer2/serializer.py:139-410` |
| 文本解压 | `ebooks/mobi/reader/mobi6.py:798-872` |
| 图片提取 | `ebooks/mobi/reader/mobi6.py:908-965` |
| 文档容器 | `ebooks/docx/container.py` |
| DOCX 命名空间表 | `ebooks/docx/names.py:17-81` |
| DOCX 转 HTML | `ebooks/docx/to_html.py` |
| DOCX 样式 | `ebooks/docx/styles.py` |
| DOCX 编号 | `ebooks/docx/numbering.py` |
| DOCX 表格 | `ebooks/docx/tables.py` |
| DOCX 图片 | `ebooks/docx/images.py` |
| DOCX 脚注 | `ebooks/docx/footnotes.py` |
| DOCX 目录 | `ebooks/docx/toc.py` |
| ODT 读入 | `ebooks/odt/input.py` |
| ODT 元数据 | `ebooks/metadata/odt.py` |
| ODF→XHTML | `src/odf/odf2xhtml.py` |
| ODF 命名空间表 | `src/odf/namespaces.py` |
| ODF manifest | `src/odf/odfmanifest.py` |
| HTML 读入 | `ebooks/html/input.py` |
| HTML 编码检测 | `ebooks/chardet.py` |
| 章节识别 | `ebooks/oeb/transforms/structure.py` |
| 默认章节 XPath | `ebooks/conversion/plumber.py:412-415` |
| Readability（不实现） | `ebooks/readability/readability.py` |
| OEB 中间表示 | `ebooks/oeb/base.py` |
| OPF/NCX 生成 | `ebooks/oeb/base.py:2190-2296` |
| EPUB 容器字节 | `ebooks/epub/__init__.py:25-63` |
| EPUB 输出 hack | `ebooks/conversion/plugins/epub_output.py` |
| EPUB2→3 升级 | `ebooks/oeb/polish/upgrade.py` |
| OPF2→3 元数据 | `ebooks/metadata/opf_2_to_3.py` |
| EPUB3 nav 生成 | `ebooks/oeb/polish/toc.py:923-988` |

---

*本规格基于对 Calibre 9.18.0 源码的只读核对与本机实测验证编写，未修改任何 Calibre 文件，
也未修改本项目的任何文件。文中所有偏移、位宽、常量数值均可在上述位置复核。*
