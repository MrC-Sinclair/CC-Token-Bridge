"""Windows notification-area icon. The hidden host uses the Tk thread's message loop."""

import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path


ICON_PATH = Path(__file__).with_name("usage.ico")
ICON_ID = 1
TRAY_MESSAGE = 0x8001  # WM_APP + 1
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
WM_CONTEXTMENU = 0x007B
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
OPEN, EXIT = 1, 2
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON = 49, 50

WindowProc = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WindowClass(ct.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WindowProc),
        ("cbClsExtra", ct.c_int), ("cbWndExtra", ct.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class NotifyIconData(ct.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", wt.DWORD * 4), ("hBalloonIcon", wt.HICON),
    ]


def _api(library, name, result_type, *argument_types):
    function = getattr(library, name)
    function.restype = result_type
    function.argtypes = argument_types
    return function


user32 = ct.WinDLL("user32")
_module = _api(ct.WinDLL("kernel32"), "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
_notify = _api(ct.WinDLL("shell32"), "Shell_NotifyIconW", wt.BOOL, wt.DWORD, ct.POINTER(NotifyIconData))
_register_class = _api(user32, "RegisterClassW", wt.ATOM, ct.POINTER(WindowClass))
_unregister_class = _api(user32, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
_register_message = _api(user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
_create_window = _api(user32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR,
                      wt.DWORD, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU,
                      wt.HINSTANCE, wt.LPVOID)
_destroy_window = _api(user32, "DestroyWindow", wt.BOOL, wt.HWND)
_default_proc = _api(user32, "DefWindowProcW", ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_post = _api(user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_load_image = _api(user32, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                   ct.c_int, ct.c_int, wt.UINT)
_destroy_icon = _api(user32, "DestroyIcon", wt.BOOL, wt.HICON)
_metric = _api(user32, "GetSystemMetrics", ct.c_int, ct.c_int)
_new_menu = _api(user32, "CreatePopupMenu", wt.HMENU)
_append = _api(user32, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, ct.c_size_t, wt.LPCWSTR)
_foreground = _api(user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
_track_menu = _api(user32, "TrackPopupMenu", wt.BOOL, wt.HMENU, wt.UINT, ct.c_int,
                   ct.c_int, ct.c_int, wt.HWND, wt.LPVOID)
_destroy_menu = _api(user32, "DestroyMenu", wt.BOOL, wt.HMENU)
_cursor_position = _api(user32, "GetCursorPos", wt.BOOL, ct.POINTER(wt.POINT))


class WindowsTray:
    def __init__(self, root, on_restore, on_exit):
        self.root = root
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.hwnd = None
        self.visible = False
        self._pending = None
        self._poll_timer = None
        self._closed = False
        self._registered = False
        self._class_name = f"CCUsageBridge.Tray.{id(self):x}"
        self._instance = _module(None)
        self._taskbar_created = _register_message("TaskbarCreated")
        self._proc = WindowProc(self._handle_message)  # Keep the callback alive until UnregisterClass.
        self._data = NotifyIconData()

    def _create_host(self):
        if not self._taskbar_created:
            return False
        cls = WindowClass(lpfnWndProc=self._proc, hInstance=self._instance,
                          lpszClassName=self._class_name)
        self._registered = bool(_register_class(ct.byref(cls)))
        if not self._registered:
            return False
        # An invisible top-level tool window receives Explorer's TaskbarCreated broadcast.
        self.hwnd = _create_window(0x80, self._class_name, "CCUsageBridge.Tray", 0x80000000,
                                   0, 0, 0, 0, None, None, self._instance, None)
        if not self.hwnd:
            _unregister_class(self._class_name, self._instance)
            self._registered = False
            return False
        # Ask Windows for the matching small frame from the packaged multi-size ICO.
        icon = _load_image(None, str(ICON_PATH), IMAGE_ICON,
                           _metric(SM_CXSMICON), _metric(SM_CYSMICON), LR_LOADFROMFILE)
        if not icon:
            _destroy_window(self.hwnd)
            self.hwnd = None
            _unregister_class(self._class_name, self._instance)
            self._registered = False
            return False
        self._data = NotifyIconData(
            cbSize=ct.sizeof(NotifyIconData), hWnd=self.hwnd, uID=ICON_ID,
            uFlags=0x01 | 0x02 | 0x04 | 0x80,  # NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
            uCallbackMessage=TRAY_MESSAGE, hIcon=icon, szTip="CC 用量助手", uVersion=4,
        )
        return True

    def _add_icon(self):
        data = ct.byref(self._data)
        if not (_notify(NIM_ADD, data) or _notify(NIM_MODIFY, data)):
            return False
        if not _notify(NIM_SETVERSION, data):
            _notify(NIM_DELETE, data)
            return False
        return True

    def show(self):
        if self._closed:
            return False
        if not self.visible and (self.hwnd or self._create_host()):
            self.visible = self._add_icon()
        if self.visible and self._poll_timer is None:
            self._poll_timer = self.root.after(90, self._dispatch)
        return self.visible

    def hide(self):
        if self._poll_timer is not None:
            self.root.after_cancel(self._poll_timer)
            self._poll_timer = None
        self._pending = None
        if self.visible:
            self.visible = False
            _notify(NIM_DELETE, ct.byref(self._data))

    def close(self):
        self._closed = True
        self.hide()
        if self._data.hIcon:
            _destroy_icon(self._data.hIcon)
            self._data.hIcon = None
        if self.hwnd:
            _destroy_window(self.hwnd)
            self.hwnd = None
        if self._registered:
            _unregister_class(self._class_name, self._instance)
            self._registered = False

    def _dispatch(self):
        # _tkinter must be entered from a Tk callback, never from a ctypes WNDPROC.
        self._poll_timer = None
        command = self._pending
        self._pending = None
        if command == OPEN:
            self.on_restore()
        elif command == EXIT:
            self.on_exit()
        if self.visible and self._poll_timer is None:
            self._poll_timer = self.root.after(90, self._dispatch)

    def _popup(self, hwnd, position):
        menu = _new_menu()
        if not menu:
            self._pending = OPEN
            return
        try:
            if not (_append(menu, 0, OPEN, "打开助手") and _append(menu, 0, EXIT, "退出助手")):
                self._pending = OPEN
                return
            point = wt.POINT(ct.c_short(position & 0xffff).value,
                             ct.c_short((position >> 16) & 0xffff).value)
            if (point.x, point.y) == (-1, -1):
                _cursor_position(ct.byref(point))
            _foreground(hwnd)
            _track_menu(menu, 0x02, point.x, point.y, 0, hwnd, None)  # TPM_RIGHTBUTTON
            _post(hwnd, 0, 0, 0)  # WM_NULL allows the next notification-area menu to dismiss.
        finally:
            _destroy_menu(menu)

    def _handle_message(self, hwnd, message, wparam, lparam):
        if self.visible:
            if message == self._taskbar_created:
                if not self._add_icon():
                    self.visible = False
                    self._pending = OPEN
                return 0
            if message == TRAY_MESSAGE and (lparam >> 16) & 0xffff == ICON_ID:
                event = lparam & 0xffff
                if event in (NIN_SELECT, NIN_KEYSELECT, WM_LBUTTONUP):
                    if self._pending is None:
                        self._pending = OPEN
                elif event == WM_CONTEXTMENU:
                    self._popup(hwnd, wparam)
                return 0
            if message == WM_COMMAND:
                command = wparam & 0xffff
                if command in (OPEN, EXIT):
                    self._pending = command
                return 0
        return _default_proc(hwnd, message, wparam, lparam)
