import re

def sanitize_agent_output(raw_text: str) -> str:
    """
    Sanitizes raw LLM output to strip internal engineering artifacts
    like <think> blocks and orphaned </think> tags, while preserving
    the natural conversational response text.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()
    
    # 1. Strip <think>...</think> reasoning blocks (Qwen3/Gemma4 thinking mode artifacts)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    
    # 2. Strip orphaned/unclosed <think> or </think> tags
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    
    # 3. Strip leading [Recalled Memories] injection header if it leaked into the output
    text = re.sub(r"^\[Recalled Memories\]:.*?(?:\n|$)", "", text, flags=re.IGNORECASE)
    
    # 4. Clean up wrapping structural quotes around the entire response
    text = text.strip()
    if len(text) >= 2:
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        
    return text.strip()
