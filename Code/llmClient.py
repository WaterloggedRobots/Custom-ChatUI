import json
import threading
import requests
import time
from PySide6.QtCore import Signal, QObject
from pathlib import Path
import shutil
import os

from enum import Enum

# ======================
# CONFIG
# ======================
#IP = "192.168.0.247"
PORT = "8000"


MAX_CONTEXT_MESSAGES = 16
SUMMARY_TRIGGER_COUNT = 24
SUMMARY_MODEL_MAX_TOKENS = 512

class LLMArch(str, Enum):
    VLLM="VLLM"
    OLLAMA="Ollama"
    FAIL="not connected"

class LLMStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    FREE = "free"
    UPLOADING = "uploading"
    ANALYZING="analyzing"
    THINKING = "thinking"
    RESPONDING = "responding"
    ERROR = "error"
    SWITCHING_MODEL = "switching_model"


class LLMClient(QObject):
    token = Signal(str)
    done = Signal(str)
    error = Signal(str)
    statusNew = Signal(str)
    model_changed = Signal(str)


    def __init__(self,fd):
        super().__init__()
        self.model_name = None
        self.temperature = 0.7
        self.max_tokens = 512
        self.token_count =0

        self.directoryDefault = fd
        IP = self.read_json_file(str(self.directoryDefault) + r"\UI\config.json")
        self.ip = IP["ip"]
        self.llm = self.get_llm()
        self.port = PORT
        self.SERVER_URL = f"http://{self.ip}:{self.port}"
        #self.VLLM_URL = f"http://{self.ip}:{self.port}/v1/chat/completions"
        #self.MODELS_URL = f"http://{self.ip}:{self.port}/v1/models"
        #self.ADMIN_URL = f"http://{self.ip}:9000"
        #self.ADMIN_URL = f"http://{self.ip}:9000/api"  #vllm api server url

        self.preset = ""                 # system role, immutable
        self.messages = []               # visible chat
        self.payload_messages = []       # full payload

        self.current_response = ""
        self.lock = threading.Lock()

        self._abort_flag = False
        self.status = LLMStatus.NOT_CONNECTED
        if self.ping:
            #print("ping")
            self._set_status(LLMStatus.FREE)
        else:
            #print("aww")
            self._set_status(LLMStatus.NOT_CONNECTED)

    def _set_status(self, status: LLMStatus):
        self.status = status
        self.statusNew.emit(str(status.value))

    def get_status(self):
        return str(self.status.value)

    def read_json_file(self, file_path):
        """Read a JSON file and return its contents"""
        try:
            # Convert to Path object for better handling
            json_path = Path(file_path)

            if not json_path.exists():
                print(f"❌ File not found in ChatUI: {file_path}")
                return None

            # Read the file
            with open(json_path, 'r', encoding='utf-8') as file:
                data = json.load(file)

            print(f"✅ JSON loaded successfully from: {json_path.name}")
            return data

        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON format: {e}")
            return None
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            return None

    # ----------------------
    # Configuration
    # ----------------------
    def set_model(self, model: str):
        self.model_name = model

    def set_preset(self, text: str):
        self.preset = text.strip()

    def ping(self):
        try:
            r = requests.post(self.SERVER_URL + f"/", timeout=300)
            r.raise_for_status()
            return True
        except Exception as e:
            self.error.emit(str(e))
            return False

    # ----------------------
    # Chat API
    # ----------------------
    def add_user_message(self, text: str):
        with self.lock:
            msg = {"role": "user", "content": text}
            self.messages.append(msg)
            self.payload_messages.append(msg)

    def generate(self):
        if not self.model_name:
            self.error.emit("No model selected")
            return

        self._set_status(LLMStatus.THINKING)
        self._abort_flag = False
        self.current_response = ""
        self.token_count = 0
        """
        if self.preset:
            if not self.payload_messages or self.payload_messages[0]["role"] != "system":
                self.payload_messages.insert(0, {
                    "role": "system",
                    "content": self.preset
                })
        """

        threading.Thread(
            target=self._stream_request,
            daemon=True
        ).start()

    # ----------------------
    # Context logic
    # ----------------------
    def _build_payload(self, msgs = None):
        payload = []

        # 1️⃣ Preset (never summarized)
        if self.preset:
            payload.append({
                "role": "system",
                "content": self.preset
            })

        # 2️⃣ Summarize ONLY payload_messages
        if msgs is None:
            msgs = list(self.payload_messages)
        #print("payload Messages: ",msgs)
        if len(msgs) > SUMMARY_TRIGGER_COUNT:
            to_summarize = self._trim_for_summary(msgs[:-MAX_CONTEXT_MESSAGES])
            summary = self._summarize(to_summarize)
            payload.append({
                "role": "system",
                "content": f"Conversation summary:\n{summary}"
            })
            payload.extend(msgs[-MAX_CONTEXT_MESSAGES:])
        else:
            payload.extend(msgs)
        #print(payload)
        return payload

    def _trim_for_summary(self, messages, max_chars=12000):
        total = 0
        trimmed = []
        for msg in reversed(messages):
            total += len(msg["content"])
            if total > max_chars:
                break
            trimmed.insert(0, msg)
        return trimmed

    def _summarize(self, messages):
        """Summarize conversation using the same logic as generate()"""
        try:
            # Validate messages format first
            if not messages or not isinstance(messages, list):
                print(f"❌ Invalid messages format: {type(messages)}")
                return ""

            # Ensure each message has proper format
            validated_messages = []
            for msg in messages:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    validated_messages.append(msg)
                else:
                    print(f"❌ Skipping invalid message: {msg}")
                    continue

            if not validated_messages:
                print("❌ No valid messages to summarize")
                return ""

            # Build payload EXACTLY like generate() - use num_predict
            payload = {
                "model": self.model_name,
                "messages": [
                                {
                                    "role": "system",
                                    "content": "Summarize the conversation concisely. Focus on key points and context."
                                }
                            ] + validated_messages,  # Use list concatenation, not spreading
                "stream": False,
                "temperature": 0.3,
                "num_predict": SUMMARY_MODEL_MAX_TOKENS  # CHANGED: Use num_predict
            }

            print(f"📤 Summarizing {len(validated_messages)} messages...")
            print(f"Payload preview: {str(payload)[:200]}...")

            # Make request
            response = requests.post(
                f"{self.SERVER_URL}/v1/chat/completions",
                json=payload,
                timeout=30
            )

            # Handle response
            if response.status_code == 200:
                result = response.json()
                print(f"🔍 Response keys: {list(result.keys())}")

                # Extract summary (OpenAI format)
                summary = ""
                if "choices" in result and len(result["choices"]) > 0:
                    choice = result["choices"][0]
                    if "message" in choice:
                        summary = choice["message"].get("content", "")
                    elif "text" in choice:
                        summary = choice.get("text", "")

                if summary:
                    print(f"✅ Summary: {summary[:100]}...")
                    return summary
                else:
                    print("⚠️ Summary returned empty content")
                    # Try to get raw response for debugging
                    print(f"Full response: {json.dumps(result)[:500]}")
                    return ""
            else:
                print(f"❌ Summary failed: {response.status_code}")
                if response.text:
                    print(f"Response: {response.text[:500]}")
                return ""

        except Exception as e:
            print(f"❌ Summarization error: {e}")
            import traceback
            traceback.print_exc()
            return ""

    # ----------------------
    # Streaming
    # ----------------------
    def _stream_request(self, messages=None):
        """Generate streaming response"""

        # Use provided messages or build from context
        if messages is not None:
            payload = {
                "model": self.model_name,
                "messages": messages,
                "stream": True,
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        else:
            payload = {
                "model": self.model_name,
                "messages": self._build_payload(),
                "stream": True,
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }

        try:
            with requests.post(
                    self.SERVER_URL + f"/v1/chat/completions",
                    json=payload,
                    stream=True,
                    timeout=600
            ) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if self._abort_flag:
                        return

                    if not line:
                        continue

                    if line.strip() == "data: [DONE]":
                        break

                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    usage = chunk.get("usage", {})

                    if usage:
                        self.token_count += int(usage.get("completion_tokens", 0))

                    content = delta.get("content")
                    if content:
                        if self.status != LLMStatus.RESPONDING:
                            self._set_status(LLMStatus.RESPONDING)
                        self.current_response += content
                        self.token.emit(content)

        except Exception as e:
            self.error.emit(str(e))
            self._set_status(LLMStatus.ERROR)
            return

        # Only add to history if this is final response (no messages param)
        if messages is None:
            with self.lock:
                msg = {"role": "assistant", "content": self.current_response}
                self.messages.append(msg)
                self.payload_messages.append(msg)

            self.done.emit(self.current_response)
            self._set_status(LLMStatus.FREE)


    # ----------------------
    # Persistence helpers
    # ----------------------
    def export_payload(self):
        """Full payload sent to LLM (including preset)."""
        payload = []
        if self.preset:
            payload.append({
                "role": "system",
                "content": self.preset
            })
        payload.extend(self.payload_messages)
        return payload


    def import_payload(self, payload):
        """Restore payload from disk."""
        self.messages.clear()
        self.payload_messages.clear()
        #self.preset = ""

        for msg in payload:
            if msg["role"] == "system" and not self.preset:
                self.preset = msg["content"]
            else:
                self.payload_messages.append(msg)
                if msg["role"] in ("user", "assistant"):
                    self.messages.append(msg)

    # ----------------------
    # Switch Models
    # ----------------------
    def request_model_switch(self, model_name: str):
        payload = {"model": model_name,
                   "parameters": {
                       "temperature": self.temperature,
                       "num_ctx": 16384,
                       "top_p": 0.95,
                   },
                   "system_prompt": "",
                   "keep_alive": -1
                   }
        try:
            r = requests.post(
                self.SERVER_URL +f"/v1/models/switch",
                json=payload,
                timeout=5
            )
        except requests.exceptions.ReadTimeout:
            print("Model switch request timed out")

        #r.raise_for_status()
    """
    #vllm api
        def request_model_switch(self, model_name: str):
        r = requests.post(
            self.ADMIN_URL,
            params={"model": model_name},
            timeout=5
        )
        r.raise_for_status()
    """
    def abort_generation(self):
        self._abort_flag = True


    def wait_for_server_ready(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(self.SERVER_URL + f"/v1/models", timeout=2)
                if r.ok:
                    return True
            except requests.RequestException:
                self._set_status(LLMStatus.NOT_CONNECTED)
            time.sleep(1)
        raise RuntimeError("LLM server did not come back online")


    def switch_model(self, model_name: str):
        self._set_status(LLMStatus.SWITCHING_MODEL)
        self.abort_generation()
        self.request_model_switch(model_name)
        self.wait_for_server_ready()
        #reset server context
        self.model_name = model_name
        self._set_status(LLMStatus.FREE)


    def get_model(self):
        r = requests.get(self.SERVER_URL+f"/v1/models/active", timeout=2)
        q = r.json()
        print(q)
        model_name = q['active_model']
        #print(model_name)
        return model_name


    def get_model_list(self):
        r = requests.get(self.SERVER_URL+f"/v1/models", timeout=2)
        q = r.json()
        #print(q)
        model_name_list = []
        for i in range(len(q['data'])):
            model_name_list.append(q['data'][i]["id"])
        #print(model_name_list)
        return model_name_list


    def get_llm(self):
        try:
            r= requests.get(self.SERVER_URL+f"/type", timeout=2)
            q=r.json()
        except Exception as e:
            try:
                r = requests.get(self.ADMIN_URL + f"/type", timeout=2)
                q = r.json()
            except Exception as e:
                return LLMArch.FAIL

        response = LLMArch.OLLAMA if q['type']=='ollama' else LLMArch.VLLM
        return response


    def analyze_image_nas(self, file_path: str, prompt: str = "Describe this image", nas_path: str = None, chat_name: str = None):
        if nas_path and chat_name:
            dest_path = nas_path + f'/' + Path(file_path).name
            if not os.path.exists(dest_path) or not os.path.samefile(file_path, dest_path):
                shutil.copy(file_path, dest_path)
            self._set_status(LLMStatus.UPLOADING)
            start_time= time.time()
            upload_timeout = 10

            while not os.path.exists(dest_path):
                if time.time() - start_time >= upload_timeout:
                    print("Uploading failed")
                    self._set_status(LLMStatus.FREE)
                    return None
                time.sleep(0.1)
            print("upload successful")
            # Construct Linux path for the server
            linux_path = f"/mnt/Shared/LLM/Version 004/Save/Chat/{chat_name}/Input/{Path(file_path).name}"

            payload = {
                'file_path': linux_path,
                'prompt': prompt
                # 'model': 'llama3.2-vision:latest'  # Optional
            }

            try:
                self._set_status(LLMStatus.ANALYZING)
                response = requests.post(
                    f"{self.SERVER_URL}/image/analyze_from_path",
                    json=payload,
                    timeout=120
                )

                if response.status_code == 200:
                    result = response.json()
                    #print(f"Analysis complete: {result}")
                    return result.get('description')
                else:
                    self.error.emit(f"Analysis failed: {response.status_code} - {response.text}")
                    return None

            except Exception as e:
                self.error.emit(f"Error: {str(e)}")
                return None
        return  None

    def analyze_file_nas(self, file_path: str, prompt: str = "Describe this image", nas_path: str = None, chat_name: str = None):
        if nas_path and chat_name:
            dest_path = nas_path + f'/' + Path(file_path).name
            if not os.path.exists(dest_path) or not os.path.samefile(file_path, dest_path):
                shutil.copy(file_path, dest_path)
            self._set_status(LLMStatus.UPLOADING)
            start_time= time.time()
            upload_timeout = 10

            while not os.path.exists(dest_path):
                if time.time() - start_time >= upload_timeout:
                    print("Uploading failed")
                    self._set_status(LLMStatus.FREE)
                    return None
                time.sleep(0.1)
            print("upload successful")
            # Construct Linux path for the server
            linux_path = f"/mnt/Shared/LLM/Version 004/Save/Chat/{chat_name}/Input/{Path(file_path).name}"

            payload = {
                'file_path': linux_path,
                'prompt': prompt
                # 'model': 'llama3.2-vision:latest'  # Optional
            }

            try:
                self._set_status(LLMStatus.ANALYZING)
                response = requests.post(
                    f"{self.SERVER_URL}/file/process_from_path",
                    json=payload,
                    timeout=120
                )

                if response.status_code == 200:
                    result = response.json()
                    #print(f"Analysis complete: {result}")
                    return result.get('content')
                else:
                    self.error.emit(f"Analysis failed: {response.status_code} - {response.text}")
                    return None

            except Exception as e:
                self.error.emit(f"Error: {str(e)}")
                return None
        return  None

    def web_search(self, user_input):

        # Get search results
        #search_results = self.web_search2(user_input)
        print("web search: ", user_input)
        response = requests.post(
            f"{self.SERVER_URL}/web/search",
            data={"query":user_input,"num_results":5},
            timeout=120
        )

        # Format results for LLM
        context = "Here are search results:\n"
        if response.status_code == 200:
            result = response.json()
            for r in result["results"]:
                context += f"- {r['title']}: {r['snippet']}\n"

        return context

    def web_browse(self, url, summarize = True):
        print("web browse: ", url)
        response = requests.post(
            f"{self.SERVER_URL}/web/browse",
            data={"url":url,"summarize":summarize},
            timeout=120
        )
        if response.status_code == 200:
            result = response.json()
        """
            
        # Check if user wants to browse
        #elif user_input.startswith("/browse "):
            url = user_input[8:].strip()

            # Browse the URL
            

            return f"**{page_content['title']}**\n\n{page_content['summary']}"
        
        """

    def run_cot_step(self, instruction: str) -> str:
        """
        Run a single Chain of Thought step.
        BLOCKS until complete, returns the CoT output.
        This does NOT affect visible chat history.
        """
        self._set_status(LLMStatus.THINKING)
        self._abort_flag = False

        # Create CoT message
        cot_msg = {"role": "user", "content": instruction}

        # Store current state to restore later
        original_messages = self.messages.copy()
        original_payload = self.payload_messages.copy()

        # Add CoT to payload ONLY
        with self.lock:
            self.payload_messages.append(cot_msg)

        # Set up synchronization
        self._cot_complete = threading.Event()
        self._cot_complete.clear()
        self._cot_response = ""

        # Build payload with ONLY the CoT message
        payload = {
            "model": self.model_name,
            "messages": [cot_msg],
            "stream": True,
            "temperature": self.temperature,
            "num_predict": self.max_tokens
        }

        def _run():
            try:
                with requests.post(
                        self.SERVER_URL + f"/v1/chat/completions",
                        json=payload,
                        stream=True,
                        timeout=600
                ) as r:
                    for line in r.iter_lines(decode_unicode=True):
                        if self._abort_flag:
                            return

                        if not line or line.strip() == "data: [DONE]":
                            continue

                        if line.startswith("data:"):
                            data = line[5:].strip()
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    self._cot_response += content
                                    # Use QMetaObject to safely emit from thread
                                    from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                                    QMetaObject.invokeMethod(
                                        self,
                                        "emit_token",
                                        Qt.QueuedConnection,
                                        Q_ARG(str, content)
                                    )
                            except:
                                pass
            except Exception as e:
                print(f"CoT error: {e}")
            finally:
                self._cot_complete.set()

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()

        # BLOCK until CoT completes
        if not self._cot_complete.wait(timeout=60):
            print("⚠️ CoT step timed out")
            self._cot_response = ""

        # Restore original state
        with self.lock:
            self.messages = original_messages
            self.payload_messages = original_payload

        self._set_status(LLMStatus.FREE)
        return self._cot_response

    # Add this method to LLMClient for thread-safe signal emission
    def emit_token(self, text):
        """Thread-safe token emission"""
        self.token.emit(text)

        # Temporary signal handlers
        def on_cot_token(text):
            self._cot_response += text
            # Optionally print for debugging
            # print(f"🧠 CoT: {text}", end="")

        def on_cot_done(full_text):
            self._cot_response = full_text
            self._cot_complete.set()

        # Connect temporary handlers
        self.token.connect(on_cot_token)
        self.done.connect(on_cot_done)

        # Build payload with ONLY the CoT message
        payload = {
            "model": self.model_name,
            "messages": [cot_msg],  # Just the CoT instruction
            "stream": True,
            "temperature": self.temperature,
            "num_predict": self.max_tokens
        }

        # Start generation in a thread
        def _run():
            try:
                with requests.post(
                        self.SERVER_URL + f"/v1/chat/completions",
                        json=payload,
                        stream=True,
                        timeout=600
                ) as r:
                    for line in r.iter_lines(decode_unicode=True):
                        if self._abort_flag:
                            return

                        if not line or line.strip() == "data: [DONE]":
                            continue

                        if line.startswith("data:"):
                            data = line[5:].strip()
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    self._cot_response += content
                                    self.token.emit(content)  # Still emit for debugging
                            except:
                                pass
            except Exception as e:
                print(f"CoT error: {e}")
            finally:
                self.done.emit(self._cot_response)

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()

        # BLOCK until CoT completes (or timeout)
        if not self._cot_complete.wait(timeout=60):
            print("⚠️ CoT step timed out")
            self._cot_response = ""

        # Disconnect temporary handlers
        self.token.disconnect(on_cot_token)
        self.done.disconnect(on_cot_done)

        # Restore original state (remove CoT from payload)
        with self.lock:
            self.messages = original_messages
            self.payload_messages = original_payload

        self._set_status(LLMStatus.FREE)
        return self._cot_response

    def generate_final_response(self, user_message: str):
        """
        Generate the final user-facing response.
        NON-BLOCKING - streams to UI.
        """
        self._set_status(LLMStatus.THINKING)
        self._abort_flag = False
        self.current_response = ""
        self.token_count = 0

        # Add the actual user message to history
        with self.lock:
            msg = {"role": "user", "content": user_message}
            self.messages.append(msg)
            self.payload_messages.append(msg)

        # Start streaming response
        threading.Thread(
            target=self._stream_request,
            daemon=True
        ).start()