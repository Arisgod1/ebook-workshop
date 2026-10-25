# Ebook Workshop · 电子书整理 Skill

一个面向 Codex 的电子书整理 skill，也附带可单独运行的 Python 命令行工具。

它处理**你已有的 TXT / EPUB**：检查章节、比较版本、整理中文 EPUB、迁移原图封面，以及为 Kindle 邮件传书准备估算页码。默认无剧透，原文件不覆盖。

## 能做什么

- 检查编码、章节乱号、疑似空章、重复段落、元数据和现有页码。
- TXT → EPUB 3，保留前置文字、全部段落、作者留言与后记。
- 从另一份 EPUB 迁移封面原图，更新封面页及导航。
- 添加 EPUB 3 页码导航和 NCX 兼容页码表，方便尝试邮件 / Send to Kindle。
- 检查资源、锚点和正文保真；可调用外部官方 EPUBCheck。

**章节齐备、逐字核对、格式合格、Kindle 实测是四件不同的事。** 工具不会把前20章抽查说成整本精校，也不会承诺邮件转换必然保留页码。估算页码不等于纸书页码或屏幕翻页次数。

## 安装为 Codex Skill

将整个仓库目录放到你的 Codex skills 目录，目录名保持 `ebook-workshop`。例如先克隆仓库，再把仓库复制或链接到 `~/.codex/skills/ebook-workshop`（使用自定义 `CODEX_HOME` 时调整位置）。如果同名目录已存在，先检查内容，避免覆盖。

在 Codex 中调用：

> 使用 $ebook-workshop 检查这两份电子书的正文与番外，不要剧透；选择适合的版本整理成带封面的 EPUB。我要通过邮件传到 Kindle，输出文件就叫标准书名。

核心入口是 [SKILL.md](SKILL.md)，具体操作见 [命令参考](references/commands.md) 和 [Kindle 页码说明](references/kindle-pages.md)。默认支持自动发现，无需只靠显式调用。

## 独立使用

Python 3.9+，无第三方 Python 依赖：

```bash
python3 scripts/ebook.py inspect '/path/书名.txt'
python3 scripts/ebook.py build '/path/书名.txt' \
  --title '书名' --author '作者' --cover '/path/封面.png' \
  --output '/path/work/书名.epub'
python3 scripts/ebook.py paginate '/path/work/书名.epub' \
  --chars 500 --output '/path/delivery/书名.epub'
python3 scripts/ebook.py validate '/path/delivery/书名.epub'
```

脚本不会搜索或下载书籍，不联网、不发邮件、不上传用户文件，不移除 DRM。已有电子书格式的转换可以使用本机 Calibre；本仓库不捆绑 Calibre、Kindle Previewer 或 EPUBCheck。

默认支持数字章号“第12章 标题”和“12|标题”；特殊规则用 `--pattern`。乱号只提示，不猜改。封面与分页修改助手目前支持 EPUB 3；EPUB 2 先规范转换。详情和限制见命令参考。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/ebook.py validate '/path/书名.epub' \
  --epubcheck '/path/epubcheck.jar'
```

测试全部使用程序自造文本与最小图片，验证段落保真、显式标题修正、封面字节保留、页码锚点、拒绝覆盖及错误输入。仓库不附带任何小说、下载资源或第三方封面。

许可：[MIT](LICENSE)。许可仅适用于本仓库代码和文档，不授予任何书籍、插画或字体的使用权。
