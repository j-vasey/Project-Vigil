import logging
import asyncio
import random
import uuid
import zlib
import struct
import json
import os
import httpx
from typing import Optional

logger = logging.getLogger("project_vigil.comfyui")

def _generate_placeholder_png(width: int = 256, height: int = 256) -> bytes:
    """Generates a valid 256x256 solid color PNG to avoid platform image processor failures."""
    png = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    png += struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data))
    
    # Solid teal color row
    row = b'\x00' + b'\x1a\x80\xa0\xff' * width
    raw_data = row * height
    compressed = zlib.compress(raw_data)
    
    png += struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', zlib.crc32(b'IDAT' + compressed))
    png += struct.pack('>I', 0) + b'IEND' + struct.pack('>I', zlib.crc32(b'IEND'))
    return png

MOCK_PNG_BYTES = _generate_placeholder_png(256, 256)


class ComfyUIClient:
    """
    Client wrapper for ComfyUI's prompt API.
    Provides automated Stable Diffusion workflows and E2E image retrieval.
    """

    def __init__(
        self, 
        base_url: str = "http://localhost:8188", 
        backend: str = "mock", 
        ckpt_name: str = "v1-5-pruned-emaonly.safetensors"
    ):
        self.base_url = base_url.rstrip("/")
        self.backend = backend.lower()
        self.ckpt_name = ckpt_name

    async def generate_image(self, prompt_text: str) -> Optional[bytes]:
        """
        Triggers text-to-image workflow inside ComfyUI.
        
        Args:
            prompt_text: Prompt instruction for the positive encoder.

        Returns:
            Optional[bytes]: Generated PNG file bytes, or placeholder PNG bytes on failure/mock.
        """
        if self.backend == "mock":
            logger.info(f"[ComfyUI Client] Returning mock placeholder PNG for prompt: '{prompt_text}'")
            return MOCK_PNG_BYTES

        logger.info(f"[ComfyUI Client] Submitting prompt: '{prompt_text}' to ComfyUI at {self.base_url}...")
        client_id = str(uuid.uuid4())
        
        # Load Vigil_API.json dynamically from workspace root or fall back to secondary templates / default Stable Diffusion workflow
        workflow = None
        workflow_path = "Vigil_API.json"
        if not os.path.exists(workflow_path):
            workflow_path = "API_Workflow.json"
        
        if os.path.exists(workflow_path):
            try:
                with open(workflow_path, "r", encoding="utf-8") as f:
                    workflow = json.load(f)
                logger.info(f"[ComfyUI Client] Loaded custom workflow template from {workflow_path}")
                
                # 1. Update Positive Prompt
                if "5" in workflow and "inputs" in workflow["5"]:
                    workflow["5"]["inputs"]["positive"] = prompt_text
                elif "1280" in workflow and "inputs" in workflow["1280"]:
                    workflow["1280"]["inputs"]["positive"] = prompt_text
                elif "6" in workflow and "inputs" in workflow["6"]:
                    workflow["6"]["inputs"]["text"] = prompt_text
                    
                # 2. Update Checkpoint Model
                if "1" in workflow and "inputs" in workflow["1"] and workflow["1"].get("class_type") == "CheckpointLoaderSimple":
                    workflow["1"]["inputs"]["ckpt_name"] = self.ckpt_name
                elif "190" in workflow and "inputs" in workflow["190"]:
                    workflow["190"]["inputs"]["ckpt_name"] = self.ckpt_name
                elif "4" in workflow and "inputs" in workflow["4"]:
                    workflow["4"]["inputs"]["ckpt_name"] = self.ckpt_name
                    
                # 3. Update Random Seed
                rand_seed = random.randint(1, 1125899906842624)  # 64-bit int max range for comfyui seeds
                if "10" in workflow and "inputs" in workflow["10"]:
                    workflow["10"]["inputs"]["seed"] = rand_seed
                elif "685" in workflow and "inputs" in workflow["685"]:
                    workflow["685"]["inputs"]["seed"] = rand_seed
                elif "3" in workflow and "inputs" in workflow["3"]:
                    workflow["3"]["inputs"]["seed"] = rand_seed
                    
            except Exception as e:
                logger.error(f"[ComfyUI Client] Failed to parse custom workflow JSON: {e}. Falling back to default.")
                workflow = None

        if workflow is None:
            logger.info("[ComfyUI Client] Compiling standard default Stable Diffusion text-to-image workflow.")
            workflow = {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "cfg": 8,
                        "denoise": 1,
                        "latent_image": ["5", 0],
                        "model": ["4", 0],
                        "seed": random.randint(1, 10000000),
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "steps": 20
                    }
                },
                "4": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                        "ckpt_name": self.ckpt_name
                    }
                },
                "5": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {
                        "batch_size": 1,
                        "height": 512,
                        "width": 512
                    }
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                        "clip": ["4", 1],
                        "text": prompt_text
                    }
                },
                "7": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                        "clip": ["4", 1],
                        "text": "bad quality, blurry, text, logo, low quality"
                    }
                },
                "8": {
                    "class_type": "VAEDecode",
                    "inputs": {
                        "samples": ["3", 0],
                        "vae": ["4", 2]
                    }
                },
                "9": {
                    "class_type": "SaveImage",
                    "inputs": {
                        "filename_prefix": "VigilOut",
                        "images": ["8", 0]
                    }
                }
            }

        payload = {
            "prompt": workflow,
            "client_id": client_id
        }

        try:
            async with httpx.AsyncClient() as client:
                # 1. POST prompt to ComfyUI queue
                url_prompt = f"{self.base_url}/prompt"
                resp = await client.post(url_prompt, json=payload, timeout=15.0)
                if resp.status_code != 200:
                    logger.error(f"[ComfyUI Client] Prompt submission failed (status {resp.status_code}): {resp.text}")
                    return MOCK_PNG_BYTES
                
                prompt_id = resp.json().get("prompt_id")
                logger.info(f"[ComfyUI Client] Prompt submitted. ID: {prompt_id}. Polling history...")
                
                # 2. Poll `/history/{prompt_id}` until completed
                url_history = f"{self.base_url}/history/{prompt_id}"
                max_attempts = 120 # Wait up to 120 seconds
                for _ in range(max_attempts):
                    await asyncio.sleep(1.0)
                    h_resp = await client.get(url_history)
                    if h_resp.status_code == 200:
                        h_data = h_resp.json()
                        if prompt_id in h_data:
                            logger.info(f"[ComfyUI Client] Generation finished for prompt {prompt_id}.")
                            outputs = h_data[prompt_id].get("outputs", {})
                            
                            # Prioritize the custom final save image nodes (26, 1313, 1245, 1277)
                            image_info_list = outputs.get("26", {}).get("images", [])
                            if not image_info_list:
                                image_info_list = outputs.get("1313", {}).get("images", [])
                            if not image_info_list:
                                image_info_list = outputs.get("1245", {}).get("images", [])
                            if not image_info_list:
                                image_info_list = outputs.get("1277", {}).get("images", [])
                            if not image_info_list:
                                image_info_list = outputs.get("9", {}).get("images", [])
                            
                            # Fallback: Loop and grab any available node containing images
                            if not image_info_list:
                                for node_id, node_out in outputs.items():
                                    if "images" in node_out:
                                        image_info_list = node_out["images"]
                                        break
                                        
                            if image_info_list:
                                filename = image_info_list[0].get("filename")
                                subfolder = image_info_list[0].get("subfolder", "")
                                img_type = image_info_list[0].get("type", "output")
                                
                                # 3. Fetch image bytes via `/view` endpoint
                                url_view = f"{self.base_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
                                logger.info(f"[ComfyUI Client] Fetching image from {url_view}")
                                img_resp = await client.get(url_view, timeout=30.0)
                                if img_resp.status_code == 200:
                                    return img_resp.content
                                else:
                                    logger.error(f"[ComfyUI Client] Image retrieval failed (status {img_resp.status_code})")
                            else:
                                logger.error("[ComfyUI Client] Output node contained no images.")
                            break
                else:
                    logger.error(f"[ComfyUI Client] Polling timed out waiting for prompt {prompt_id}")
        except Exception as e:
            logger.exception(f"[ComfyUI Client] Connection error during generation: {e}")
            
        logger.warning("[ComfyUI Client] Generation pipeline failed. Returning mock PNG placeholder.")
        return MOCK_PNG_BYTES
