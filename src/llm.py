import logging
from abc import ABC, abstractmethod
import httpx
import xml.etree.ElementTree as ET
from src.tools.search import search_web_tool

logger = logging.getLogger("project_vigil.llm")

async def web_search(query: str) -> str:
    """
    Perform a live web search using the registered search tool.
    """
    from src.tools.registry import tool_registry
    return await tool_registry.execute("web_search", {"query": query})


def parse_prompt_to_messages(prompt: str, system_prompt: str = "") -> list:
    """
    Parses conversation dialogue history (multi-turn logs with 'User:' and 'Companion:')
    into structured message objects suitable for Ollama /api/chat.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
        
    lines = prompt.strip().split("\n")
    current_role = None
    current_content = []
    
    for line in lines:
        if line.startswith("User:"):
            if current_role:
                messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
            current_role = "user"
            current_content = [line[5:].strip()]
        elif line.startswith("Companion:") or line.startswith("System:"):
            if current_role:
                messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
            current_role = "assistant" if line.startswith("Companion:") else "system"
            content_start = 10 if line.startswith("Companion:") else 7
            current_content = [line[content_start:].strip()]
        else:
            if current_role:
                current_content.append(line)
            else:
                current_role = "user"
                current_content.append(line)
                
    if current_role and current_content:
        content_str = "\n".join(current_content).strip()
        if not (current_role == "assistant" and content_str == ""):
            messages.append({"role": current_role, "content": content_str})
            
    if not messages or (len(messages) == 1 and system_prompt):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
    # Extract base64 image attachments and format for Ollama vision support
    import re
    for msg in messages:
        if msg.get("role") == "user" and "content" in msg:
            content_str = msg["content"]
            image_match = re.search(r"\[IMAGE_ATTACHMENT:\s*([A-Za-z0-9+/=]+)\]", content_str)
            if image_match:
                b64_data = image_match.group(1)
                clean_content = re.sub(r"\[IMAGE_ATTACHMENT:\s*([A-Za-z0-9+/=]+)\]", "", content_str).strip()
                msg["content"] = clean_content
                msg["images"] = [b64_data]
        
    return messages

class BaseLLMClient(ABC):
    """
    Abstract Base Class representing an LLM backend API connector.
    """

    @abstractmethod
    async def generate_response(self, prompt: str, system_prompt: str = "") -> str:
        """
        Sends prompts to the LLM backend and extracts the completion response.

        Args:
            prompt: User/context prompt input.
            system_prompt: System-level constraints or identity instructions.

        Returns:
            str: Generated text response.
        """
class MockLLMClient(BaseLLMClient):
    """
    Mock LLM implementation for local environment verification.
    Generates dummy responses without requiring network endpoints.
    """

    async def generate_response(self, prompt: str, system_prompt: str = "") -> str:
        logger.info(f"[MockLLM] Mock response generated for prompt: {prompt[:30]}...")
        # Check for image trigger in the prompt to allow automated testing of ComfyUI
        if "trigger image" in prompt.lower():
            return "[IMAGE: a futuristic coding assistant] Here is your generated photo!"
            
        # Parse the latest user message out of multi-turn conversation logs
        # E.g. prompt ends with: "User: <message>\nCompanion:"
        clean_input = prompt.strip()
        if "User:" in clean_input:
            parts = clean_input.split("User:")
            latest_part = parts[-1].strip()
            # Strip trailing "Companion:" tag
            if latest_part.endswith("Companion:"):
                latest_part = latest_part[:-len("Companion:")].strip()
            clean_input = latest_part
            
        # Simulate processing time slightly
        return f"[Simulated Companion Response]\nThank you for sharing that! (System prompt: '{system_prompt[:25]}...'). I processed your input: '{clean_input[:50]}...'"


class OllamaClient(BaseLLMClient):
    """
    Connector for Ollama API backend. 
    Supports standard Ollama /api/chat endpoint with native function/tool calling.
    """

    def __init__(self, base_url: str, model: str, num_ctx: int = 8192):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    async def generate_response(self, prompt: str, system_prompt: str = "") -> str:
        url = f"{self.base_url}/api/chat"
        messages = parse_prompt_to_messages(prompt, system_prompt)
        
        from src.tools.registry import tool_registry
        tools = tool_registry.get_schemas()
        
        last_tool_name = None
        last_tool_args = None
        accumulated_contents = []
        
        for iteration in range(3):
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "tools": tools,
                "options": {
                    "num_ctx": self.num_ctx
                }
            }
            logger.info(f"[Ollama Client] Sending chat request (iteration {iteration+1}, model '{self.model}') to {url}...")
            try:
                import json
                async with httpx.AsyncClient() as client:
                    async with client.stream("POST", url, json=payload, timeout=300.0) as response:
                        if response.status_code != 200:
                            await response.aread()
                            err_msg = f"Ollama API error {response.status_code}: {response.text}"
                            logger.error(err_msg)
                            return f"[Ollama Backend Error] Status {response.status_code}"
                            
                        full_content = ""
                        tool_calls = []
                        
                        print(f"[Ollama Generating (Iter {iteration+1})]: ", end="", flush=True)
                        
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                assistant_message = data.get("message", {})
                                
                                # Extract content chunks and print dots
                                chunk = assistant_message.get("content", "")
                                if chunk:
                                    full_content += chunk
                                    print(".", end="", flush=True)
                                    
                                # Capture tool calls (usually sent in the final chunk)
                                if "tool_calls" in assistant_message and assistant_message["tool_calls"]:
                                    tool_calls = assistant_message["tool_calls"]
                            except json.JSONDecodeError:
                                pass
                                
                        print(" [Done]", flush=True)
                        
                    if full_content:
                        accumulated_contents.append(full_content.strip())
                        
                    if not tool_calls:
                        return full_content.strip()
                        
                    # Append assistant tool call message to history
                    messages.append({
                        "role": "assistant",
                        "content": full_content,
                        "tool_calls": tool_calls
                    })
                    logger.info(f"[Ollama Client] Model requested tool calls: {tool_calls}")
                    
                    for tool_call in tool_calls:
                        func_info = tool_call.get("function", {})
                        func_name = func_info.get("name")
                        func_args = func_info.get("arguments", {})
                        
                        # Deduplication Guard
                        if func_name == last_tool_name and func_args == last_tool_args:
                            logger.warning(f"[Ollama Client] Loop detected: exact same tool '{func_name}' called consecutively with arguments: {func_args}")
                            tool_result = "Error: You are in an execution loop. Immediately halt tool calls and summarize your status."
                        else:
                            last_tool_name = func_name
                            last_tool_args = func_args
                            tool_result = await tool_registry.execute(func_name, func_args)
                        
                        # Tool Response Wrapping
                        wrapped_content = f"{tool_result}\n\n[SYSTEM: Data successfully retrieved. Do not repeat this tool call. Synthesize your final text output now.]"
                        
                        messages.append({
                            "role": "tool",
                            "content": wrapped_content,
                            "name": func_name,
                            "tool_call_id": tool_call.get("id")
                        })
                            
            except Exception as e:
                logger.exception(f"[Ollama Client] Connection failed to {url}: {e}")
                return f"[Ollama Connection Error] Could not connect to {self.base_url}"
                
        # Fallback Graceful Terminate
        partial_text = "\n\n".join(accumulated_contents).strip()
        if not partial_text:
            partial_text = "I performed some automated system commands on your behalf."
        return f"{partial_text}\n\n*(Note: Some automated environment actions were truncated to prevent a tool loop)*"


class KoboldAIClient(BaseLLMClient):
    """
    Connector for KoboldAI / KoboldCPP API.
    Supports standard Kobold /api/v1/generate endpoint.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def generate_response(self, prompt: str, system_prompt: str = "") -> str:
        url = f"{self.base_url}/api/v1/generate"
        
        # Combine system prompt and user context using standard ChatML style format
        formatted_prompt = ""
        if system_prompt:
            formatted_prompt += f"<|system|>\n{system_prompt}\n"
        formatted_prompt += f"<|user|>\n{prompt}\n<|assistant|>\n"

        payload = {
            "prompt": formatted_prompt,
            "max_length": 250,
            "temperature": 0.7,
            "quiet": True
        }

        logger.info(f"[KoboldAI Client] Generating using Kobold API at {url}...")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=300.0)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        return results[0].get("text", "").strip()
                    return "[KoboldAI Backend Error] Received empty result list"
                else:
                    err_msg = f"KoboldAI API error {response.status_code}: {response.text}"
                    logger.error(err_msg)
                    return f"[KoboldAI Backend Error] Status {response.status_code}"
        except Exception as e:
            logger.exception(f"[KoboldAI Client] Connection failed to {url}: {e}")
            return f"[KoboldAI Connection Error] Could not connect to {self.base_url}"


def get_llm_client(backend: str, url: str, model: str, num_ctx: int = 8192) -> BaseLLMClient:
    """
    Factory helper to load the active LLM Client based on config values.
    """
    backend_lower = backend.lower()
    if backend_lower == "ollama":
        return OllamaClient(base_url=url, model=model, num_ctx=num_ctx)
    elif backend_lower in ("kobold", "koboldai"):
        return KoboldAIClient(base_url=url)
    else:
        logger.warning(f"LLM Backend '{backend}' unrecognized. Falling back to MockLLMClient.")
        return MockLLMClient()
