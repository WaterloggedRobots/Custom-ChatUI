"""Pictures from ComfyUI in the client: the per-chat workflow setting, saving the
pictures the server streams, and (when PySide6 is installed) the settings window,
the chat and the picture panel.

Run from Code/:  python -m pytest -q tests
"""

import base64
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_settings  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
WORKFLOWS = {"data": [{"id": "sd15-basic", "name": "sd15-basic", "usable": True, "error": ""},
                      {"id": "old", "name": "old", "usable": False, "error": "saved in the editor's format"}],
             "comfyui": True, "error": "", "folder": "/srv/workflows"}


def test_image_settings_are_checked():
    assert chat_settings.DEFAULTS["Image Workflow"] == ""
    s = chat_settings.normalize({"Image Workflow": " sd15-basic ", "Image Mode": "on"})
    assert (s["Image Workflow"], s["Image Mode"]) == ("sd15-basic", "on")
    s = chat_settings.normalize({"Image Workflow": 3, "Image Mode": "always"})
    assert (s["Image Workflow"], s["Image Mode"]) == ("", "auto")


@pytest.fixture
def client_mod(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import llmClient
    import paths
    monkeypatch.setattr(paths, "IMAGE_DIR", tmp_path / "Images")
    return llmClient


def test_keep_image_saves_the_picture(client_mod, tmp_path):
    entry = client_mod.keep_image({"b64_json": base64.b64encode(PNG).decode(), "mime": "image/png",
                                   "prompt": "a fox", "negative_prompt": "blurry", "workflow": "sd15-basic"})
    assert entry["path"].endswith(".png") and open(entry["path"], "rb").read() == PNG
    assert entry["prompt"] == "a fox" and "b64_json" not in entry
    failed = client_mod.keep_image({"error": "ComfyUI isn't reachable", "prompt": "a fox"})
    assert failed["error"] == "ComfyUI isn't reachable" and "path" not in failed


class FakeResponse:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)

    def close(self):
        pass


def test_stream_keeps_pictures_and_sends_the_workflow(client_mod, monkeypatch):
    sent = []
    chunks = [{"choices": [{"delta": {"content": "Here."}}]},
              {"images": [{"b64_json": base64.b64encode(PNG).decode(), "mime": "image/png", "prompt": "a fox"}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 2}}]

    def post(url, json=None, **kw):
        sent.append(json)
        return FakeResponse([f"data: {__import__('json').dumps(c)}" for c in chunks] + ["data: [DONE]"])

    monkeypatch.setattr(client_mod.requests, "post", post)
    c = client_mod.LLMClient()
    c.model_name, c.tools_enabled = "m1", False
    c.image_workflow, c.image_mode = "sd15-basic", "on"
    got, done = [], []
    c.image.connect(got.append)
    c.done.connect(done.append)
    c._run([{"role": "user", "content": "draw a fox"}])
    assert sent[0]["image_workflow"] == "sd15-basic" and sent[0]["image_mode"] == "on"
    assert done[0]["images"] == got and open(got[0]["path"], "rb").read() == PNG

    # The next request tells the model what it drew.
    history = [{"role": "user", "content": "draw a fox"},
               {"role": "assistant", "content": "Here.", "images": got}]
    assert "a fox" in c.build_messages(history)[-1]["content"]


# ---------------------------------------------------------------------------
# GUI, offscreen
# ---------------------------------------------------------------------------

@pytest.fixture
def gui(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import paths
    import llmClient
    from tests.test_chat_settings import MODELS

    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / "Chat")
    monkeypatch.setattr(paths, "CHAT_INDEX_FILE", tmp_path / ".temp.json")
    monkeypatch.setattr(paths, "IMAGE_DIR", tmp_path / "Images")
    monkeypatch.setattr(llmClient.LLMClient, "list_models", lambda self: MODELS)
    monkeypatch.setattr(llmClient.LLMClient, "list_workflows", lambda self: WORKFLOWS)
    paths.ensure_data_dirs()
    app = QApplication.instance() or QApplication([])
    from Widgets import chatMain, chatSettings
    main = chatMain.ChatMain()
    settings = chatSettings.ChatSettings(main)
    yield main, settings, tmp_path, app
    settings.close()
    main.close()
    app.processEvents()


def _picture(tmp_path, name="fox.png"):
    path = tmp_path / name
    path.write_bytes(PNG)
    return str(path)


def test_settings_window_lists_the_workflows(gui):
    main, win, tmp, _ = gui
    main.create_chat("Pics", chat_settings.defaults())
    assert main.isShowImgWindowLock and not main.ui.pushButton.isEnabled()   # no workflow: no panel
    win.open_for("Pics", main.settings, is_new=False)
    assert not win.ui.imageModeCombo.isEnabled()
    win.refresh_workflows()
    combo = win.workflow_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "sd15-basic", "old"]
    combo.activated.emit(combo.findData("old"))
    assert "editor's format" in win.ui.imageStatusLabel.text()
    combo.activated.emit(combo.findData("sd15-basic"))
    win.ui.imageModeCombo.setCurrentIndex(win.ui.imageModeCombo.findData("on"))
    main.apply_settings("Pics", win.settings())
    saved = json.loads(open(main.chat_path, encoding="utf-8").read())["Settings"]
    assert (saved["Image Workflow"], saved["Image Mode"]) == ("sd15-basic", "on")
    assert (main.client.image_workflow, main.client.image_mode) == ("sd15-basic", "on")
    assert not main.isShowImgWindowLock and main.ui.pushButton.isEnabled()


def test_unknown_workflow_is_kept(gui):
    main, win, _, _ = gui
    win.open_for("x", {**chat_settings.defaults(), "Image Workflow": "gone"}, is_new=True)
    assert win.settings()["Image Workflow"] == "gone"
    win.refresh_workflows()
    assert "not on the server" in win.workflow_combo.currentText()
    assert win.settings()["Image Workflow"] == "gone"


def test_reply_pictures_show_in_the_chat_and_panel_and_redraw(gui, monkeypatch):
    main, _, tmp, app = gui
    main.create_chat("Pics", {**chat_settings.defaults(), "Image Workflow": "sd15-basic"})
    main.chat_markdown.append({"role": "user", "content": "draw a fox", "ts": 1})
    main.save_chat()
    main.response_start_pos = main.chat.textCursor().position()
    main._streamed_images = []
    entry = {"path": _picture(tmp), "prompt": "a fox", "negative_prompt": "", "workflow": "sd15-basic"}
    main.on_image(entry)
    assert main.image_panel.path == entry["path"] and main.image_panel.where == (1, 0)
    main.on_done({"content": "Here.", "images": [entry, {"error": "out of memory", "prompt": "a cat"}]})
    msg = main.chat_markdown[-1]
    assert len(msg["images"]) == 2
    rendered = main.render_assistant(msg, 1)
    assert "image:1:0" in rendered and "out of memory" in rendered

    asked = []

    def draw(self, prompt, negative, workflow):
        asked.append((prompt, workflow))
        return {"path": _picture(tmp, "fox2.png"), "prompt": prompt, "negative_prompt": negative, "workflow": workflow}

    import llmClient
    monkeypatch.setattr(llmClient.LLMClient, "draw", draw)
    main.redraw_image()
    deadline = time.time() + 5
    while main.redrawing and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert asked == [("a fox", "sd15-basic")]
    assert main.chat_markdown[1]["images"][0]["path"].endswith("fox2.png")
    assert main.image_panel.path.endswith("fox2.png")

    # Reopening the chat shows its newest picture again.
    main.image_panel.clear()
    main.load_chat(main.chat_path)
    assert main.image_panel.path.endswith("fox2.png")
