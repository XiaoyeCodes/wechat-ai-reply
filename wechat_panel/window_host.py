"""Keep minimized chat windows rendering without taking foreground focus."""
import json
from pathlib import Path


class NativeWindows:
    def identity(self, handle):
        import psutil
        import win32gui
        import win32process
        if not win32gui.IsWindow(handle):
            return None
        thread, pid = win32process.GetWindowThreadProcessId(handle)
        return [pid, psutil.Process(pid).create_time(), thread, win32gui.GetClassName(handle)]

    def minimized(self, handle):
        import win32gui
        return bool(win32gui.IsIconic(handle))

    def foreground(self):
        import win32gui
        return win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2)

    def placement(self, handle):
        import win32gui
        return win32gui.GetWindowPlacement(handle)

    def on_screen(self, handle):
        import win32api
        import win32gui
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        return any(left < r and right > l and top < b and bottom > t
                   for _, _, (l, t, r, b) in win32api.EnumDisplayMonitors())

    def park(self, handle):
        import win32api
        import win32con
        import win32gui
        # SetWindowPlacement clamps offscreen positions back onto a monitor.
        # Restore without activation, then move using SetWindowPos instead.
        win32gui.ShowWindow(handle, win32con.SW_SHOWNOACTIVATE)
        x = win32api.GetSystemMetrics(76)+win32api.GetSystemMetrics(78)+200
        win32gui.SetWindowPos(handle, 0, x, win32api.GetSystemMetrics(77), 0, 0,
                             win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE |
                             win32con.SWP_NOZORDER | win32con.SWP_NOOWNERZORDER)
        if self.minimized(handle) or self.on_screen(handle):
            raise RuntimeError('聊天窗口未能进入后台托管，请重试监听')

    def restore(self, handle, placement, minimized):
        import win32con
        import win32gui
        flags, _, minimum, maximum, normal = placement
        command = win32con.SW_SHOWMINNOACTIVE if minimized else win32con.SW_SHOWNOACTIVATE
        # SetWindowPlacement also adapts a saved position to changed monitors.
        win32gui.SetWindowPlacement(handle, (flags, command, tuple(minimum), tuple(maximum), tuple(normal)))


class WindowHost:
    def __init__(self, path, matches, native=None):
        self.path, self.matches = Path(path), matches
        self.native = native or NativeWindows()
        try:
            records = json.loads(self.path.read_text(encoding='utf-8'))
            self.records = records if isinstance(records, dict) else {}
        except (OSError, ValueError):
            self.records = {}
        # Recover positions left behind by a terminated worker before binding.
        self.release_all()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.records, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def _valid(self, handle, record):
        try:
            return (self.native.identity(handle) == record['identity']
                    and self.matches(handle, record))
        except Exception:
            return False

    def managed(self, handle):
        return str(handle) in self.records

    def ensure(self, handle, contact):
        """Called only after the bridge verifies the account and chat title."""
        key = str(handle)
        record = self.records.get(key)
        if record and not self._valid(handle, record):
            del self.records[key]
            self._save()
            raise RuntimeError('后台窗口身份发生变化，请重新连接联系人')
        minimized = self.native.minimized(handle)
        if record and not minimized:
            if self.native.foreground() == handle or self.native.on_screen(handle):
                # Taskbar activation or a manual move returns the user's window.
                self.reveal(handle)
            return False
        if not minimized:
            return False
        if record is None:
            record = dict(identity=self.native.identity(handle), placement=self.native.placement(handle),
                          account_id=contact['account_id'], lookup=contact['lookup'])
            self.records[key] = record
            self._save()  # Persist BEFORE moving, including forced worker termination.
        try:
            self.native.park(handle)
        except Exception:
            self.release(handle)
            raise
        return True

    def reveal(self, handle):
        self.release(handle, minimized=False)

    def release(self, handle, minimized=True):
        key = str(handle)
        record = self.records.get(key)
        if record is None:
            return
        if self._valid(handle, record):
            self.native.restore(handle, record['placement'], minimized)
        del self.records[key]
        self._save()

    def release_all(self):
        for handle in list(self.records):
            try:
                self.release(int(handle))
            except Exception:
                pass  # Retain the journal so a subsequent worker can recover.
