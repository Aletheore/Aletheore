import subprocess
from pathlib import Path

from aletheore.council.clipboard import read_clipboard_image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeRun:
    """Stands in for subprocess.run: answers by command, optionally writing the file a command was told to write."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        for needle, (code, out, writes) in self.answers.items():
            if needle in " ".join(map(str, argv)):
                if writes is not None:
                    import re
                    target = re.search(r"[^\s'\"]+\.png", " ".join(map(str, argv))).group(0)
                    Path(target).write_bytes(writes)
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 1, "", "no")


def test_mac_saves_a_png_clipboard_and_ignores_text_only(tmp_path):
    run = FakeRun({"clipboard info": (0, "«class PNGf», 300", None), "write": (0, "", PNG)})
    got = read_clipboard_image(tmp_path, run=run, platform="darwin")
    assert got is not None and got.read_bytes() == PNG and got.parent == tmp_path
    text_only = FakeRun({"clipboard info": (0, "«class utf8», 12", None)})
    assert read_clipboard_image(tmp_path, run=text_only, platform="darwin") is None


def test_windows_and_linux_paths(tmp_path):
    win = FakeRun({"powershell": (0, "", PNG)})
    assert read_clipboard_image(tmp_path, run=win, platform="win32").read_bytes() == PNG
    lin = FakeRun({"wl-paste": (0, "", None)})  # no image: command fails or yields non-image bytes
    assert read_clipboard_image(tmp_path, run=lin, platform="linux") is None


def test_non_image_bytes_from_the_clipboard_are_discarded(tmp_path):
    run = FakeRun({"clipboard info": (0, "«class PNGf», 5", None), "write": (0, "", b"MZ" + b"\x00" * 40)})
    assert read_clipboard_image(tmp_path, run=run, platform="darwin") is None
    assert not list(tmp_path.glob("*.png"))


def test_missing_tools_are_not_an_error(tmp_path):
    def boom(argv, **kw):
        raise FileNotFoundError(argv[0])
    assert read_clipboard_image(tmp_path, run=boom, platform="linux") is None
    assert read_clipboard_image(tmp_path, run=boom, platform="darwin") is None
