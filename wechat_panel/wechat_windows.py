"""Discover current Weixin windows, including ones omitted from the UIA desktop.

Qt's native class is shared by main, login and independent chat windows. Only
the UIA class obtained from the exact HWND identifies their purpose. Native
enumeration supplies hidden HWNDs; neither a caption nor a cached HWND proves
that a window is the current main window.
"""


def discover_weixin_windows():
    import psutil
    import win32gui
    import win32process
    from pywinauto.uia_element_info import UIAElementInfo

    handles=[]
    win32gui.EnumWindows(lambda handle,_:handles.append(handle),None)
    processes={}
    found=[]
    for handle in handles:
        try:
            if not win32gui.IsWindow(handle):continue
            _,pid=win32process.GetWindowThreadProcessId(handle)
            if pid not in processes:
                process=psutil.Process(pid)
                processes[pid]=(process.name().lower(),process.create_time())
            name,started_at=processes[pid]
            if name!='weixin.exe':continue
            element=UIAElementInfo(handle)
            kind=element.class_name
            if kind not in ('mmui::MainWindow','mmui::LoginWindow'):continue
            # The HWND may be destroyed/reused while UIA is resolving it.
            if (not win32gui.IsWindow(handle)
                    or win32process.GetWindowThreadProcessId(handle)[1]!=pid
                    or element.process_id!=pid
                    or psutil.Process(pid).create_time()!=started_at):continue
            found.append(dict(handle=handle,pid=pid,started_at=started_at,kind=kind))
        except (psutil.Error,OSError):
            continue  # Another application's window can disappear at any time.
        except Exception as exc:
            from .bridge import connection_error
            if connection_error(exc):continue
            raise
    return found
