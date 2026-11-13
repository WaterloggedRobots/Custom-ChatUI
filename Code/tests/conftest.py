"""Every test saves into its own temporary folder, never your chats on the NAS or this PC.

paths.py picks the save folder when it's imported, so each test points all of it at
tmp_path first; a test's own fixture can still move parts of it elsewhere.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402


@pytest.fixture(autouse=True)
def _saves_in_tmp(tmp_path, monkeypatch):
    save = tmp_path / "Save"
    for name, value in (("CLIENT_DATA_DIR", tmp_path), ("LOCAL_SAVE_DIR", save), ("SAVE_DIR", save),
                        ("SAVE_ON_NAS", False), ("NAS_SAVE_DIR", None),
                        ("CHAT_DIR", save / "Chat"), ("CHAT_INDEX_FILE", save / ".temp.json"),
                        ("LINKS_FILE", save / "links.json"), ("PROJECT_DIR", save / "Projects"),
                        ("PERSONALITY_DIR", save / "Personalities"), ("USER_NETWORK_FILE", tmp_path / "network.json"),
                        ("AUDIO_DIR", tmp_path / "Audio"), ("IMAGE_DIR", tmp_path / "Images")):
        monkeypatch.setattr(paths, name, value)
