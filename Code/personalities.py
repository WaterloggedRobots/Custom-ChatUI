"""Saved personalities: settings that any number of chats can talk as.

They're optional. A chat that doesn't use one keeps all its settings to itself,
and a one-off bot never needs one. A personality only holds who is talking, in
PERSONALITY_KEYS; tools, web search, pictures and memory stay with each chat. Its
file is the same format as an exported settings file (chat_settings.py), kept in
Save/Personalities/<name>/personality.json next to its memory folder.

A chat that uses a personality always takes its name, description and whether its
model may use git (REQUIRED).
The personality's name is the character's name: there's one name, and a chat can't
change it while it talks as the personality. OPTIONAL holds what it can choose to keep to itself instead: the voice, the 3D
model, and the LLM model. It also follows the personality except for "overrides":
the keys the chat changed for itself. The chat's own values stay in its file, so
unlinking the personality gives the chat back what it had.

No Qt here, so it can be tested on its own (tests/test_chat_links.py).
"""

import shutil
from pathlib import Path

import chat_settings
import paths

REQUIRED = ("Character", "Pretext", chat_settings.TOOL_KEYS["git"])
OPTIONAL = {
    "Voice": ("TTS", "TTS Engine", "TTS Voice", "TTS Speed", "TTS Auto Play", "TTS Translate"),
    "3D model": ("Avatar", "Avatar Personality", "Avatar Notes"),
    "LLM model": ("Model", "Temperature"),
}
PERSONALITY_KEYS = (*REQUIRED, *(key for keys in OPTIONAL.values() for key in keys))


def followed_keys(skipped=()) -> tuple:
    """The personality's keys a chat takes, when it keeps the groups in `skipped` to itself."""
    left_out = {key for group in skipped for key in OPTIONAL.get(group, ())}
    return tuple(key for key in PERSONALITY_KEYS if key not in left_out)


def _follows(key: str, personality: dict) -> bool:
    return key != "Model" or bool(personality["Model"])      # no model saved: each chat picks its own


def resolve(own: dict, personality, overrides=(), skipped=()) -> dict:
    """The settings a chat runs with: its own, with the personality's over them."""
    settings = dict(own)
    if personality:
        for key in followed_keys(skipped):
            if (key == "Character" or key not in overrides) and _follows(key, personality):
                settings[key] = personality[key]
    return chat_settings.normalize(settings)


def split(settings: dict, own: dict, personality: dict, skipped=()):
    """(what to save as the chat's own settings, the overrides) from the settings it runs with."""
    new_own, overrides = dict(settings), []
    for key in followed_keys(skipped):
        if not _follows(key, personality):
            continue
        if key == "Character" or settings[key] == personality[key]:
            new_own[key] = own[key]
        else:
            overrides.append(key)
    return new_own, overrides


def _file(name: str) -> Path:
    return paths.PERSONALITY_DIR / name / "personality.json"


def load(name: str):
    """The personality's settings, or None if it has no readable file."""
    try:
        return {**chat_settings.import_file(_file(name)), "Character": name}
    except ValueError as e:
        print(f"Can't read personality {name}: {e}")
        return None


def save(name: str, settings: dict, skipped=()) -> None:
    """Save the personality's part of `settings`. The groups in `skipped` keep what it had saved."""
    data = load(name) if skipped else None
    data = data or chat_settings.defaults()
    data.update({key: settings[key] for key in followed_keys(skipped)})
    data["Character"] = name
    _file(name).parent.mkdir(parents=True, exist_ok=True)
    chat_settings.export_file(data, _file(name))


def rename_folder(folder: Path, new: Path) -> bool:
    """Move a project's or personality's folder, memory notes included; False if the new name is taken."""
    if new.exists():
        return False
    if folder.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        folder.rename(new)
    return True


def remove_folder(folder: Path) -> None:
    """To the Recycle Bin where there is one; otherwise out of the way in a Deleted folder, never gone."""
    from local_tools.files import _recycle
    if not folder.exists() or _recycle(folder):
        return
    bin_dir = folder.parent / "Deleted"
    bin_dir.mkdir(exist_ok=True)
    target, i = bin_dir / folder.name, 1
    while target.exists():
        target = bin_dir / f"{folder.name} ({i})"
        i += 1
    shutil.move(str(folder), str(target))
