# Custom ChatUI At Home
# 1. Introduction
LLM chatbots are all the rage now a days.  I feel like it would be fun to make my own chat bot.

# 2. The LLM
This project WILL NOT focus on the LLM itself.  I didn't train my own LLM. I don't have the hardware or knowledge to pull of such a feat.

## 2.1. Setup
### Server
AMD 9xxxX3D

64GB of Ram

RTX5090

Ubuntu 24.04

### Client
Windows PC

## 2.2. Server
Despite such beefy of a machine.  I'm still choking on both d and v ram, besides since I wanted to access the chatbot remotely in the future anyways.  I decided to have this machine as a dedicated LLM server that I can just run all the time and other devices can access its services via LAN. 

## 2.3. VLLM
The server utilises vllm for the llm processing.  I have tried OpenUI beforehand but it keeps crashing with ComfyUI api, something i want to tinker with later on the line.  Besides i can't fully customise it for future development.  Meanwhile vllm still uses OpenAI style operations and it only takes 1 shell script to launch the entire thing without much effort.

While the current script allow model switching to vllms found on HuggingFace, it has so many bugs at the moment that it just default runs openAI's chatGPT 4.0 model.
# 2. Client UI
Despite the whole project being about AI chatbots, the AI part is all ready made and simple to set up.  What really was the focus, at least at this point of the project, is the HMI to use the AI chatbot.  The client UI is what sends messages to the LLM server with specific instructions and configs and relays whatever response it gets to the user in a clear and organised manner.  The app was written with Qt6 and programmed in Python.

## 2.1. Main Chat Window
The Main interface of the client UI. With Top bar tools to create new chats, edit current chat settings, set llm server ip, and edit messages.

The main area is a window that shows all the interaction between the user and the bot.  The interactions are timestamped with response times recorded while the texts support markdown formats for enhanced readability.  The bottom bar allows multi row text input.  So code pasting is feasible for self sustained vibe coding development.  There are also 2 pop up side bars on the sides for chat selection list and image browser respectively, with edit and delete message functions, the UI functions really similar to modern chatbot applications on the market.

## 2.2. Chat Creation and Settings
The user can create new chats or edit existing ones with functions to name the chats, switch the bot LLM, and select bot settings, allowing users to hotswap models and presets for the chat to handle different tasks in various scenarios.

## 2.3. Bot Creation and Settings
The user can drag and drop images to give the bot a bit more personality together with the text description window for more specific and detailed context.  The bot also supports workflow.json for ComfyUI image generation, but that feature is still under development.  This setting is seperated from the chat settings page such that the same bot with the same personality can be use across multiple chats without manually doing all the settings every time.

# 2.4. Save Files
Since the LLM server is only for processing text only. All of the settings and logs are saved by the client UI, hence more work was done on the UI itself instead of the LLM.  The UI Saves the bot settings separately as previously mentioned.  Meanwhile, each chat was saved as a massive .json file, which includes data such as the save path of the bot settings, visible chat history, invisible chat history, model name, llm temperature, etc.  Everytime the UI loads a chat, the .temp.json updates the order of the chats such that the chat list can reflect the most recently interacted chats for user convenience and organisation.  Meanwhile, the program follows the bot save path set in the chat.json to obtain the bot presets and combines it with the invisible chat history.  The user only sees the visible version of the chat history in the chat window, with all the presets and invisible instructions returned by the bot being hidden in the full version.  The 2 chat logs would then be updated with the latest user message to be sent as a .json to the llm and be reflected onto the chat window.  The program also asks the llm to summarize previous chat logs and compresses the payload chat log to minimise tokens used and optimise the LLM's work load.

# 3. Future Works
I have packaged the program into a .exe app that can connect to my server machine on the same LAN.  It's now functioning enough that I can ask it to code its own code for me :).

The server only supports manual LLM switching due to my inability to code, which I'm currently tackling.  There are also tons of minor bugs but I don't plan on dealing with them right away as they don't impact the program as longs as I don't deliberately try to break it.

Right now the chatbot does not support image and file uploads, I am currently working on such functions to add multi modality to the bot for future expansion.  Some features I would like to include would be TTS and STT to streamline the chat flow.  I really hope to integrate this bot to my future projects.
