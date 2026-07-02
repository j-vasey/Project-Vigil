import os
import re
import sqlite3
import asyncio
from src.mcp.servers.base import MCPServer

server = MCPServer("server-filesystem")

def get_workspace_paths() -> list:
    """Retrieves all allowed sandbox workspace paths from the SQLite configurations table."""
    try:
        from src.database import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM configurations WHERE key='workspace_path'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            raw_paths = row[0].split(",")
            paths = []
            for p in raw_paths:
                p_str = p.strip().strip("'\"")
                if p_str:
                    paths.append(os.path.abspath(p_str))
            if paths:
                return paths
    except Exception:
        pass
    return [os.path.abspath(os.getcwd())]

def resolve_safe_path(rel_path: str) -> str:
    """Resolves and enforces that the target path lies strictly inside at least one allowed workspace path."""
    base_dirs = get_workspace_paths()
    primary_base = base_dirs[0]
    
    # Check if target is an absolute path (or Windows drive path e.g. E:\)
    if os.path.isabs(rel_path) or (len(rel_path) > 1 and rel_path[1] == ":"):
        target_path = os.path.abspath(rel_path)
    else:
        target_path = os.path.abspath(os.path.join(primary_base, rel_path.lstrip("/\\")))
        
    allowed = False
    for base_dir in base_dirs:
        try:
            if os.path.commonpath([base_dir, target_path]) == base_dir:
                allowed = True
                break
        except ValueError:
            # Different drives
            pass
            
    if not allowed:
        raise PermissionError("Directory Traversal Blocked: Target path does not lie inside any allowed workspace paths.")
    return target_path

@server.register_tool(
    name="read_file",
    description="Read the text content of a file within the sandboxed workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path inside the workspace."}
        },
        "required": ["path"]
    }
)
async def read_file(path: str) -> str:
    safe_path = resolve_safe_path(path)
    if not os.path.isfile(safe_path):
        return f"Error: File '{path}' does not exist."
        
    with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

@server.register_tool(
    name="write_file",
    description="Create or overwrite a file with text content in the sandboxed workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path inside the workspace."},
            "content": {"type": "string", "description": "Text content to write."}
        },
        "required": ["path", "content"]
    }
)
async def write_file(path: str, content: str) -> str:
    safe_path = resolve_safe_path(path)
    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    with open(safe_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Success: File '{path}' written successfully."

@server.register_tool(
    name="list_directory",
    description="List files and directories in a specific sandboxed workspace folder path.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative directory path (use '.' for workspace root)."}
        },
        "required": ["path"]
    }
)
async def list_directory(path: str) -> str:
    safe_path = resolve_safe_path(path)
    if not os.path.isdir(safe_path):
        return f"Error: Directory '{path}' does not exist."
        
    entries = []
    for item in os.listdir(safe_path):
        full_item = os.path.join(safe_path, item)
        is_dir = os.path.isdir(full_item)
        size = os.path.getsize(full_item) if not is_dir else 0
        entries.append({
            "name": item,
            "type": "directory" if is_dir else "file",
            "size_bytes": size
        })
    import json
    return json.dumps(entries, indent=2)

if __name__ == "__main__":
    asyncio.run(server.run())
