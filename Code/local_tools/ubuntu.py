"""Files and folders on the Ubuntu server (Server/ollama/chat_server/ubuntu.py).

The same tools as files.py, named ubuntu_*, run by the server on its own disk
inside the folders it allows (the home folder and /mnt/nas/REA by default).
Deleting moves to a .chatui-trash folder there.
"""

from .remote import Remote, first_line


def remote() -> Remote:
    return Remote(
        "ubuntu", {"files": "ubuntu_files"}, "Ubuntu file tools",
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
        },
        call_timeout=300,
    )


def note(roots) -> str:
    where = f" in these folders only: {'; '.join(roots)}" if roots else ""
    return (f"You can browse, read and edit files and folders on the Ubuntu server with the ubuntu_* tools{where}. "
            "Ubuntu paths look like /home/... or ~/...; they are not Windows paths. The PC's tools can't open "
            "them, and the ubuntu_* tools can't open the PC's files.")
