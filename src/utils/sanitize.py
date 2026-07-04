import re

def sanitize_agent_output(raw_text: str) -> str:
    """
    Sanitizes raw LLM output to strip internal engineering meta-cognition
    scratchpads (like 'thought', 'Plan:', and 'Response:' markers) and
    retains only the final conversational response dialogue payload.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()
    
    # Failsafe: strip bracketed system identifiers or raw log markers at the start of a response
    text = re.sub(r"^\[Recalled Memories\]:.*?(?:\n|$)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\[.*?\]:.*?(?:\n|$)", "", text)
    
    # 1. Look for explicit markers like "Response:" or "Output:"
    match = re.search(r"\b(?:Response|Output)\s*:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if match:
        extracted = match.group(1).strip()
    else:
        # If no explicit "Response:" marker, strip thought/Plan blocks individually if they exist
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE) # Strip orphaned tags
        cleaned = re.sub(r"\bthought\s*:\s*.*?\n\n", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"\bthought\s*\n.*?\n\n", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"\bthought\b.*?\n\n", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        
        # Strip Plan blocks
        cleaned = re.sub(r"\bPlan\s*:\s*.*?\n\n", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"\bPlan\s*\n.*?\n\n", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        extracted = cleaned.strip()

    # 2. Clean up any trailing/leading structural double/single quotes wrapped around the whole response
    if (extracted.startswith('"') and extracted.endswith('"')) or (extracted.startswith("'") and extracted.endswith("'")):
        inner = extracted[1:-1].strip()
        # Verify that we don't accidentally swallow unmatched quotes
        extracted = inner
        
    # Apply failsafe stripper to extracted text too (in case LLM placed markers inside Response: block)
    extracted = re.sub(r"^\[Recalled Memories\]:.*?(?:\n|$)", "", extracted, flags=re.IGNORECASE)
    extracted = re.sub(r"^\[.*?\]:.*?(?:\n|$)", "", extracted)
        
    return extracted.strip()
