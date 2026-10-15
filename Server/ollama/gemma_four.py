import json
from ollama import chat

model = "gemma4:31b"

toolsDefault = [
    {
        "type": "function",
        "function": {
            "name": "post_mercury",
            "description": (
                "Change the Mercury machine power state. "
                "Use ONLY when the user wants to turn it on, off, or restart. "
                "Do not use for weather, jokes, greetings, or general questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["on", "off", "restart"],
                        "description": (
                            "Exactly one of: "
                            "'on' for turn on / start / power on / switch on; "
                            "'off' for turn off / shut down / power off / switch off; "
                            "'restart' for restart / reboot / reset / recycle. "
                            "Never use 'on' for restart/reboot."
                        ),
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the live web. Use for current events, prices, docs, news, "
                "or facts that may have changed. Do not use for greetings, jokes, "
                "or Mercury power on/off."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "3-8 word search query, not the full user sentence.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "How many results to fetch. Default 5, max 8.",
                    },
                },
            },
        },
    }
]

SYSTEM = """You call tools to take actions. Do not invent tools.

Routing:
- Mercury power / start / stop / reboot → post_mercury
- Anything else (weather, jokes, chat, questions) → reply in text, no tool

Rules:
- status must be exactly on, off, or restart
- restart/reboot/reset → restart, never on
- If the request is not about Mercury power, do not call post_mercury
"""

tests = [
    "Turn on the Mercury machine",
    "Turn Mercury off",
    "Restart Mercury",
    "Reboot Mercury",
    "Start Mercury",
    "Shut down Mercury",
    "What is the weather today?",
    "Hello, how are you?",
    "Tell me a joke",
]

tools = list(toolsDefault)


def ToolsReset():
    global tools
    tools = list(toolsDefault)


def ToolsAdd(tool):
    tools.append(tool)


def ToolsRemove(tool):
    try:
        tools.remove(tool)
        return True
    except ValueError as e:
        return e


def ToolCall(prompt: str):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]

    response = chat(
        model=model,
        messages=messages,
        tools=tools,
        think=False,
        options={"temperature": 0.0},
    )

    if response.message.tool_calls:
        tool_select = []
        for tc in response.message.tool_calls:
            args = tc.function.arguments or {}
            print(f"→ TOOL CALL: {tc.function.name}({args})")
            tool_select.append({"tool": tc.function.name, "args": args})
        return tool_select

    print(f"→ NO TOOL CALL: {response.message.content}")
    return None


if __name__ == "__main__":
    for test in tests:
        print(f"\nUSER: {test}")
        ToolCall(test)