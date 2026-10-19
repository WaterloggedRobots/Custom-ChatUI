import json

from absl.logging import exception
from ollama import chat

model = "functiongemma"

toolsDefault=[
    {
        "type": "function",
        "function": {
            "name": "post_mercury",
            "description": "Use this tool only when the user wants to change the machine state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "status must be exactly one of these three values in '': -'on'     for: turn"
                                       "on, start, power on, switch on \n-'off'    for: turn off, shut down, "
                                       "power off, switch off \n-'restart' for: restart, reboot, reset, "
                                       "recycle. Never use 'on' for restart/reboot requests."
                    }
                },
                "required": ["status"]
            }
        }
    }
]

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

tools = toolsDefault


def ToolsReset():
    global tools
    tools= toolsDefault

def ToolsAdd(tool):
    tools.append(tool)

def ToolsRemove(tool):
    try:
        tools.remove(tool)
    except exception as e:
        return e
    return 22

def ToolCall(prompt: str):

    messages = [
        {
            "role": "developer",
            "content": "You are a model that can do function calling with the following functions"
        },
        {"role": "user", "content": prompt}
    ]

    response = chat(model=model, messages=messages, tools=tools)

    if response.message.tool_calls:
        toolSelect =[]
        for tc in response.message.tool_calls:
            print(f"→ TOOL CALL: {tc.function.name}({tc.function.arguments or {}})")
            toolSelect.append({"tool": tc.function.name, "args": tc.function.arguments})
        return toolSelect
    else:
        print("→ NO TOOL CALL")
        return None

if __name__ == "__main__":
    for test in tests:
        ToolCall(test)