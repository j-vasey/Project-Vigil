import os
import sys

# Establish a writable user-scoped AppData directory for all runtime writes (logs, certs, crash dumps)
base_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ProjectVigil')
os.makedirs(base_data_dir, exist_ok=True)

log_file_path = os.path.join(base_data_dir, 'project_vigil.log')
crash_file_path = os.path.join(base_data_dir, 'import_crash.txt')
stdout_log_path = os.path.join(base_data_dir, 'stdout.log')
stderr_log_path = os.path.join(base_data_dir, 'stderr.log')
cert_path = os.path.join(base_data_dir, 'cert.pem')
key_path = os.path.join(base_data_dir, 'key.pem')

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    # Only redirect stdout/stderr if we are NOT a child MCP server subprocess
    if not (len(sys.argv) > 2 and sys.argv[1] == "--mcp-server"):
        try:
            sys.stdout = open(stdout_log_path, "w", buffering=1)
        except BaseException:
            pass
        try:
            sys.stderr = open(stderr_log_path, "w", buffering=1)
        except BaseException:
            pass
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import time
    import threading
    import webbrowser
    import uvicorn
    import uvicorn.logging
    from pystray import Icon, Menu, MenuItem
    from PIL import Image, ImageDraw
    from src.main import generate_self_signed_cert
except BaseException as e:
    with open(crash_file_path, "w") as f:
        import traceback
        traceback.print_exc(file=f)
    sys.exit(1)

server = None
server_thread = None
is_running = False
icon = None

def get_server_url():
    # Retrieve port from env or database
    port = int(os.environ.get("PORT", 8001))
    cert_exists = os.path.exists(cert_path) and os.path.exists(key_path)
    scheme = "https" if cert_exists else "http"
    return f"{scheme}://127.0.0.1:{port}"

def check_ssl_certificates():
    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        try:
            generate_self_signed_cert(cert_path, key_path)
        except Exception as e:
            import logging
            logging.getLogger("project_vigil.tray").error(f"Failed to generate SSL cert: {e}")

def run_uvicorn():
    global server, is_running
    try:
        check_ssl_certificates()
        from src.main import app
        port = int(os.environ.get("PORT", 8001))
        use_ssl = os.path.exists(cert_path) and os.path.exists(key_path)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            ssl_keyfile=key_path if use_ssl else None,
            ssl_certfile=cert_path if use_ssl else None,
            log_level="info",
            log_config=None,
            loop="asyncio"
        )
        server = uvicorn.Server(config)
        is_running = True
        if icon:
            icon.update_menu()
        server.run()
    except BaseException as e:
        import traceback
        traceback.print_exc()
        import logging
        logging.getLogger("project_vigil.tray").error(f"Uvicorn start failed: {e}")
    finally:
        is_running = False
        if icon:
            icon.update_menu()

def start_server(icon_inst=None, item=None):
    global server_thread, is_running
    if not is_running:
        server_thread = threading.Thread(target=run_uvicorn, daemon=True)
        server_thread.start()

def stop_server(icon_inst=None, item=None):
    global server, is_running
    if is_running and server:
        server.should_exit = True

def open_dashboard(icon_inst=None, item=None):
    webbrowser.open(get_server_url())

def exit_app(icon_inst, item):
    global is_running
    stop_server()
    icon_inst.stop()
    # Force exit the process to ensure all child threads and MCP subprocesses terminate immediately
    os._exit(0)

def create_image():
    # Generates a premium dark purple/teal shield eye icon for Project Vigil
    image = Image.new('RGB', (64, 64), color=(15, 23, 42))
    dc = ImageDraw.Draw(image)
    # Shield shape points
    shield_pts = [(32, 8), (54, 18), (54, 40), (32, 56), (10, 40), (10, 18)]
    dc.polygon(shield_pts, fill=(30, 41, 59), outline=(139, 92, 246), width=3)
    # Eye circle inside the shield
    dc.ellipse([20, 20, 44, 44], outline=(167, 139, 250), width=3)
    dc.ellipse([27, 27, 37, 37], fill=(45, 212, 191)) # Pupil
    return image

def setup_tray():
    global icon
    
    # Pre-start the FastAPI server thread
    start_server()
    
    menu = Menu(
        MenuItem('Open Dashboard', open_dashboard, default=True),
        MenuItem(lambda item: 'Server Status: RUNNING' if is_running else 'Server Status: STOPPED', lambda: None, enabled=False),
        MenuItem('Start Server', start_server, visible=lambda item: not is_running),
        MenuItem('Stop Server', stop_server, visible=lambda item: is_running),
        MenuItem('Exit', exit_app)
    )
    
    icon = Icon("Project Vigil", create_image(), "Project Vigil - AI Companion", menu)
    icon.run()

if __name__ == "__main__":
    # Check if this is launched as a packaged subprocess to run one of the MCP servers
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--mcp-server":
        server_name = sys.argv[2]
        import asyncio
        try:
            if server_name == "filesystem":
                from src.mcp.servers.filesystem import server
                asyncio.run(server.run())
            elif server_name == "scheduler":
                from src.mcp.servers.scheduler import server
                asyncio.run(server.run())
            elif server_name == "m365-calendar":
                from src.mcp.servers.calendar import server
                asyncio.run(server.run())
            elif server_name == "active-memory":
                from src.mcp.servers.memory import server
                asyncio.run(server.run())
        except BaseException as e:
            with open(os.path.join(base_data_dir, "mcp_crash.txt"), "w") as f:
                import traceback
                traceback.print_exc(file=f)
        sys.exit(0)
        
    try:
        setup_tray()
    except BaseException as e:
        with open(os.path.join(base_data_dir, "tray_crash.txt"), "w") as f:
            import traceback
            traceback.print_exc(file=f)
