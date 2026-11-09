"""包内路径的安全处理。

从 ZIP 包里读到的路径可能带 '..' 或绝对路径，写回 EPUB 时会越出
内容目录。这里统一清洗，拿不准的路径直接丢掉。
"""

def safe_path(path):
    """把包内路径清洗成相对路径；不安全时返回 None。"""
    if not path:
        return None
    text = path.replace('\\', '/')
    if text.startswith('/') or ':' in text.split('/')[0]:
        return None
    parts = []
    for piece in text.split('/'):
        if piece in ('', '.'):
            continue
        if piece == '..':
            if not parts:
                return None      # 试图越出根目录
            parts.pop()
            continue
        parts.append(piece)
    if not parts:
        return None
    return '/'.join(parts)


def relative_to(base, path):
    """把路径换算成相对于某个目录的形式。

    base 是参考目录（如 OEBPS），path 是包内路径。路径本来就在
    base 之下就去掉这层前缀，否则原样返回（仍然经过 safe_path 清洗）。
    越出包根的路径返回 None。
    """
    cleaned = safe_path(path)
    if cleaned is None:
        return None
    prefix = safe_path(base)
    if prefix and cleaned.startswith(prefix + '/'):
        cleaned = cleaned[len(prefix) + 1:]
    return cleaned or None
