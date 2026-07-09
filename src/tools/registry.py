import logging
import asyncio
import inspect
import re
import random
import json
import os
import xml.etree.ElementTree as ET
import httpx
from typing import Callable, Dict, List, Any, get_type_hints, Literal
from src.tools.search import search_web_tool

logger = logging.getLogger("project_vigil.tools.registry")

class ToolRegistry:
    """
    Registry for functions that can be dynamically converted into OpenAI-compatible
    JSON schemas and executed dynamically.
    """
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._schemas: List[Dict[str, Any]] = []

    def register(self, func: Callable) -> Callable:
        """Decorator to register a function as an LLM tool."""
        name = func.__name__
        self._tools[name] = func
        schema = self._generate_schema(func)
        self._schemas.append(schema)
        logger.info(f"[ToolRegistry] Registered tool: '{name}'")
        return func

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Returns the list of OpenAI-compatible function definitions, including dynamic MCP server tools."""
        schemas = list(self._schemas)
        try:
            from src.mcp.client import mcp_manager
            for status in mcp_manager.get_status():
                server_name = status["name"]
                for tool in status["tools"]:
                    # Sanitise MCP input schema — Gemma4 is strict about schema keys.
                    # Only allow type/properties/required; drop any extras like additionalProperties.
                    raw_schema = tool.get("inputSchema", {"type": "object", "properties": {}, "required": []})
                    clean_schema = {
                        "type": raw_schema.get("type", "object"),
                        "properties": raw_schema.get("properties", {}),
                    }
                    if "required" in raw_schema:
                        clean_schema["required"] = raw_schema["required"]
                    schemas.append({
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": f"[MCP: {server_name}] {tool.get('description', '')}",
                            "parameters": clean_schema
                        }
                    })
        except Exception as e:
            logger.error(f"[ToolRegistry] Failed fetching dynamic MCP schemas: {e}")
            
        return schemas

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool by name (routing to local functions or active MCP servers)."""
        if name in self._tools:
            func = self._tools[name]
            try:
                logger.info(f"[ToolRegistry] Executing tool '{name}' with arguments: {arguments}")
                if inspect.iscoroutinefunction(func):
                    result = await func(**arguments)
                else:
                    result = func(**arguments)
                return str(result)
            except Exception as e:
                logger.exception(f"[ToolRegistry] Execution error on tool '{name}': {e}")
                return f"Error executing tool '{name}': {str(e)}"
        else:
            # Route to dynamic MCP server if exposed
            try:
                from src.mcp.client import mcp_manager
                found_server = None
                for status in mcp_manager.get_status():
                    for tool in status["tools"]:
                        if tool["name"] == name:
                            found_server = status["name"]
                            break
                    if found_server:
                        break
                        
                if found_server:
                    logger.info(f"[ToolRegistry] Routing tool call '{name}' to MCP Server '{found_server}'")
                    return await mcp_manager.call_mcp_tool(found_server, name, arguments)
            except Exception as e:
                logger.error(f"[ToolRegistry] Error during dynamic MCP tool routing for '{name}': {e}")
                
            logger.warning(f"[ToolRegistry] Execution failed: Tool '{name}' not found anywhere.")
            return f"Error: Tool '{name}' is not registered."

    def _generate_schema(self, func: Callable) -> Dict[str, Any]:
        """Dynamically generates an OpenAI-compatible function schema from Python inspect signature."""
        sig = inspect.signature(func)
        doc = func.__doc__ or ""
        
        # Extract first line of docstring as the main description
        doc_lines = [line.strip() for line in doc.strip().split("\n") if line.strip()]
        description = doc_lines[0] if doc_lines else func.__name__
        
        properties = {}
        required = []
        type_hints = get_type_hints(func)
        
        for param_name, param in sig.parameters.items():
            if param_name in ["self", "cls"]:
                continue
                
            param_type = type_hints.get(param_name, str)
            
            # Map Python type to JSON Schema parameter type
            json_type = "string"
            if param_type == int:
                json_type = "integer"
            elif param_type == float:
                json_type = "number"
            elif param_type == bool:
                json_type = "boolean"
            elif param_type == list:
                json_type = "array"
            elif param_type == dict:
                json_type = "object"
                
            # Find parameter description in docstring
            param_desc = f"The {param_name} parameter."
            for line in doc_lines:
                if line.startswith(f"{param_name}:"):
                    param_desc = line[len(param_name)+1:].strip()
                    break
                elif f":param {param_name}:" in line:
                    param_desc = line.split(f":param {param_name}:")[-1].strip()
                    break
            
            properties[param_name] = {
                "type": json_type,
                "description": param_desc
            }
            
            # Extract Literal enum values if present
            if hasattr(param_type, "__metadata__") or str(param_type).startswith("typing.Literal") or str(param_type).startswith("typing_extensions.Literal"):
                if hasattr(param_type, "__args__"):
                    properties[param_name]["enum"] = list(param_type.__args__)
                    
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
                
        return {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }

# Global singleton tool registry
tool_registry = ToolRegistry()

# ----------------- Tools Registration -----------------

@tool_registry.register
async def web_search(query: str) -> str:
    """
    Search the internet for live, factual, or current-events information. Only call this tool when the user explicitly asks you to look something up online, search the web, or asks about real-time data (scores, news, weather, prices). Do NOT call for casual conversation or questions you already know the answer to.

    query: Strictly 3-5 keywords only. No conversational phrasing, no relative time words like 'today' or 'this week'. Example: 'England Mexico World Cup 2026 schedule'.
    """
    query_lower = query.lower()
    
    is_news_query = any(word in query_lower for word in ["news", "headline", "breaking", "article", "world event", "current event"])
    
    if is_news_query:
        rss_url = "https://feeds.bbci.co.uk/news/rss.xml"
        if "tech" in query_lower or "technology" in query_lower:
            rss_url = "https://feeds.bbci.co.uk/news/technology/rss.xml"
            
        logger.info(f"[Web Search Tool] News query detected. Fetching live RSS feed from: {rss_url}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(rss_url, timeout=10.0)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    channel = root.find("channel")
                    items = channel.findall("item")
                    
                    results = []
                    for item in items[:5]:
                        title = item.find("title").text
                        desc = item.find("description").text
                        link = item.find("link").text
                        results.append(f"Title: {title}\nURL: {link}\nSnippet: {desc}\n")
                        
                    if results:
                        return "\n".join(results)
        except Exception as e:
            logger.warning(f"[Web Search Tool] Failed to fetch news RSS feed: {e}. Falling back to search scraper.")

    logger.info(f"[Web Search Tool] Querying DuckDuckGo for: '{query}'")
    try:
        results = await search_web_tool(query)
        if "Web search returned no immediate direct answers." in results or not results.strip():
            return (
                f"Search query: '{query}' returned no results or was rate-limited by the provider. "
                "Do NOT attempt another web search query. Please explain to the user that the live search "
                "service is currently throttled or offline, and answer their question to the best of your "
                "pre-existing knowledge."
            )
        return results
    except Exception as e:
        logger.error(f"[Web Search Tool] Exception querying internal search tool: {e}")
        return f"Error executing search for query '{query}': {str(e)}"


async def _run_ps_command(cmd: str) -> tuple[int, str, str]:
    """Helper to run Windows PowerShell commands securely and asynchronously."""
    process = await asyncio.create_subprocess_exec(
        "powershell", "-NoProfile", "-Command", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="ignore").strip(),
        stderr.decode("utf-8", errors="ignore").strip()
    )


@tool_registry.register
async def get_weather(location: str) -> str:
    """
    Get current weather and short-term forecast for a specific location. Use this tool when the user asks about the weather, temperature, rain, or forecast for a city.
    """
    import httpx
    try:
        # First, geocode the location to get lat/lon
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo_response = await client.get(geo_url, params={"name": location, "count": 1, "language": "en", "format": "json"})
            geo_data = geo_response.json()
            
            if not geo_data.get("results"):
                return f"Could not find coordinates for location: '{location}'"
                
            lat = geo_data["results"][0]["latitude"]
            lon = geo_data["results"][0]["longitude"]
            loc_name = geo_data["results"][0].get("name", location)
            country = geo_data["results"][0].get("country", "")
            
            # Now fetch the weather
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                "timezone": "auto"
            }
            weather_response = await client.get(weather_url, params=params)
            weather_data = weather_response.json()
            
            if "current" not in weather_data:
                return f"Weather data not available for {loc_name}"
                
            current = weather_data["current"]
            temp = current.get("temperature_2m", "?")
            feels_like = current.get("apparent_temperature", "?")
            precip = current.get("precipitation", 0)
            wind = current.get("wind_speed_10m", "?")
            code = current.get("weather_code", 0)
            
            # WMO Weather interpretation codes
            weather_desc = {
                0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
                61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
                95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
            }.get(code, "Unknown conditions")
            
            return f"Weather in {loc_name}{', ' + country if country else ''}:\nCondition: {weather_desc}\nTemperature: {temp}°C (Feels like {feels_like}°C)\nPrecipitation: {precip}mm\nWind: {wind} km/h"
    except Exception as e:
        return f"Weather lookup failed: {e}"


@tool_registry.register
async def get_system_metrics() -> str:
    """
    Get current CPU usage percentage, total and free RAM, and C: drive disk space from the local Windows host. Call this when the user asks about system performance, memory, or storage.
    """
    logger.info("[ToolRegistry] Fetching host system metrics...")
    
    # Run CPU LoadPercentage query
    cpu_code, cpu_out, cpu_err = await _run_ps_command("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average")
    cpu_val = cpu_out if (cpu_code == 0 and cpu_out) else "0"
    
    # Run RAM Usage query
    mem_code, mem_out, mem_err = await _run_ps_command("Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory | ConvertTo-Json")
    
    # Run Disk storage space query
    disk_code, disk_out, disk_err = await _run_ps_command("Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\" | Select-Object Size, FreeSpace | ConvertTo-Json")
    
    mem_summary = "Unavailable"
    if mem_out:
        try:
            mem_data = json.loads(mem_out)
            total_mem = float(mem_data.get("TotalVisibleMemorySize", 0)) / 1024 / 1024
            free_mem = float(mem_data.get("FreePhysicalMemory", 0)) / 1024 / 1024
            used_mem = total_mem - free_mem
            mem_percent = (used_mem / total_mem) * 100 if total_mem > 0 else 0
            mem_summary = f"{used_mem:.1f} GB / {total_mem:.1f} GB ({mem_percent:.1f}% used)"
        except Exception as e:
            mem_summary = f"Error: {e}"

    disk_summary = "Unavailable"
    if disk_out:
        try:
            disk_data = json.loads(disk_out)
            total_disk = float(disk_data.get("Size", 0)) / 1024 / 1024 / 1024
            free_disk = float(disk_data.get("FreeSpace", 0)) / 1024 / 1024 / 1024
            used_disk = total_disk - free_disk
            disk_percent = (used_disk / total_disk) * 100 if total_disk > 0 else 0
            disk_summary = f"{used_disk:.1f} GB / {total_disk:.1f} GB ({disk_percent:.1f}% used, {free_disk:.1f} GB free)"
        except Exception as e:
            disk_summary = f"Error: {e}"

    try:
        cpu_float = float(cpu_val)
    except ValueError:
        cpu_float = 0.0

    return (
        f"--- Host System Metrics ---\n"
        f"CPU Usage: {cpu_float:.1f}%\n"
        f"RAM Memory: {mem_summary}\n"
        f"Storage (C:): {disk_summary}\n"
    )


@tool_registry.register
async def manage_hyperv_vm(
    vm_name: str, 
    action: Literal["start", "stop", "status"],
    computer_name: str = ""
) -> str:
    """
    Start, stop, or get the status of a Hyper-V virtual machine on this host or a remote host.

    vm_name: The exact name of the virtual machine (alphanumeric, hyphens, underscores, spaces).
    action: Action to perform — 'start', 'stop', or 'status'.
    computer_name: Optional. Remote Hyper-V host name or IP. Leave empty for local host.
    """
    # 1. Rigorous input validation to prevent command injection
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", vm_name):
        return "Error: Invalid VM name format. Only alphanumeric characters, spaces, hyphens, and underscores are allowed."
        
    if action not in ["start", "stop", "status"]:
        return "Error: Invalid action. Supported actions are 'start', 'stop', or 'status'."
        
    comp_param = ""
    if computer_name:
        if not re.match(r"^[a-zA-Z0-9\.\-]+$", computer_name):
            return "Error: Invalid computer name format. Only alphanumeric characters, dots, and hyphens are allowed."
        comp_param = f" -ComputerName '{computer_name}'"
        
    # 2. Select appropriate Hyper-V PowerShell commands
    if action == "start":
        cmd = f"Start-VM -Name '{vm_name}'{comp_param}"
    elif action == "stop":
        cmd = f"Stop-VM -Name '{vm_name}'{comp_param} -Force"
    else:  # status
        cmd = f"Get-VM -Name '{vm_name}'{comp_param} | Select-Object Name, State, CPUUsage, MemoryAssigned | ConvertTo-Json"
        
    logger.info(f"[ToolRegistry] Attempting VM action '{action}' on '{vm_name}'...")
    code, stdout, stderr = await _run_ps_command(cmd)
    
    if code != 0:
        err_lower = stderr.lower()
        if "permission" in err_lower or "authorization" in err_lower or "access is denied" in err_lower:
            return (
                f"Hyper-V management failed: Access Denied. The application process must be run as "
                f"Administrator or as a member of the 'Hyper-V Administrators' group to control Hyper-V VMs. "
                f"(Error detail: {stderr[:100]}...)"
            )
        if "cannot find" in err_lower or "does not exist" in err_lower:
            return f"Hyper-V Error: Virtual machine '{vm_name}' could not be found on this host."
            
        return f"Hyper-V Error (exit code {code}): {stderr}"
        
    if action == "status":
        if not stdout:
            return f"Virtual Machine '{vm_name}' status: Offline or Not found."
        return f"Virtual Machine '{vm_name}' status info:\n{stdout}"
        
    return f"Success: Virtual machine '{vm_name}' was successfully request-dispatched for '{action}'."


# Helper database config loader
def _load_db_config(key: str, default_val: str = "") -> str:
    try:
        from src.database import SessionLocal
        from src.repository import MessageRepository
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            val = repo.get_config(key, default_val)
            return val if val is not None else default_val
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[ToolRegistry] Could not load DB config for key '{key}': {e}")
        return default_val


@tool_registry.register
async def discover_local_infrastructure(subnet: str = "") -> str:
    """
    Scan the local network subnet to find online hosts and detect open management ports (SSH 22, WinRM 5985, RDP 3389). Use when the user asks to discover or map their home or office network.

    subnet: Optional subnet prefix (e.g. '192.168.1.'). Leave empty to auto-detect from local IP.
    """
    # 1. Resolve local prefix if subnet parameter is omitted
    prefix = subnet.strip()
    if not prefix:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.254.254.254', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '192.168.1.1'
        finally:
            s.close()
        parts = ip.split(".")
        if len(parts) == 4:
            prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        else:
            prefix = "192.168.1."
            
    # Validate prefix pattern to prevent malicious subnet range inputs
    if "/" in prefix:
        prefix = prefix.split("/")[0]
        
    # Ensure prefix ends with a dot and fits subnet pattern
    if not re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.$", prefix):
        parts = prefix.rstrip(".").split(".")
        if len(parts) >= 3:
            prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        else:
            return "Error: Invalid subnet prefix format. Please specify a prefix like '192.168.1.'"

    logger.info(f"[ToolRegistry] Initiating fast port scan on subnet prefix: '{prefix}'")
    
    async def check_port(ip: str, port: int, timeout: float = 0.3) -> bool:
        try:
            conn = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def check_ip(ip: str, sem: asyncio.Semaphore) -> dict:
        async with sem:
            ports = [22, 5985, 3389]
            results = await asyncio.gather(*(check_port(ip, p) for p in ports))
            
            if any(results):
                guessed_os = "Unknown"
                if results[0]:
                    guessed_os = "Linux/BSD"
                elif results[1] or results[2]:
                    guessed_os = "Windows"
                    
                return {
                    "ip": ip,
                    "guessed_os": guessed_os,
                    "status": "online"
                }
            return None

    # Limit concurrency with a semaphore
    semaphore = asyncio.Semaphore(100)
    tasks = [check_ip(f"{prefix}{i}", semaphore) for i in range(1, 255)]
    
    results = await asyncio.gather(*tasks)
    discovered = [r for r in results if r is not None]
    
    logger.info(f"[ToolRegistry] Scan complete. Discovered {len(discovered)} hosts.")
    return json.dumps(discovered)


@tool_registry.register
async def execute_linux_bsd_command(ip_address: str, command: str, ssh_key_path: str = "") -> str:
    """
    Run a read-only diagnostic command on a Linux or BSD machine over SSH. Only call when the user asks to check a specific Linux/BSD host's status.

    ip_address: IP address of the target Linux/BSD machine.
    command: The command to run. Allowed: uptime, free, df, top, uname, netstat, ss, systemctl, service.
    ssh_key_path: Optional path to the SSH private key file.
    """
    # 1. IP validation
    if not re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$", ip_address):
        return f"Error: Invalid IP address format: '{ip_address}'"
        
    # 2. Strict whitelist filtering for security
    allowed_commands = ["uptime", "free", "df", "top", "uname", "netstat", "ss", "systemctl", "service"]
    cmd_clean = command.strip()
    cmd_base = cmd_clean.split()[0] if cmd_clean else ""
    if cmd_base not in allowed_commands:
        return (
            f"Error: Command '{cmd_base}' is blocked by security policy. "
            f"Only diagnostic and telemetry commands are permitted: {allowed_commands}"
        )
        
    if not re.match(r"^[a-zA-Z0-9_\-\s]+$", cmd_clean):
        return "Error: Command contains forbidden shell control characters (e.g. ;, |, &, etc.)."
        
    # 3. Load connection configurations
    username = _load_db_config("ssh_username", "root")
    password = _load_db_config("ssh_password", "")
    private_key = _load_db_config("ssh_private_key", "")
    
    # 4. Resolve and validate runtime SSH Key Certificate path
    if ssh_key_path:
        resolved_path = os.path.abspath(os.path.expanduser(ssh_key_path))
        if not os.path.isfile(resolved_path):
            return f"SSH Error: SSH key/certificate file not found at '{ssh_key_path}'"
            
        # Security validation check on certificate file suffix/naming to prevent arbitrary system reads
        basename = os.path.basename(resolved_path).lower()
        is_key = (
            "id_rsa" in basename or 
            "id_ed25519" in basename or 
            "id_ecdsa" in basename or 
            basename.endswith(".pem") or 
            basename.endswith(".key") or
            ".ssh" in resolved_path
        )
        if not is_key:
            return "SSH Error: Target path is not a valid SSH key/certificate format (.pem, .key, id_rsa, id_ed25519, etc.)."
            
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                private_key = f.read()
        except Exception as e:
            return f"SSH Error: Failed to read key file from '{ssh_key_path}': {e}"
            
    # 5. Define SSH Execution callback for Thread Executor
    def _ssh_run():
        import io
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if private_key:
                key = None
                for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
                    try:
                        key = key_class.from_private_key(io.StringIO(private_key.strip()))
                        break
                    except Exception:
                        continue
                if not key:
                    raise ValueError("Could not parse private SSH key/certificate.")
                ssh.connect(ip_address, username=username, pkey=key, timeout=10.0)
            else:
                ssh.connect(ip_address, username=username, password=password, timeout=10.0)
                
            stdin, stdout, stderr = ssh.exec_command(cmd_clean, timeout=15.0)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="ignore")
            err = stderr.read().decode("utf-8", errors="ignore")
            return exit_status, out, err
        finally:
            ssh.close()

    logger.info(f"[ToolRegistry] Connecting SSH to {username}@{ip_address} executing '{cmd_clean}'...")
    try:
        code, out, err = await asyncio.to_thread(_ssh_run)
        if code != 0:
            return f"SSH Command returned error (exit code {code}):\n{err}"
        return out if out else "Command completed with no output."
    except Exception as e:
        logger.error(f"[ToolRegistry] SSH command failed: {e}")
        return f"SSH Connection to {ip_address} failed: {str(e)}"


@tool_registry.register
async def execute_windows_command(ip_address: str, powershell_script: str) -> str:
    """
    Run a read-only diagnostic PowerShell cmdlet on a remote Windows Server over WinRM. Only call when the user asks to check a specific Windows host's status.

    ip_address: IP address of the target Windows Server host.
    powershell_script: PowerShell cmdlet to run. Allowed: get-service, get-process, get-eventlog, get-content, get-vm.
    """
    # 1. IP validation
    if not re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$", ip_address):
        return f"Error: Invalid IP address format: '{ip_address}'"
        
    # 2. Strict whitelist filtering for security
    allowed_commands = ["get-service", "get-process", "get-eventlog", "get-content", "get-vm"]
    cmd_clean = powershell_script.strip().lower()
    cmd_base = cmd_clean.split()[0] if cmd_clean else ""
    if cmd_base not in allowed_commands:
        return (
            f"Error: Command '{cmd_base}' is blocked by security policy. "
            f"Only diagnostic cmdlets are permitted: {allowed_commands}"
        )
        
    if not re.match(r"^[a-zA-Z0-9_\-\s\|?$.\u007b\u007d\u0028\u0029\u003d\u0021\'\"]+$", powershell_script):
        return "Error: Command contains suspicious shell control characters."
        
    # 3. Load DB configs
    username = _load_db_config("winrm_username", "Administrator")
    password = _load_db_config("winrm_password", "")
    
    if not password:
        return "WinRM Error: winrm_password is not configured in Project Vigil database settings."
        
    def _winrm_run():
        import winrm
        session = winrm.Session(f"http://{ip_address}:5985/wsman", auth=(username, password), transport='ntlm')
        r = session.run_ps(powershell_script)
        return r.status_code, r.std_out.decode("utf-8", errors="ignore"), r.std_err.decode("utf-8", errors="ignore")

    logger.info(f"[ToolRegistry] Connecting WinRM to {username}@{ip_address} executing '{powershell_script}'...")
    try:
        code, out, err = await asyncio.to_thread(_winrm_run)
        if code != 0:
            return f"WinRM Command returned error (exit code {code}):\n{err}"
        return out if out else "Command completed with no output."
    except Exception as e:
        logger.error(f"[ToolRegistry] WinRM command failed: {e}")
        return f"WinRM Connection to {ip_address} failed: {str(e)}"

@tool_registry.register
async def view_screen() -> str:
    """
    Capture the user's primary desktop screen and analyze what they are currently doing. Use this tool when the user asks you to look at their screen, read something on their desktop, or asks what they are working on.
    """
    try:
        from PIL import ImageGrab
        from io import BytesIO
        import base64
        from src.llm import get_llm_client
        
        # 1. Capture Engine
        img = ImageGrab.grab()
        img = img.convert("RGB")
        img.thumbnail((1024, 1024))
        
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        b64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        backend = _load_db_config("llm_backend", "mock")
        url = _load_db_config("llm_url", "http://localhost:11434")
        model = _load_db_config("screen_memory_model", "llama3.2-vision")
        
        client = get_llm_client(backend=backend, url=url, model=model)
        
        system_prompt = "Analyze this screen capture of the user's desktop. Write a concise description of what they are looking at or working on."
        prompt = f"[IMAGE_ATTACHMENT: {b64_data}]\nPlease describe the contents of this screen."
        
        response_text = await client.generate_response(prompt=prompt, system_prompt=system_prompt)
        return response_text.strip()
    except Exception as e:
        logger.error(f"[ToolRegistry] Error capturing/analyzing screen: {e}")
        return f"Failed to capture or analyze the screen: {e}"
