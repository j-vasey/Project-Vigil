import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Any, Optional

logger = logging.getLogger("project_vigil.mcp.client")

class MCPClient:
    """
    Asynchronous JSON-RPC 2.0 client for communicating with an MCP server subprocess over stdio.
    """
    def __init__(self, name: str, command: List[str]):
        self.name = name
        self.command = command
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.tools: List[Dict[str, Any]] = []
        self.initialized = False
        self.is_healthy = False

    async def start(self):
        try:
            logger.info(f"[MCP Client - {self.name}] Starting process with command: {self.command}")
            import sys
            # On Windows suppress the console pop-up for each MCP subprocess
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs
            )
            self._read_task = asyncio.create_task(self._read_stdout_loop())
            self._stderr_task = asyncio.create_task(self._read_stderr_loop())
            
            # Perform MCP protocol initialization handshake
            await self._initialize_handshake()
            self.is_healthy = True
            logger.info(f"[MCP Client - {self.name}] Successfully initialized and online.")
        except Exception as e:
            logger.exception(f"[MCP Client - {self.name}] Failed to start or initialize server: {e}")
            self.is_healthy = False
            await self.stop()

    async def _initialize_handshake(self):
        # 1. Send initialize request
        await self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vigil-mcp-client", "version": "1.0.0"}
        })
        # 2. Send initialized notification (which expects no response)
        await self.send_notification("notifications/initialized", {})
        self.initialized = True
        
        # 3. Retrieve and cache available tools
        await self.refresh_tools()

    async def refresh_tools(self):
        if not self.initialized:
            return
        resp = await self.send_request("tools/list", {})
        self.tools = resp.get("tools", [])
        logger.info(f"[MCP Client - {self.name}] Cached {len(self.tools)} tools.")

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.process or self.process.returncode is not None:
            raise RuntimeError(f"MCP server '{self.name}' process is not running.")
            
        req_id = self._next_id
        self._next_id += 1
        
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future
        
        packet = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        
        payload = json.dumps(packet) + "\n"
        self.process.stdin.write(payload.encode("utf-8"))
        await self.process.stdin.drain()
        
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            raise e

    async def send_notification(self, method: str, params: Dict[str, Any]):
        if not self.process or self.process.returncode is not None:
            raise RuntimeError(f"MCP server '{self.name}' process is not running.")
            
        packet = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        payload = json.dumps(packet) + "\n"
        self.process.stdin.write(payload.encode("utf-8"))
        await self.process.stdin.drain()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

    async def _read_stdout_loop(self):
        try:
            while self.process and self.process.returncode is None:
                line = await self.process.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                    if "id" in msg:
                        req_id = msg["id"]
                        future = self._pending_requests.pop(req_id, None)
                        if future and not future.done():
                            if "error" in msg:
                                future.set_exception(Exception(msg["error"].get("message", "Unknown error")))
                            else:
                                future.set_result(msg.get("result", {}))
                except Exception as e:
                    logger.error(f"[MCP Client - {self.name}] JSON-RPC parse error: {e} (Raw: {line_str})")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"[MCP Client - {self.name}] stdout reader error: {e}")
            self.is_healthy = False

    async def _read_stderr_loop(self):
        try:
            while self.process and self.process.returncode is None:
                line = await self.process.stderr.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if line_str:
                    logger.warning(f"[MCP Server Stderr - {self.name}] {line_str}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[MCP Client - {self.name}] stderr reader error: {e}")

    async def stop(self):
        logger.info(f"[MCP Client - {self.name}] Stopping client process...")
        self.is_healthy = False
        if self._read_task:
            self._read_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
            
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass
        logger.info(f"[MCP Client - {self.name}] Client process stopped.")


class MCPManager:
    """
    Manages lifecycle and tool routing for all four local local MCP servers.
    """
    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}
        import sys
        if getattr(sys, 'frozen', False):
            # If running as a compiled PyInstaller executable, run itself with --mcp-server argument
            exe_path = sys.executable
            self._server_definitions = {
                "filesystem": [exe_path, "--mcp-server", "filesystem"],
                "scheduler": [exe_path, "--mcp-server", "scheduler"],
                "m365-calendar": [exe_path, "--mcp-server", "m365-calendar"],
                "active-memory": [exe_path, "--mcp-server", "active-memory"]
            }
        else:
            # Fallback to local python venv execution
            python_exe = os.path.join(os.getcwd(), "venv", "Scripts", "python.exe")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
                
            self._server_definitions = {
                "filesystem": [python_exe, "-m", "src.mcp.servers.filesystem"],
                "scheduler": [python_exe, "-m", "src.mcp.servers.scheduler"],
                "m365-calendar": [python_exe, "-m", "src.mcp.servers.calendar"],
                "active-memory": [python_exe, "-m", "src.mcp.servers.memory"]
            }

    async def start_all(self):
        logger.info("[MCP Manager] Launching all local MCP servers...")
        for name, cmd in self._server_definitions.items():
            client = MCPClient(name, cmd)
            self.clients[name] = client
            asyncio.create_task(client.start())

    async def stop_all(self):
        logger.info("[MCP Manager] Stopping all local MCP servers...")
        await asyncio.gather(*(client.stop() for client in self.clients.values()), return_exceptions=True)
        self.clients.clear()

    def get_status(self) -> List[Dict[str, Any]]:
        status_list = []
        for name, client in self.clients.items():
            status_list.append({
                "name": name,
                "status": "online" if client.is_healthy else "offline",
                "tools": client.tools
            })
        return status_list

    async def call_mcp_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        client = self.clients.get(server_name)
        if not client:
            return f"Error: MCP Server '{server_name}' is not registered."
        if not client.is_healthy:
            return f"Error: MCP Server '{server_name}' is offline."
            
        try:
            resp = await client.call_tool(tool_name, arguments)
            content = resp.get("content", [])
            text_blocks = [block.get("text", "") for block in content if block.get("type") == "text"]
            return "\n".join(text_blocks)
        except Exception as e:
            logger.error(f"[MCP Manager] Tool execution error '{tool_name}' on '{server_name}': {e}")
            return f"Error executing tool '{tool_name}' on server '{server_name}': {str(e)}"

# Global singleton manager instance
mcp_manager = MCPManager()
