"""A project's own chat hands out the work: it reads the user's request, starts new
sessions or picks existing ones, links them to the project and gives each a brief.

The sessions then work through their briefs in the background, one at a time
(Widgets/sessionRunner.py), and each one's answer comes back to the project's chat
as a report. What a session does is still asked of you like in any chat: its
changes and commands wait for your OK.

The GUI hands the toolbox a Desk (ToolBox.project) for the project's chat only, so
no other chat can start sessions. Handing out work runs without asking (kind
"delegate"): it only writes a brief, and the session asks before it changes anything.
"""

from typing import Optional, Protocol

from . import Tool, ToolError, params

BRIEF_CHARS = 12000          # one brief


class Desk(Protocol):
    """What the GUI does for the project's chat (ChatMain's ProjectDesk). Safe to call from the worker thread."""
    project: str
    personality: Optional[str]      # who the project's chat speaks as; None = it's a technical lead

    def sessions(self) -> list:     # [{"name", "summary", "state"}]
        ...

    def start(self, title: str, brief: str) -> str:   # the new session's name
        ...

    def assign(self, chat: str, brief: str) -> str:   # what happened, for the model
        ...


def _desk(box) -> Desk:
    if box.project is None:
        raise ToolError("only a project's own chat can hand out work")
    return box.project


def _brief(text) -> str:
    text = str(text or "").strip()
    if not text:
        raise ToolError("the brief is empty; say what the session should do and what it needs to know")
    if len(text) > BRIEF_CHARS:
        raise ToolError(f"the brief is {len(text):,} characters; keep it under {BRIEF_CHARS:,}")
    return text


def list_sessions(box) -> str:
    desk = _desk(box)
    rows = desk.sessions()
    if not rows:
        return f"Project {desk.project} has no sessions yet. Start one with start_session."
    lines = [f"Sessions of project {desk.project}:"]
    for row in rows:
        summary = f": {row['summary']}" if row.get("summary") else ""
        lines.append(f"- {row['name']} [{row['state']}]{summary}")
    lines.append("Read one with read_linked_chat for its details.")
    return "\n".join(lines)


def start_session(box, title, brief) -> str:
    desk = _desk(box)
    name = desk.start(str(title or "").strip() or "Session", _brief(brief))
    return (f"Started session {name} in project {desk.project} with your brief. It works on it in the background; "
            "its answer comes back here as a report.")


def assign_task(box, session, brief) -> str:
    return _desk(box).assign(str(session or "").strip(), _brief(brief))


SESSION = {"type": "string", "description": "The session's name, as list_sessions shows it"}
BRIEF = {"type": "string", "description": "Everything the session needs to do the task on its own: the goal, the "
                                          "context and decisions from this chat, the files, folders or repositories "
                                          "involved, limits, and what done looks like. It can't see this chat."}

TOOLS = [
    Tool("list_sessions", "List this project's sessions: the chats that work on its tasks, with what each is about "
                          "and whether it's idle, queued or working.",
         params(), list_sessions, summary=lambda a: "List the project's sessions"),
    Tool("start_session", "Start a new session for one task, linked to this project, and give it its brief. "
                          "Use it for work that no existing session covers.",
         params(["title", "brief"], title={"type": "string", "description": "A short name for the session, like "
                                                                            "'Login page' or 'Weekly report'"},
                brief=BRIEF),
         start_session, kind="delegate", summary=lambda a: f"Start session {a.get('title')}"),
    Tool("assign_task", "Give an existing session another task with a brief. A chat that isn't in this project yet is "
                        "linked to it first. Use it when the work continues what that session did.",
         params(["session", "brief"], session=SESSION, brief=BRIEF),
         assign_task, kind="delegate", summary=lambda a: f"Hand {a.get('session')} a task"),
]


def lead_note(project: str, personality: Optional[str], name: str) -> str:
    """The system note of a project's own chat: what it's for, and its tone."""
    common = (
        f"This is the own chat of project {project}, where its work is handed out. Never do a task yourself here, "
        "however small or quick: don't write the list, the email, the code or the answer. Split what the user asks "
        "into tasks and give each one to a session with start_session or assign_task, one call per task, in this "
        "reply. Call list_sessions first and give work to a session that already knows that area; start a new one "
        "for new work. Each session only sees its brief and its own chat, so put in the brief everything it needs "
        "from this conversation. Sessions work in the background, one at a time, and their answers come back here "
        "as reports; after handing out the work, tell the user in a few lines who does what, not the results. "
        "When a report arrives, say where things stand and hand out what's next. Only questions about the plan or "
        "the sessions' progress, and small talk, you answer yourself.")
    if personality:
        return common + (
            f" You are {name}, and these sessions are your own tasks. Talk like a person juggling several jobs at "
            "once: natural and in your own voice, saying which task you'll pick up, which can wait and how it's "
            "going. Write the briefs as notes to yourself for later, in plain words, but still complete.")
    return common + (
        " You are the technical lead and the sessions are your team. Be precise and brief, like a manager "
        "assigning work: for each task give the objective, the context, the files and constraints, the steps "
        "if they matter, and acceptance criteria. Tell the user the plan as a short list of who does what.")
