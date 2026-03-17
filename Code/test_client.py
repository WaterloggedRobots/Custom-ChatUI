import llmClient
import os
import time

current_script_path = os.path.abspath(__file__)
parent_directory = os.path.dirname(current_script_path)

client = llmClient.LLMClient(parent_directory)

if __name__ == "__main__":
# CONNECT FIRST - before any status changes happen
    print(client.get_model_list())
    client.request_model_switch("llama3.2-vision:latest")

    print(client.analyze_image_nas(r"C:\Users\seacr\OneDrive\Pictures\Screenshots\Screenshot 2026-02-20 134017.png","describe this image",r"\\192.168.0.236\PiShared\LLM\Version 004\Save\Chat\test save\Input", "test save"))