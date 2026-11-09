# Kindle 使用说明

告诉助手你的 Kindle 型号，以及准备通过邮件还是 USB 传书，它就可以选择对应的处理方式。

## 通过邮件传书

使用 EPUB 文件作为附件，发送到你的 Kindle 邮箱。助手可以提前给书添加估算页码；邮件转换后能否显示，需要在 Kindle 上确认。

收到并下载电子书后，打开阅读设置中的“阅读进度”，选择“书中页码”。菜单名称可能随设备版本略有不同。如果这一项不可用，说明设备收到的版本没有提供可识别的页码，可以再换一种处理方式。

## 通过 USB 传书

对支持 AZW3 的 Kindle，用 `kindle --method usb` 生成 AZW3（原生完成，不需要 Calibre）。把文件加入 Calibre 再用“发送到设备”传书，或直接用 Send to Kindle。需要页码时，在 Calibre 的 Kindle 设备设置中开启页码生成，然后在 Kindle 上确认是否显示。

## 页码怎么看

- **纸书页码**：对应某个实体版本的具体页面。
- **估算页码**：按文字内容划分，用来记录阅读进度。
- **屏幕翻页**：会随字号、行距和屏幕大小改变。

本项目可以添加估算页码，默认以每页约500字为目标，在段落之间划分，也可以按需要调整。字号变大后，翻几屏才增加一个页码是正常现象。

已经有正确页码的书，不需要重新生成。正文、番外和附录是否全部计入页数，可以按你的偏好选择。

需要自己操作时，见 [命令参考](commands.md)。

## 相关资料

- [亚马逊：邮件传书与支持格式](https://digprjsurvey.amazon.co.uk/csad/help/node/G5WYD9SAF7PGXRNA)
- [亚马逊：电子书页码说明](https://kdp.amazon.com/en_US/help/topic/GH4DRT75GWWAGBTU)
- [Paperwhite 使用指南](https://d1ergij2b6wmg5.cloudfront.net/kug/kindle_paperwhite_12th/v1/en-GB/html/kug.html)
- [EPUB 页码工具说明](https://github.com/matthewbstanton/calibre-epub3-pagelist)
