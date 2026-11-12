# 注释体检与规范化

电子书的注释有多种做法，有的本来就能点，有的只是正文里的纯文本。
本文件说明这两个命令各自做什么、怎么判断一条注释是哪一种、以及哪些情况不动手。

## 两个命令

```sh
python3 scripts/ebook.py notes 原书.epub                    # 体检：只报告，不改文件
python3 scripts/ebook.py notes-fix 原书.epub --output 新书.epub   # 规范化：输出新文件
```

`notes` 永远不写任何文件。`notes-fix` 不改原书，输出已存在时报错退出。

## 注释有哪几种

同一本书里也可能混着几种。命令按**引用侧**的形状判断：

| 形态 | 正文里的样子 | 注释正文 | 能不能点 |
| --- | --- | --- | --- |
| `noteref` | `<a epub:type="noteref" href="#fn-1">` | `<aside epub:type="footnote" id="fn-1">` | 能，且已是 EPUB 3 规范 |
| `anchored` | `<sup><a href="#note-3">[3]</a></sup>` | 带 `id` 的段落或 `<aside>` | 能，但没标语义 |
| `plain` | `<sup>①</sup>`（圈码或 `[N]`，纯文本） | 段落间的 `<ul><li>` 或 `<aside>` | **不能** |

`plain` 是唯一需要动手的：`anchored` 只要补上语义标记，`noteref` 已经合规。

## 体检报告里有什么

```json
{
  "file": "原书.epub",
  "documents": 24,
  "references": {"total": 76, "linked": 0, "plain": 76},
  "notes": {"total": 76, "semantic": 0, "plain": 76},
  "forms": {"noteref": 0, "anchored": 0, "plain": 76},
  "pairings": {"high": 71, "medium": 0, "low": 6},
  "broken_links": [{"document": "text/part0003.html", "text": "①", "href": "#fn-1",
                    "reason": "锚点不存在"}],
  "self_referential": [],
  "orphan_notes": [],
  "unmarked_notes": []
}
```

- `broken_links`：注释引用指向的文件或锚点不存在。这是**最要紧的一项**——书能打开，
  但点注释跳不过去或跳错地方。
- `pairings`：圈码配对的置信度分布。`high` 可自动改，`medium`／`low` 留给人判断。
- `self_referential`：链接指向自己所在的那个标记，跳过去没意义（底本常见缺陷）。
- `orphan_notes`：注释正文没有任何引用指向它。
- `unmarked_notes`：注释正文没有 `epub:type="footnote"`，阅读器不认它是脚注。

报告只给位置和引用标记，不展示注释正文，不剧透。

## 规范化做了什么

1. **`plain` 引用变成可跳转**：把圈码包进 `<a epub:type="noteref" href="#...">`，
   圈码文字本身**一个字不改**；注释正文从 `<li>` 换成 `<aside epub:type="footnote" id="...">`，
   内容同样不改，原来的 `<ul>` 空壳删掉。
2. **`anchored` 补语义标记**：给引用加 `epub:type="noteref"`，给注释正文加
   `epub:type="footnote"`。**直接给原元素加属性**，不新增容器、不改 `id` 和 `href`，
   所以已有的链接不会断。
3. **`noteref` 不动**：已经合规的只核对，不重写。

正文文字一字未改，改的只是标记与容器。

## 圈码注释怎么配对

`plain` 的圈码在正文里，注释正文在段落之间，两者之间没有直接对应关系。配对规则：

> 一个注释块（`<ul>`）覆盖**自上一个注释块以来**出现的全部圈码引用，按出现顺序一一对应。

对不上的按顺序比：圈码序列一致才算**高置信**，可以放心改；数量一致但圈码对不上，
说明**底本自己写错了编号**，标成**中置信**，报告里列出来但**不改**；
数量都对不上则是**低置信**，同样不改。

宁可少改，也不要把注释链到错的段落上——链错比不链更难发现。

> 注意：书里同一段可能被 PDF 转换切成多个 `<p>`，所以注释对应的段落**不一定是紧挨着的那个**。
> 按位置邻接配对会错，必须按上面那条区间规则。

## 哪些情况不动手

- 圈码序列对不上（中置信、低置信）——底本编号有错，报告出来让人判断。
- 注释正文是列表但**没有圈码前缀**——那是译本对照之类的正文列表，不是注释。
- 引用指向的文件或锚点不存在——是断链，得先决定注释正文在哪儿，补不出来。
- 已经是 `noteref` 的引用——只核对。

## 与其它命令的关系

- `notes` 与 `validate` 不同：`validate` 查的是书内文件与链接能不能解析，
  `notes` 查的是**注释**这一类东西有没有接好。
- `notes-fix` 只加属性、不换文档结构，**不升级 EPUB 版本**：EPUB 2 的书跑完还是
  EPUB 2，`epub:type` 在 EPUB 2 里是未知命名空间的属性，阅读器会忽略但不报错。
  想拿到 EPUB 3 语义脚注的完整效果，先跑 `convert` 升级，或像 `layout` 那样处理。
- `notes-fix` 生成的 `aside` 带 `epub:type="footnote"`，`layout --preset cn-novel`
  的样式表也认 `[role="doc-footnote"]` 这类语义标记，但 **`cn-novel.css` 的选择器带
  `body.workshop-cn-novel` 前缀**，所以要先跑 `layout` 才会生效。

## 局限

- 只认上面三种引用形态。别的做法（比如用图片当脚注标记）查不出来，报告里不会提；
  带 `epub:type="noteref"` 的图片型引用是例外，会单独认出来。
- 圈码只认 `①`–`⑳` 这 20 个字符和 `[数字]`。裸 `*` 只在**确实带链接**时才算
  注释标记——它在正文里常作排印分隔符，无条件认会误报一大片。
- 报告里的「注释正文数」按 DOM 结构清点（`<aside>`、圈码 `<li>`、带注释 class 的段落），
  不从链接反推：引用与回链长得一样，反推会把正文侧的锚点也算进来。
- `notes-fix` 不改圈码错标、不补断链。这两类列在报告里，得先判断注释正文在哪儿。
- 规范化不升级 EPUB 版本，见上节。
