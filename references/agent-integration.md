# 如何在 AI 助手中使用

这个 skill 由操作说明和处理工具组成，不限定某个 AI 平台。导入时保留整个文件夹，方便助手读取说明并找到工具。

## 最通用的方法

下载并解压项目，让助手打开这个文件夹，然后说：

> 请先读取 SKILL.md，再帮我整理提供的电子书。保留原文件，默认不要剧透，输出使用标准书名。

把电子书位置、想用的封面、阅读器型号和传书方式一起告诉它。如果助手不能直接处理电脑里的文件，可以让它给出操作步骤，由你执行。

## WorkBuddy

通过技能板块的“添加技能 → 上传技能”导入，按当前界面选择本地技能包。

[官方说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)

## Qoder 系列

通过所用产品的 Skills 功能导入，或直接让助手读取项目文件。不同产品的入口可能不同：

- [Qoder](https://docs.qoder.com/qoder/skills)
- [QoderWork](https://docs.qoder.com/qoderwork/skills)
- [Qoder CLI](https://docs.qoder.com/cli/Skills)

## 豆包及其他对话助手

提供 `SKILL.md` 和需要整理的文件。能直接运行处理工具的环境可以完成操作；只有聊天功能时，可以让助手给出电脑上可执行的命令。它只能处理当前环境能够读取的文件。

## Codex 示例

把整个文件夹放到 `~/.codex/skills/ebook-workshop`（已有同名目录时先检查），随后可以这样调用：

> 使用 $ebook-workshop 将这本 TXT 整理成带封面的 EPUB。

也可以不安装，直接让 Codex 读取项目中的 `SKILL.md`。自定义过技能目录的用户使用自己的位置。

`agents/openai.yaml` 是给 Codex 使用的可选显示文件，其他助手可以忽略。
