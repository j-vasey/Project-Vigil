import logging
from typing import Dict, Callable, Awaitable, List
from src.providers.base import BaseMessagingProvider
from src.models import InboundMessage
from src.utils.sanitize import sanitize_agent_output

logger = logging.getLogger("project_vigil.router")

class MessagingRouter:
    """
    Orchestrates messaging providers, routing outbound messages 
    and dispatching parsed inbound webhook payloads.
    """

    def __init__(self):
        self.providers: Dict[str, BaseMessagingProvider] = {}
        self.inbound_handlers: List[Callable[[InboundMessage], Awaitable[None]]] = []

    def register_provider(self, platform: str, provider: BaseMessagingProvider) -> None:
        """
        Registers a provider instance for a specific platform.

        Args:
            platform: The string identifier for the platform (e.g., 'mock', 'telegram').
            provider: An instance implementing BaseMessagingProvider.
        """
        platform_lower = platform.lower()
        self.providers[platform_lower] = provider
        logger.info(f"Registered provider for platform: '{platform_lower}'")

    def register_inbound_handler(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """
        Registers a callback handler for inbound messages. 
        When an inbound message is parsed, it will be dispatched to this handler.

        Args:
            handler: An async function that takes an InboundMessage as an argument.
        """
        self.inbound_handlers.append(handler)
        logger.info("Registered new inbound message handler.")

    def split_text(self, text: str, max_chars: int = 2000) -> List[str]:
        """
        Splits a text payload into clean chunks under the maximum character limit,
        preferring boundaries at paragraph breaks or sentence endings.
        """
        if len(text) <= max_chars:
            return [text]

        chunks = []
        remaining = text.strip()
        
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break
                
            # Attempt to split at the last paragraph break (\n\n) before max_chars
            split_idx = remaining.rfind("\n\n", 0, max_chars)
            
            # Fallback to single newline if no double newline
            if split_idx == -1:
                split_idx = remaining.rfind("\n", 0, max_chars)
                
            # Fallback to sentence/punctuation boundaries (. , ! , ? , ; , ,)
            if split_idx == -1:
                for separator in [". ", "! ", "? ", "; ", ", "]:
                    found = remaining.rfind(separator, 0, max_chars)
                    if found > split_idx:
                        split_idx = found + len(separator) - 1 # Keep the punctuation mark with the preceding sentence
                        
            # Absolute fallback: split at the last space before max_chars
            if split_idx == -1:
                split_idx = remaining.rfind(" ", 0, max_chars)
                
            # Hard split at max_chars if no whitespace or punctuation boundaries exist
            if split_idx == -1 or split_idx == 0:
                split_idx = max_chars
                
            chunk = remaining[:split_idx].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_idx:].strip()
            
        logger.info(f"Split long message of length {len(text)} into {len(chunks)} chunks under {max_chars} character limit.")
        return chunks

    async def send_message(self, platform: str, user_id: str, text: str) -> bool:
        """
        Routes and sends an outbound message using the registered platform provider.
        Splits text if it exceeds the platform character limit (Discord: 2000, Telegram: 4096).
        """
        platform_lower = platform.lower()
        provider = self.providers.get(platform_lower)
        if not provider:
            logger.error(f"Failed to route message: No provider registered for platform '{platform_lower}'")
            return False
            
        platform_max_chars = {
            "discord": 2000,
            "telegram": 4096,
            "mock": 4096,
            "twilio": 1600,
            "whatsapp": 1600
        }
        max_chars = platform_max_chars.get(platform_lower, 2000)

        sanitized_text = sanitize_agent_output(text)
        chunks = self.split_text(sanitized_text, max_chars=max_chars)
        success = True
        
        for idx, chunk in enumerate(chunks):
            logger.debug(f"Routing outbound message chunk {idx+1}/{len(chunks)} to provider '{platform_lower}' for user {user_id}")
            result = await self._send_with_fallback_split(provider, user_id, chunk)
            if not result:
                success = False
                
        return success

    async def _send_with_fallback_split(self, provider: BaseMessagingProvider, user_id: str, text: str, depth: int = 0) -> bool:
        """
        Attempts to send a message. If the provider returns False (e.g., due to length/markup limits or HTTP 400 Bad Request),
        splits the message in half and recursively attempts to send the two pieces.
        """
        if not text.strip():
            logger.warning("[Router] Attempted to send an empty message chunk via router. Skipping provider dispatch.")
            return True
            
        logger.debug(f"Attempting to send message of length {len(text)} (depth: {depth})")
        result = await provider.send_message(user_id, text)
        if result:
            return True
            
        # Stop splitting if we are too deep or the text is already very small
        if depth >= 3 or len(text) <= 100:
            logger.error(f"Message send failed and cannot be split further (length: {len(text)}, depth: {depth})")
            return False
            
        logger.warning(f"Outbound send failed (possibly due to length or markup constraints). Splitting text and retrying...")
        
        # Split text into two halves by finding a logical boundary near the middle
        half_len = len(text) // 2
        look_range = len(text) // 5
        start_look = max(0, half_len - look_range)
        end_look = min(len(text), half_len + look_range)
        
        chunk_area = text[start_look:end_look]
        split_idx = -1
        for sep in ["\n\n", "\n", ". ", " "]:
            found = chunk_area.rfind(sep)
            if found != -1:
                split_idx = start_look + found + len(sep) - 1
                break
                
        if split_idx == -1:
            split_idx = half_len
            
        part1 = text[:split_idx].strip()
        part2 = text[split_idx:].strip()
        
        logger.info(f"Fallback splitting failed chunk into Part 1 (len: {len(part1)}) and Part 2 (len: {len(part2)})")
        
        res1 = await self._send_with_fallback_split(provider, user_id, part1, depth + 1)
        res2 = await self._send_with_fallback_split(provider, user_id, part2, depth + 1)
        return res1 and res2

    async def handle_webhook(self, platform: str, raw_payload: dict) -> InboundMessage:
        """
        Receives raw webhook payloads from a specific platform, parses them using 
        the registered provider, and dispatches the resulting InboundMessage 
        to all registered inbound handlers.
        """
        platform_lower = platform.lower()
        provider = self.providers.get(platform_lower)
        if not provider:
            error_msg = f"No provider registered for platform '{platform_lower}' to parse webhook."
            logger.error(error_msg)
            raise KeyError(error_msg)

        # Parse the raw payload using the provider's custom extractor
        inbound_msg = await provider.parse_webhook_payload(raw_payload)
        
        # Dispatch to registered handlers
        for handler in self.inbound_handlers:
            try:
                await handler(inbound_msg)
            except Exception as e:
                logger.exception(f"Error executing inbound message handler: {e}")
                
        return inbound_msg

    async def send_image(self, platform: str, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Routes and sends an outbound image file using the registered platform provider.
        Splits captions exceeding 2000 characters and dispatches extra chunks as text.
        """
        platform_lower = platform.lower()
        provider = self.providers.get(platform_lower)
        if not provider:
            logger.error(f"Failed to route image: No provider registered for platform '{platform_lower}'")
            return False
            
        sanitized_caption = sanitize_agent_output(caption)
        chunks = self.split_text(sanitized_caption, max_chars=2000)
        first_chunk = chunks[0] if chunks else ""
        
        logger.debug(f"Routing outbound image with caption length {len(first_chunk)} to provider '{platform_lower}'")
        success = await provider.send_image(user_id, image_bytes, filename, first_chunk)
        
        if not success and first_chunk:
            # If photo with caption failed, retry sending empty photo and dispatching text separately
            logger.warning("Image send failed with caption. Retrying sending image with NO caption, and sending caption as separate text...")
            success = await provider.send_image(user_id, image_bytes, filename, "")
            if success:
                for chunk in chunks:
                    await self._send_with_fallback_split(provider, user_id, chunk)
                return True
                
        # Send remaining caption chunks as text messages
        if success and len(chunks) > 1:
            logger.info(f"Sending {len(chunks) - 1} remaining caption chunks as text messages.")
            for chunk in chunks[1:]:
                res = await self._send_with_fallback_split(provider, user_id, chunk)
                if not res:
                    success = False
                    
        return success

    async def send_job_result(self, platform: str, user_id: str, text: str, artifacts: List[str]) -> bool:
        """
        Combined package dispatch containing both textual report outputs
        and local generated artifact files (like ComfyUI images).
        """
        import os
        success = True
        images = [a for a in artifacts if a.lower().endswith((".png", ".jpg", ".jpeg"))]
        other_files = [a for a in artifacts if a not in images]
        
        # Dispatch images
        if images:
            for idx, img_path in enumerate(images):
                try:
                    if not os.path.exists(img_path):
                        logger.error(f"Image artifact path does not exist: {img_path}")
                        continue
                    with open(img_path, "rb") as f:
                        img_bytes = f.read()
                    caption = text if idx == 0 else ""
                    filename = os.path.basename(img_path)
                    res = await self.send_image(platform, user_id, img_bytes, filename, caption)
                    if not res:
                        success = False
                except Exception as e:
                    logger.exception(f"Error sending image artifact '{img_path}': {e}")
                    success = False
            
            # Dispatch non-image files if present
            if other_files:
                other_text = "Generated file artifacts:\n" + "\n".join(f"- {os.path.basename(f)}" for f in other_files)
                res = await self.send_message(platform, user_id, other_text)
                if not res:
                    success = False
        else:
            # Standard text dispatch if no image artifacts exist
            res = await self.send_message(platform, user_id, text)
            if not res:
                success = False
                
        return success
