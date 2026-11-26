import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
from unittest.mock import Mock, patch

import bridge_ui


@unittest.skipUnless(sys.platform == "win32", "Tests Windows tray behavior")
class WindowLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.addCleanup(self.destroy_window)
        self.root.withdraw()
        bridge_ui.install_styles(self.root)
        config = {"sources": [], "auto_sync": True, "db_path": str(Path(self.temp.name) / "unused.db")}
        self.tray_factory = self.enterContext(patch.object(bridge_ui, "WindowsTray", autospec=True))
        self.tray = self.tray_factory.return_value
        self.tray.show.return_value = True
        self.window = bridge_ui.BridgeWindow(self.root, config)
        self.root.geometry("+20000+20000")
        self.root.deiconify()
        self.root.update()

    def destroy_window(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # The explicit exit action already destroyed the test window.

    def click_titlebar_close(self):
        self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        self.root.update()

    def test_titlebar_close_keeps_auto_timer_running_and_window_restorable(self):
        self.window.start_sync = Mock()
        # Let one real Tk timer expire while the window is hidden in the tray.
        with patch.object(bridge_ui, "INTERVAL", 50):
            self.window.schedule_auto()
        self.click_titlebar_close()
        self.assertEqual(self.root.state(), "withdrawn")
        self.tray.show.assert_called_once_with()
        self.assertFalse(self.window.closing)
        self.root.after(160, self.root.quit)
        self.root.mainloop()
        self.window.start_sync.assert_called_once_with([])
        _, restore, _ = self.tray_factory.call_args.args
        restore()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")
        self.tray.hide.assert_called_once_with()
        self.window.exit_app()
        self.assertRaises(tk.TclError, self.root.winfo_exists)

    def test_failed_tray_registration_leaves_taskbar_entry(self):
        self.tray.show.return_value = False
        self.click_titlebar_close()
        self.assertEqual(self.root.state(), "iconic")

    def test_native_notification_is_delivered_through_tk_event_loop(self):
        import bridge_tray

        restored = Mock(side_effect=self.root.quit)
        tray = bridge_tray.WindowsTray(self.root, restored, self.fail)
        # Exercise the real Win32 callback even on a desktop without Explorer.
        with (patch.object(bridge_tray, "_notify", return_value=True),
              patch.object(bridge_tray, "_load_image", wraps=bridge_tray._load_image) as load_icon):
            try:
                self.assertTrue(tray.show())
                self.assertEqual(Path(load_icon.call_args.args[1]), bridge_tray.ICON_PATH)
                self.assertTrue(tray._data.hIcon)
                self.root.withdraw()
                select = (bridge_tray.ICON_ID << 16) | bridge_tray.NIN_SELECT
                self.assertTrue(bridge_tray._post(tray.hwnd, bridge_tray.TRAY_MESSAGE, 0, select))
                timeout = self.root.after(2500, self.root.quit)
                self.root.mainloop()
                self.root.after_cancel(timeout)
                restored.assert_called_once_with()
            finally:
                tray.close()

    def test_tray_exit_waits_for_active_sync(self):
        self.window.running = True
        self.click_titlebar_close()
        self.assertFalse(self.window.closing)
        _, _, exit_from_tray = self.tray_factory.call_args.args
        exit_from_tray()
        self.tray.close.assert_called_once_with()
        self.assertTrue(self.window.closing)
        self.assertIsNone(self.window.timer)
        self.assertEqual(self.root.state(), "withdrawn")
        self.window.messages.put(("finished", None, None))
        self.window.poll()
        self.assertRaises(tk.TclError, self.root.winfo_exists)

    def test_exit_button_destroys_window(self):
        widgets = [self.root]
        while widgets:
            widget = widgets.pop()
            if isinstance(widget, ttk.Button) and widget.cget("text") == "退出助手":
                widget.invoke()
                break
            widgets.extend(widget.winfo_children())
        else:
            self.fail("Missing exit button")
        self.assertRaises(tk.TclError, self.root.winfo_exists)
