import llmClient
import os

current_script_path = os.path.abspath(__file__)
parent_directory = os.path.dirname(current_script_path)

client = llmClient.LLMClient(parent_directory)

print(client.get_model())