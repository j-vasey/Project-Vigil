import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("project_vigil.memory.context_manager")


def estimate_token_count(text: str) -> int:
    """
    Heuristic token counter (~4 characters per token for standard English text).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


class ContextManager:
    """
    Token-budgeted Context Manager for Project Vigil.
    Manages context window limits, token budgeting, and triggers dynamic summarization.
    """

    def __init__(self, num_ctx: int = 8192, threshold_pct: float = 0.75):
        self.num_ctx = num_ctx
        self.threshold_tokens = int(num_ctx * threshold_pct)

    def calculate_prompt_tokens(self, system_prompt: str, history_turns: List[Dict[str, str]], prompt_body: str) -> int:
        """
        Calculates estimated total token usage for a prompt package.
        """
        total_text = system_prompt + "\n" + prompt_body
        for turn in history_turns:
            total_text += f"\n{turn.get('role', '')}: {turn.get('content', '')}"
        return estimate_token_count(total_text)

    def should_summarize(self, total_tokens: int) -> bool:
        """
        Determines whether the conversation context requires summarization to fit within budget.
        """
        summarize_needed = total_tokens >= self.threshold_tokens
        if summarize_needed:
            logger.warning(
                f"[ContextManager] Token count {total_tokens} exceeded threshold ({self.threshold_tokens}/{self.num_ctx}). "
                "Triggering history compression."
            )
        return summarize_needed

    def trim_history_to_budget(self, history_turns: List[Any], max_tokens: int) -> List[Any]:
        """
        Trims older conversation turns to keep prompt token count strictly within specified max_tokens.
        """
        trimmed = []
        accumulated_tokens = 0
        for item in reversed(history_turns):
            text = getattr(item, "text", str(item))
            tokens = estimate_token_count(text)
            if accumulated_tokens + tokens > max_tokens:
                break
            trimmed.insert(0, item)
            accumulated_tokens += tokens
        return trimmed
