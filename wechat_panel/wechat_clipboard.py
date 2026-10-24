"""Write verified clipboard payloads before any irreversible desktop input."""
import ntpath
from pathlib import Path
import struct
import time


class ClipboardError(RuntimeError):
    pass


def _write(fmt, payload, verify, *, text=False):
    import win32clipboard as cb
    for attempt in range(10):
        try:
            cb.OpenClipboard()
            break
        except Exception as exc:
            if attempt == 9:
                raise ClipboardError('剪贴板暂时被其他程序占用，未执行粘贴') from exc
            time.sleep(.03)
    try:
        cb.EmptyClipboard()
        if text:
            cb.SetClipboardText(payload, fmt)
        else:
            cb.SetClipboardData(fmt, payload)
        if not verify(cb.GetClipboardData(fmt)):
            raise ClipboardError('剪贴板回读内容不一致，未执行粘贴')
    except ClipboardError:
        raise
    except Exception as exc:
        raise ClipboardError('剪贴板写入或回读失败，未执行粘贴') from exc
    finally:
        cb.CloseClipboard()


def copy_files(filepaths_list):
    import win32clipboard as cb
    if isinstance(filepaths_list, (str, bytes)):
        raise ValueError('文件剪贴板需要路径列表')
    paths = [str(Path(path).resolve()) for path in filepaths_list]
    if not paths or any('\0' in path for path in paths):
        raise ValueError('文件剪贴板路径无效')
    # DROPFILES uses fixed Win32 widths, including four-byte BOOL fields.
    # Every UTF-16 path has a NUL, plus one final NUL terminating the array.
    header = struct.pack('<IiiII', 20, 0, 0, 0, 1)
    payload = header + ('\0'.join(paths) + '\0\0').encode('utf-16-le')
    normalize = lambda values: [ntpath.normcase(ntpath.normpath(str(value))) for value in values]
    expected = normalize(paths)
    _write(cb.CF_HDROP, payload, lambda value:
           isinstance(value, (tuple, list)) and normalize(value) == expected)


def copy_text(text):
    import win32clipboard as cb
    if not isinstance(text, str) or '\0' in text:
        raise ValueError('文字剪贴板内容无效')
    _write(cb.CF_UNICODETEXT, text, lambda value: value == text, text=True)
