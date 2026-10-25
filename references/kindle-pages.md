# Kindle 页码与传输路线

核实日期：2026-10-25。亚马逊服务和固件可能变化，针对当次机型与传输方法核实当前资料；本说明不把某次成功报告提升为全机型保证。

## 三种“页”

1. **纸书对应页码**：与某个实体版本逐页映射。需要实际页面边界；拿到 ISBN 或纸书总页数，不等于知道每页边界。不能把估算分页冒充纸书分页。
2. **估算参考页码**：按内容划分的稳定位置标记。字号变化时页码仍可保持，翻几屏才增加一页属正常。
3. **屏幕页数**：取决于屏幕、字体、字号、行距和边距。不能把前两种页码宣传成“每翻一屏必加一页”。

亚马逊出版指南的 Real Page Numbers 说明针对出版流程，不能仅凭它就保证个人邮件文档支持相同功能。

## 邮件 / Send to Kindle

发送 EPUB 附件，由云端转换。Send to Kindle 的支持格式列表没有 KFX、AZW3、APNX；不要把 USB 方案的文件组合推荐为邮件方案。

本仓库的 `paginate` 在 EPUB 3 内添加：

- 空白 `epub:type="pagebreak"`、`role="doc-pagebreak"` 标记；
- 隐藏的 EPUB 导航 `nav epub:type="page-list"`；
- 输入已有 NCX 时增加 NCX `pageList` 兼容信息。

有工具作者报告类似方式可经 Send to Kindle 显示页码，但亚马逊转换或设备固件可能忽略它。结构检查/EPUBCheck通过，只说明文件验证情况。真正的设备验证必须经过用户使用其账号投递、云端转换并在该设备打开。未经用户要求不发送邮件、不上传文件。

默认500非空白字符一页是中文内容的可调估算约定，不是亚马逊标准；也不照搬英文工具常见的1,850字符页长。需要其他密度时按用户选择调整，并报告计算口径与参与分页的内容范围。

指导用户下载新文件后，在阅读界面 Aa → 更多／阅读设置 → 阅读进度中选择“书中页码”（具体菜单随固件变化）。若不可用，说明设备收到的转换结果未提供可识别的页码；不能保证单纯改 EPUB 元数据或邮件主题能解决。云端重复文档和旧下载可能影响核对，先区分新旧文件再判断。

## USB：仅在用户选择本地传输时

- 支持 KFX 的设备：Calibre + KFX Output + Kindle Previewer，可启用 `Create approximate page numbers`。需要确认当前工具与机型兼容，不能仅换后缀。
- AZW3 / APNX：Calibre 设备插件可以在 USB 发送时生成页码配套信息。页码与书籍内容位置绑定，修改正文后需要重新生成；固件兼容性仍需实测。

如用户坚持邮件方式，优先保持 EPUB 并明确云端验证边界，而不是不断建议 USB。

## 维护来源

- [Amazon：Send to Kindle 支持格式和邮件方式](https://digprjsurvey.amazon.co.uk/csad/help/node/G5WYD9SAF7PGXRNA)
- [Amazon：流式书籍与 Real Page Numbers](https://kdp.amazon.com/en_US/help/topic/GH4DRT75GWWAGBTU)
- [Paperwhite 官方用户指南](https://d1ergij2b6wmg5.cloudfront.net/kug/kindle_paperwhite_12th/v1/en-GB/html/kug.html)
- [EPUB3 Page List Generator 作者的说明](https://github.com/matthewbstanton/calibre-epub3-pagelist)
- [Calibre Kindle 驱动的页码设置](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/devices/kindle/driver.py)
- [KFX Output 插件作者对估算页码设置的解释](https://www.mobileread.com/forums/showthread.php?t=344200)

本仓库是独立实现，没有安装或捆绑上述插件。代码中的500字符约定、实现范围和已有页码拒绝覆盖策略以本仓库文档为准。
