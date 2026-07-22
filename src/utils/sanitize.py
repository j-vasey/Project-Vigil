import re

def sanitize_agent_output(raw_text: str) -> str:
    """
    Sanitizes raw LLM output to strip internal engineering artifacts
    like <think> blocks, unclosed thinking tags, raw tool call XML tags,
    and JSON payloads while preserving natural conversational response text.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()

    # 1. Strip <think>...</think> reasoning blocks (including unclosed tags at tail)
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Strip orphaned/unclosed <think> or </think> tags
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)

    # 3. Strip raw tool call tags e.g. <tool_call>...</tool_call> or <tool_response>...</tool_response>
    text = re.sub(r"<(?:tool_call|tool_response)>.*?(?:</(?:tool_call|tool_response)>|$)", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 4. Strip leading [Recalled Memories] injection header if it leaked into output
    text = re.sub(r"^\[Recalled Memories\]:.*?(?:\n|$)", "", text, flags=re.IGNORECASE)

    # 5. Clean up wrapping structural quotes around the response
    text = text.strip()
    if len(text) >= 2:
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()

    return text.strip()

