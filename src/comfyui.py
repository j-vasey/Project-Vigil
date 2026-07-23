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


def get_workflows_dir() -> str:
    """Returns the absolute path to the workflows folder, ensuring it exists."""
    wf_dir = os.path.join(os.getcwd(), "workflows")
    os.makedirs(wf_dir, exist_ok=True)
    return wf_dir

def validate_workflow_json(content: dict) -> tuple:
    """
    Validates if a parsed dictionary matches ComfyUI's API prompt graph format.
    API format requires a dictionary mapping node IDs (strings) to objects containing 'class_type' and 'inputs'.
    """
    if not isinstance(content, dict) or not content:
        return False, "JSON content must be a non-empty object."
    
    if "nodes" in content and "links" in content:
        return False, "This appears to be a ComfyUI UI format save. Please export using ComfyUI's 'Save (API Format)' setting."
        
    has_valid_node = False
    for node_id, node_data in content.items():
        if isinstance(node_data, dict) and "class_type" in node_data and "inputs" in node_data:
            has_valid_node = True
            break
            
    if not has_valid_node:
        return False, "Invalid ComfyUI API workflow format. Expected node dictionary with 'class_type' and 'inputs'."
        
    return True, "Valid ComfyUI API format workflow."

def list_available_workflows(active_filename: str = "") -> list:
    """
    Lists all available ComfyUI workflow JSON files in the workflows directory.
    """
    wf_dir = get_workflows_dir()
    workflows = []
    built_in = {"default_sd15.json", "vigil_api.json"}
    
    for filename in sorted(os.listdir(wf_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(wf_dir, filename)
            node_count = 0
            is_valid = False
            title = filename[:-5].replace("_", " ").title()
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    valid, _ = validate_workflow_json(data)
                    if valid:
                        is_valid = True
                        node_count = len(data)
            except Exception:
                pass
                
            workflows.append({
                "filename": filename,
                "name": title,
                "is_active": (filename.lower() == active_filename.lower()),
                "is_custom": filename.lower() not in built_in,
                "node_count": node_count,
                "is_valid": is_valid
            })
            
    return workflows


class ComfyUIClient:
    """
    Client wrapper for ComfyUI's prompt API.
    Provides automated Stable Diffusion workflows and E2E image retrieval.
    """

    def __init__(
        self, 
        base_url: str = "http://localhost:8188", 
        backend: str = "mock", 
        ckpt_name: str = "v1-5-pruned-emaonly.safetensors",
        workflow_filename: str = "default_sd15.json"
    ):
        self.base_url = base_url.rstrip("/")
        self.backend = backend.lower()
        self.ckpt_name = ckpt_name
        self.workflow_filename = workflow_filename or "default_sd15.json"

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

        logger.info(f"[ComfyUI Client] Submitting prompt: '{prompt_text}' to ComfyUI at {self.base_url} (workflow: {self.workflow_filename})...")
        client_id = str(uuid.uuid4())
        
        # Resolve workflow template file
        wf_dir = get_workflows_dir()
        target_path = os.path.join(wf_dir, self.workflow_filename)
        if not os.path.exists(target_path):
            target_path = os.path.join(os.getcwd(), self.workflow_filename)
        if not os.path.exists(target_path):
            target_path = os.path.join(os.getcwd(), "Vigil_API.json")
        if not os.path.exists(target_path):
            target_path = os.path.join(wf_dir, "default_sd15.json")

        workflow = None
        if os.path.exists(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    workflow = json.load(f)
                logger.info(f"[ComfyUI Client] Loaded workflow template from {target_path}")
                
                # Parameterize workflow nodes dynamically
                rand_seed = random.randint(1, 1125899906842624)
                prompt_updated = False
                ckpt_updated = False
                seed_updated = False
                
                for node_id, node in workflow.items():
                    if not isinstance(node, dict) or "inputs" not in node:
                        continue
                    inputs = node.get("inputs", {})
                    class_type = str(node.get("class_type", ""))
                    meta_title = str(node.get("_meta", {}).get("title", ""))
                    
                    # 1. Update positive text prompt
                    if "text" in inputs and (class_type == "CLIPTextEncode" or "prompt" in meta_title.lower() or "positive" in meta_title.lower()):
                        if not prompt_updated:
                            inputs["text"] = prompt_text
                            prompt_updated = True
                    elif "positive" in inputs and isinstance(inputs["positive"], str):
                        inputs["positive"] = prompt_text
                        prompt_updated = True
                        
                    # 2. Update checkpoint model
                    if "ckpt_name" in inputs and ("checkpointloader" in class_type.lower() or "checkpoint" in meta_title.lower()):
                        inputs["ckpt_name"] = self.ckpt_name
                        ckpt_updated = True
                        
                    # 3. Update random seed
                    if "seed" in inputs and not isinstance(inputs["seed"], list):
                        inputs["seed"] = rand_seed
                        seed_updated = True
                    elif "noise_seed" in inputs and not isinstance(inputs["noise_seed"], list):
                        inputs["noise_seed"] = rand_seed
                        seed_updated = True
                
                # Fallback to specific node IDs if dynamic matching didn't update prompt/checkpoint/seed
                if not prompt_updated:
                    if "5" in workflow and "inputs" in workflow["5"]:
                        workflow["5"]["inputs"]["positive"] = prompt_text
                    elif "1280" in workflow and "inputs" in workflow["1280"]:
                        workflow["1280"]["inputs"]["positive"] = prompt_text
                    elif "6" in workflow and "inputs" in workflow["6"]:
                        workflow["6"]["inputs"]["text"] = prompt_text
                        
                if not ckpt_updated:
                    if "1" in workflow and "inputs" in workflow["1"] and "ckpt_name" in workflow["1"]["inputs"]:
                        workflow["1"]["inputs"]["ckpt_name"] = self.ckpt_name
                    elif "4" in workflow and "inputs" in workflow["4"] and "ckpt_name" in workflow["4"]["inputs"]:
                        workflow["4"]["inputs"]["ckpt_name"] = self.ckpt_name

                if not seed_updated:
                    if "10" in workflow and "inputs" in workflow["10"]:
                        workflow["10"]["inputs"]["seed"] = rand_seed
                    elif "3" in workflow and "inputs" in workflow["3"]:
                        workflow["3"]["inputs"]["seed"] = rand_seed
                        
            except Exception as e:
                logger.error(f"[ComfyUI Client] Failed to parse custom workflow JSON at {target_path}: {e}. Falling back to default.")
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
