"""默认样式表：写出的 EPUB 都引用这一份。

以前它同时存在于两个模块里，两份内容已经漂移
（一份有封面与标题页规则，另一份只剩三行）。样式表是用户直接看到的东西，
只能有一份。
"""

CSS = '''body{font-family:serif;line-height:1.8;margin:0 5%;overflow-wrap:break-word}
h1{font-size:1.5em;font-weight:normal;line-height:1.6;margin:2em 0 1.5em;text-align:center;break-after:avoid}
p{text-indent:2em;margin:.5em 0;orphans:2;widows:2}nav ol{list-style:none;padding-left:1.2em}
nav li{margin:.35em 0}a{color:inherit}.cover img{display:block;max-width:100%;max-height:95vh;width:auto;height:auto;margin:auto}
.title{text-align:center;margin-top:20%}.title p{text-indent:0}.notice p{text-indent:0}
'''
