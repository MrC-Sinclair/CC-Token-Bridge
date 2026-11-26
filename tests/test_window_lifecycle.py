import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
from unittest.mock import Mock, patch

import bridge_ui


@unittest.skipUnless(sys.platform == "win32", "Tests Windows taskbar behavior")
class WindowLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.addCleanup(self.destroy_window)
        self.root.withdraw()
        bridge_ui.install_styles(self.root)
        config = {"sources": [], "auto_sync": True, "db_path": str(Path(self.temp.name) / "unused.db")}
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
        # Let one real Tk timer expire while the window is minimized.
        with patch.object(bridge_ui, "INTERVAL", 50):
            self.window.schedule_auto()
        self.click_titlebar_close()
        self.assertEqual(self.root.state(), "iconic")
        self.assertFalse(self.window.closing)
        self.root.after(160, self.root.quit)
        self.root.mainloop()
        self.window.start_sync.assert_called_once_with([])
        self.root.deiconify()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")
        self.window.exit_app()
        self.assertRaises(tk.TclError, self.root.winfo_exists)

    def test_exit_button_waits_for_active_sync(self):
        self.window.running = True
        self.click_titlebar_close()
        self.assertFalse(self.window.closing)
        self.root.deiconify()
        self.root.update()
        widgets = [self.root]
        while widgets:
            widget = widgets.pop()
            if isinstance(widget, ttk.Button) and widget.cget("text") == "退出助手":
                widget.invoke()
                break
            widgets.extend(widget.winfo_children())
        else:
            self.fail("Missing exit button")
        self.assertTrue(self.window.closing)
        self.assertIsNone(self.window.timer)
        self.assertEqual(self.root.state(), "withdrawn")
        self.window.messages.put(("finished", None, None))
        self.window.poll()
        self.assertRaises(tk.TclError, self.root.winfo_exists)
