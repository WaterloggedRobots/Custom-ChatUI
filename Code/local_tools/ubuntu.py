"""Files, folders and commands on the Ubuntu server (Server/ollama/chat_server/ubuntu.py).

The same tools as files.py and terminal.py, named ubuntu_*, run by the server
on its own disk inside the folders it allows (the home folder and the NAS at /mnt/nas
by default). Deleting moves to a .chatui-trash folder there. They are two
groups, so a chat can have the files without the commands: ubuntu_files and ubuntu_run.
Server folders linked to the chat (ToolBox.server_folders) go with every call,
and the server then keeps the tools inside them.
"""

from .remote import Remote, first_line


def remote() -> Remote:
    return Remote(
        "ubuntu", {"files": "ubuntu_files", "run": "ubuntu_run"}, "Ubuntu file and run tools",
        {
            "ubuntu_write_file": lambda a: f"{'overwrite' if a.get('overwrite') else 'create'} {a.get('path')} "
                                           f"({len(a.get('content', '')):,} characters)",
            "ubuntu_edit_file": lambda a: f"edit {a.get('path')}",
            "ubuntu_create_folder": lambda a: f"create folder {a.get('path')}",
            "ubuntu_move_path": lambda a: f"move {a.get('source')} to {a.get('destination')}",
            "ubuntu_copy_path": lambda a: f"copy {a.get('source')} to {a.get('destination')}",
            "ubuntu_delete_path": lambda a: f"delete {a.get('path')}{' and everything in it' if a.get('recursive') else ''}"
                                            " (to its trash folder)",
            "ubuntu_list_directory": lambda a: f"list {a.get('path', '~')}",
            "ubuntu_read_file": lambda a: f"read {a.get('path')}",
            "ubuntu_find_files": lambda a: f"find {a.get('pattern')} in {a.get('path', '~')}",
            "ubuntu_search_in_files": lambda a: f"search for {first_line(a.get('query'), 80)!r} in {a.get('path', '~')}",
            "ubuntu_run_command": lambda a: f"run in bash in {a.get('cwd') or '~'}:\n{a.get('command')}",
        },
        call_timeout=1900,       # a command may run for up to 30 minutes (TOOL_RUN_MAX_TIMEOUT)
        limited=True,
    )


def note(roots, files: bool, run: bool, linked=()) -> str:
    if linked:
        where = (f" in the server folders the user linked to this chat, and only there: {'; '.join(linked)}"
                 f" (the first is the default when you give no path)")
    else:
        where = f" in these folders only: {'; '.join(roots)}" if roots else ""
    can = [text for on, text in ((files, "browse, read and edit files and folders"),
                                 (run, "run bash commands (ubuntu_run_command)")) if on]
    return (f"On the Ubuntu server you can {' and '.join(can)} with the ubuntu_* tools{where}. "
            "Ubuntu paths look like /home/... or ~/...; they are not Windows paths. The PC's tools can't open "
            "them, and the ubuntu_* tools can't open the PC's files.")
