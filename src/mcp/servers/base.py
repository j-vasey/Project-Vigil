import sys
import json
import asyncio
from typing import Dict, Callable, Any, List

class MCPServer:
    """
    Standard stdio-based MCP Server helper class parsing JSON-RPC 2.0 messages.
    """
    def __init__(self, name: str, version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, input_schema: Dict[str, Any]):
        """Decorator to register a tool function and schema definitions."""
        def decorator(func: Callable):
            self.tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": input_schema,
                "func": func
            }
            return func
        return decorator

    async def run(self):
        def _read_line():
            return sys.stdin.readline()

        while True:
            # Read sys.stdin asynchronously in a separate thread to bypass Windows proactor pipe limitations
            line = await asyncio.to_thread(_read_line)
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue
            
            try:
                msg = json.loads(line_str)
                await self._handle_message(msg)
            except Exception as e:
                sys.stderr.write(f"[MCPServer {self.name}] Stdio processing exception: {e}\n")
                sys.stderr.flush()

    async def _handle_message(self, msg: Dict[str, Any]):
        if "id" not in msg:
            # Notification or heartbeat, no response required
            return
            
        req_id = msg["id"]
        method = msg.get("method")
        params = msg.get("params", {})
        
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": self.name,
                        "version": self.version
                    }
                }
                self._send_response(req_id, result)
            elif method == "tools/list":
                tools_list = []
                for name, details in self.tools.items():
                    tools_list.append({
                        "name": details["name"],
                        "description": details["description"],
                        "inputSchema": details["inputSchema"]
                    })
                self._send_response(req_id, {"tools": tools_list})
            elif method == "tools/call":
                tool_name = params.get("name")
                args = params.get("arguments", {})
                
                if tool_name not in self.tools:
                    self._send_error(req_id, -32601, f"Tool '{tool_name}' is not registered.")
                    return
                    
                func = self.tools[tool_name]["func"]
                try:
                    if asyncio.iscoroutinefunction(func):
                        res_val = await func(**args)
                    else:
                        res_val = func(**args)
                    
                    self._send_response(req_id, {
                        "content": [
                            {"type": "text", "text": str(res_val)}
                        ],
                        "isError": False
                    })
                except Exception as ex:
                    # Return executing error message cleanly as content
                    self._send_response(req_id, {
                        "content": [
                            {"type": "text", "text": f"Error: {str(ex)}"}
                        ],
                        "isError": True
                    })
            else:
                self._send_error(req_id, -32601, f"Method '{method}' is not implemented.")
        except Exception as e:
            self._send_error(req_id, -32603, f"Internal error during execution: {str(e)}")

    def _send_response(self, req_id: int, result: Dict[str, Any]):
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    def _send_error(self, req_id: int, code: int, message: str):
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message
            }
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
