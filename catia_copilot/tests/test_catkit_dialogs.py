import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from catia_copilot.catia import dialogs


class FakeWindow:
    def __init__(self, hwnd, pid, title, class_name, enabled=True, owner=0,
                 controls=(), friendly="Dialog"):
        self.handle = hwnd
        self.pid = pid
        self._title = title
        self._class = class_name
        self._enabled = enabled
        self._owner = owner
        self._controls = controls
        self._friendly = friendly

    def process_id(self):
        return self.pid

    def window_text(self):
        return self._title

    def class_name(self):
        return self._class

    def friendly_class_name(self):
        return self._friendly

    def is_enabled(self):
        return self._enabled

    def descendants(self):
        return self._controls


class FakeControl(FakeWindow):
    def __init__(self, hwnd, text, class_name="Button", enabled=True):
        super().__init__(hwnd, 7, text, class_name, enabled)
        self.value = ""

    def set_edit_text(self, text):
        self.value = text


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.posted = []
        self.win32gui = SimpleNamespace(
            IsWindow=lambda hwnd: True,
            IsWindowVisible=lambda hwnd: True,
            GetWindow=lambda hwnd, relation: 0,
            IsWindowEnabled=lambda hwnd: True,
            IsChild=lambda parent, child: True,
            PostMessage=lambda hwnd, message, wparam, lparam: self.posted.append(hwnd),
        )
        self.patcher = patch.object(dialogs, "win32gui", self.win32gui)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def desktop(self, windows):
        controls = {int(child.handle): child for window in windows
                    for child in window.descendants()}

        class Desktop:
            def __init__(self, backend):
                self.backend = backend

            def windows(self, **kwargs):
                return [window for window in windows
                        if kwargs.get("process") is None or
                        window.pid == kwargs["process"]]

            def window(self, handle):
                return SimpleNamespace(wrapper_object=lambda: controls[handle])

        return Desktop

    def test_scan_keeps_standard_message_box_even_with_dialog_friendly_name(self):
        standard = FakeWindow(11, 7, "Warning", "#32770",
                              controls=[FakeControl(21, "OK")])
        ordinary = FakeWindow(12, 7, "CATIA", "AfxFrameOrView")
        Desktop = self.desktop([standard, ordinary])
        with patch.object(dialogs, "Desktop", Desktop):
            records = dialogs.scan_dialogs([7])
        self.assertEqual([item["hwnd"] for item in records], [11])

    def test_scan_ignores_window_destroyed_during_inspection(self):
        window = FakeWindow(11, 7, 'Closing', '#32770', controls=[FakeControl(21, 'OK')])
        self.win32gui.IsWindow = lambda hwnd: False
        with patch.object(dialogs, 'Desktop', self.desktop([window])):
            self.assertEqual(dialogs.scan_dialogs([7]), [])

    def test_scan_ignores_empty_teardown_snapshot(self):
        window = FakeWindow(11, 7, '', '#32770', controls=[FakeControl(21, '', enabled=False)])
        with patch.object(dialogs, 'Desktop', self.desktop([window])):
            self.assertEqual(dialogs.scan_dialogs([7]), [])

    def test_scan_filters_by_pid_and_excludes_tooltip_longhelp(self):
        keep = FakeWindow(11, 7, "Warning", "#32770")
        other_pid = FakeWindow(12, 8, "Warning", "#32770")
        tooltip = FakeWindow(13, 7, "Help", "CATIA_TooltipWindow")
        longhelp = FakeWindow(14, 7, "Help", "CATIA_LongHelpWindow")
        Desktop = self.desktop([keep, other_pid, tooltip, longhelp])
        with patch.object(dialogs, "Desktop", Desktop):
            records = dialogs.scan_dialogs([7])
        self.assertEqual([item["hwnd"] for item in records], [11])

    def test_process_ids_require_visible_window_and_cnext_name(self):
        with patch.object(dialogs, "_visible_window_pids", return_value={7, 8}), \
                patch.object(dialogs, "_process_executable_name",
                             side_effect=lambda pid: {7: "CNEXT.exe", 8: "other.exe"}[pid]):
            self.assertEqual(dialogs.catia_process_ids(), [7])

    def test_respond_selects_same_named_button_by_id(self):
        first, second = FakeControl(21, "OK"), FakeControl(22, "OK")
        window = FakeWindow(11, 7, "Warning", "#32770", controls=[first, second])
        Desktop = self.desktop([window])
        with patch.object(dialogs, "Desktop", Desktop):
            record = dialogs.scan_dialogs([7])[0]
            dialogs.respond(record, button="22")
        self.assertEqual(self.posted, [22])

    def test_respond_rejects_stale_fingerprint(self):
        window = FakeWindow(11, 7, "Warning", "#32770",
                            controls=[FakeControl(21, "OK")])
        Desktop = self.desktop([window])
        with patch.object(dialogs, "Desktop", Desktop):
            record = dialogs.scan_dialogs([7])[0]
            window._title = "Changed"
            with self.assertRaisesRegex(ValueError, "stale"):
                dialogs.respond(record, button="21")

    def test_respond_sets_text_on_edit(self):
        edit = FakeControl(31, "", "Edit")
        window = FakeWindow(11, 7, "Input", "#32770", controls=[edit])
        Desktop = self.desktop([window])
        with patch.object(dialogs, "Desktop", Desktop):
            record = dialogs.scan_dialogs([7])[0]
            dialogs.respond(record, control="31", text="part name")
        self.assertEqual(edit.value, "part name")

    def test_respond_rejects_disabled_edit(self):
        edit = FakeControl(31, "", "Edit", enabled=False)
        window = FakeWindow(11, 7, "Input", "#32770", controls=[edit])
        Desktop = self.desktop([window])
        with patch.object(dialogs, "Desktop", Desktop):
            record = dialogs.scan_dialogs([7])[0]
            with self.assertRaisesRegex(ValueError, "disabled"):
                dialogs.respond(record, control="31", text="part name")


if __name__ == "__main__":
    unittest.main()
