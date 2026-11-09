"""ZIP 包的安全打开。

DOCX / ODT 都是 ZIP，压缩比可以做到上千倍：几百 KB 的包解出几十 GB，
足以把工具拖死。这里在解压前先看声明的解压后总大小，超限直接拒绝。
"""

import zipfile

# 解压后总大小上限。电子书远达不到这个量级，超过就是异常文件。
MAX_UNCOMPRESSED = 1024 ** 3          # 1 GiB

# 单条目的解压后大小上限。
MAX_ENTRY = 256 * 1024 ** 2           # 256 MiB


class ArchiveError(ValueError):
    """压缩包不安全或无法读取。"""


class Archive:
    """一个已经过安全检查的 ZIP 包。"""

    def __init__(self, path):
        try:
            self.handle = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise ArchiveError('Not a readable ZIP file: %s' % exc) from exc
        infos = self.handle.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            self.handle.close()
            raise ArchiveError('Duplicate paths in archive')
        for info in infos:
            name = info.filename
            if name.startswith('/') or '..' in name.split('/') or '\\' in name:
                self.handle.close()
                raise ArchiveError('Unsafe path in archive: ' + name)
            if info.file_size > MAX_ENTRY:
                self.handle.close()
                raise ArchiveError('Archive entry too large: ' + name)
        total = sum(info.file_size for info in infos)
        if total > MAX_UNCOMPRESSED:
            self.handle.close()
            raise ArchiveError('Archive expands to %d bytes; refusing to process' % total)
        self.names = set(names)

    def read(self, name):
        return self.handle.read(name)

    def namelist(self):
        return self.handle.namelist()

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
