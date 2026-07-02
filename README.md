# 👁️ Project Vigil - Outbound AI Companion Gateway

Project Vigil is a self-hosted, 24/7 proactive AI companion system. Unlike typical chatbots, this system runs locally, maintains persistent conversation history in SQLite, and uses a background scheduler to autonomously initiate outbound pings to the user (via SMS, WhatsApp, or Telegram) based on randomized triggers. It also processes incoming user replies.

---

## 🌟 Core Features

*   **Resilient Decoupled Queue**: Webhooks instantly enqueue incoming payloads into an in-memory queue, ensuring Gateway connections never timeout during slow local LLM generations.
*   **Pseudo-Random Scheduler**: Initiates outreach based on configured check intervals, random sleep jitter offsets (to avoid mechanical patterns), DND window boundary validation, and probability checks.
*   **Multi-Model LLM Client**: Modular connectors for **Ollama** (including dynamic model selector tags and connection checks) and **KoboldAI**.
*   **Durable Stateful Multi-Agent System**: Long-running background jobs run asynchronously via `BackgroundAgentRunner`. Complete execution plans, states, and generated file assets/artifacts are serialized to SQLite `agent_job_state` after every turn.
*   **Contextual Companion Engine**: Enforces companion personalities dynamically in async workers by prepending custom system rules, guides, and user stress/behavioral history logs to all model prompts.
*   **Inline Memory Recall Tags**: Detects `[RECALL: query]` triggers, executes search commands directly against database tables, and substitutes results inline synchronously.
*   **ComfyUI Image Pipeline**: Detects `[IMAGE: positive prompt]` templates inside responses (either synchronously or via background tasks), runs ComfyUI workflows, and dispatches generated images alongside text captions.
*   **Local HTTPS & SSL Auto-Cert**: Generates self-signed certificates with SAN support (`localhost`, `127.0.0.1`, `172.16.1.123`) at startup. Serves both FastAPI and Vite dev server securely over local HTTPS.
*   **Sleek WebUI Control Plane**: A dark, premium React + Vite dashboard displaying real-time server logging (via SSE), configuration management, proactivity logs, and a manual message dispatch console.

---

## 🛠️ Setup & Installation

The application uses an isolated Python virtual environment (`venv`) containing all dependency frameworks pre-packaged.

### 1. Activate the Virtual Environment
Open a terminal in the root directory `e:\Dev\Project Vigil` and run:

**Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
.\venv\Scripts\activate.bat
```

**macOS/Linux (Bash/zsh):**
```bash
source venv/bin/activate
```

---

## 🚀 Starting the Application

To launch the FastAPI server and background workers, run Python with the module flag (`-m`) from the root directory:

```bash
python -m src.main
```

Once running:
*   **Control Plane Dashboard**: Open [http://127.0.0.1:8001](http://127.0.0.1:8001) in your web browser.
*   **Decoupled Worker**: Starts consumption of the in-memory queue.
*   **Proactivity Engine**: Begins scheduled outbound checks.

---

## ⚙️ Dashboard Controls

The dashboard is structured into four main operational columns:
1.  **Engine Health Switch**: Toggle between `ACTIVE` and `PAUSED` to temporarily mute background triggers and webhook replies.
2.  **Gateway Configuration**:
    *   *AI Response Backend*: Select between `Mock`, `Ollama`, or `KoboldAI`.
    *   *Ollama Integration*: Enter your API URL; the dashboard dynamically populates a dropdown list of available local models. Click **Test Connection to Model** to verify responsiveness.
    *   *Credentials*: Input access tokens (e.g. Telegram Bot Token).
    *   *Proactivity Parameters*: Adjust intervals, recipient user IDs, and Do-Not-Disturb (DND) window times.
3.  **Manual Outbound Message Card**: Directly dispatch custom text or image prompts to a specific platform user for testing, bypassing the background scheduler.
4.  **Real-Time Logging Terminal**: View system logs, debug info, warning triggers, and worker operations streamed live from the backend via Server-Sent Events (SSE).

---

## 🎨 Image Generation Workflow (ComfyUI)

To enable image creation:
1.  Ensure ComfyUI is active and configured (or set `comfyui_backend` to `mock` in SQLite/API parameters for testing).
2.  When responding to a user or setting system instructions, configure your LLM to output a tag in the format:
    `[IMAGE: a beautiful illustration of a cat on a laptop] Check this out!`
3.  The orchestrator captures this tag, runs the ComfyUI API workflow, downloads the file, and transmits it via `router.send_image` (with the tag removed and the remaining text used as the caption).

## 🛠️ Tool Registry & Home Lab Integration

Project Vigil includes a modular **Tool Registry** framework that allows OpenAI-compatible LLMs (like Ollama with native function calling) or simpler models (via tag-based orchestration) to interact with your local environment.

### Registered Tools
1.  **Web Search** (`web_search`): Queries live results (falling back to BBC News RSS feeds for news/headline queries to bypass search engine rate limits).
2.  **System Metrics** (`get_system_metrics`): Retrieves host system telemetry, including real-time CPU Usage, RAM utilization, and logical drive `C:` storage space.
3.  **VM Management** (`manage_hyperv_vm`): Runs PowerShell commandlets to control local or remote Hyper-V virtual machine states on the network.
    *   *Parameters*:
        *   `vm_name` (required): Target VM name (strictly sanitized to alphanumeric characters, spaces, hyphens, and underscores to prevent command-injection).
        *   `action` (required): Action to perform, restricted to `["start", "stop", "status"]`.
        *   `computer_name` (optional): IP address or hostname of a remote Hyper-V host (sanitized to prevent command-injection).
4.  **Network Discovery** (`discover_local_infrastructure`): Performs a fast, concurrent TCP port check (ports 22, 5985, 3389) across the host's subnet to locate active machines and guess their OS.
    *   *Parameters*:
        *   `subnet` (optional): Subnet prefix to scan (e.g. `192.168.1.`).
5.  **Linux/BSD SSH Execution** (`execute_linux_bsd_command`): Executes a diagnostic command natively on a Linux/BSD node over SSH.
    *   *Parameters*:
        *   `ip_address` (required): Target machine IP address.
        *   `command` (required): Diagnostic command (limited strictly to a security whitelist: `uptime`, `free`, `df`, `top`, `uname`, `netstat`, `ss`, `systemctl`, `service`).
        *   `ssh_key_path` (optional): Path to an SSH key/certificate file (e.g. `~/.ssh/id_rsa`).
6.  **Windows Server WinRM Execution** (`execute_windows_command`): Executes PowerShell diagnostic cmdlets natively on a Windows guest using WinRM.
    *   *Parameters*:
        *   `ip_address` (required): Target machine IP address.
        *   `powershell_script` (required): Diagnostic script cmdlet (limited strictly to a security whitelist: `get-service`, `get-process`, `get-eventlog`, `get-content`, `get-vm`).

### Execution Formats
*   **Native Tool Calling (Ollama)**: Dynamic JSON schemas are compiled directly from Python type-hints and sent to Ollama's `/api/chat` interface. The multi-turn tool loop executes automatically.
*   **Tag-Based Interception (Orchestrator)**: If the LLM generates a text tag, the orchestration kernel intercepts and runs it, feeding results back to the companion:
    *   `[SEARCH: search query]`
    *   `[SYSTEM_METRICS]`
    *   `[HYPERV_VM: vm_name, action]` or `[HYPERV_VM: vm_name, action, computer_name]`
    *   `[DISCOVER_INFRASTRUCTURE: subnet]` or `[DISCOVER_INFRASTRUCTURE]`
    *   `[EXECUTE_LINUX: ip, command]` or `[EXECUTE_LINUX: ip, command, ssh_key_path]`
    *   `[EXECUTE_WINDOWS: ip, script]`

### 📤 2000-Character Message Splitting
Outbound text messages and image captions exceeding 2000 characters are automatically split at logical boundaries (paragraphs `\n\n`, newlines `\n`, or sentence endings `. `, `! `, `? `). Each chunk is sent sequentially to ensure message delivery without platform truncation.

---

## 🔑 Credentials & Target Recipient Setup Guide

To establish outbound connections, you need to acquire valid API tokens and target identifiers.

### 📤 Telegram Integration

#### 1. How to Validate a Telegram Bot Token
You can verify if your bot token is active by querying the official Telegram Bot API via your browser or terminal:
1. Open this URL in your web browser:
   `https://api.telegram.org/bot<YOUR_TELEGRAM_BOT_TOKEN>/getMe`
   *(Replace `<YOUR_TELEGRAM_BOT_TOKEN>` with your exact string, leaving no angle brackets).*
2. **Result**: A successful token returns a JSON payload showing the bot's username and attributes:
   ```json
   {"ok":true,"result":{"id":12345678,"is_bot":true,"first_name":"Project Vigil Bot","username":"project_vigil_bot"}}
   ```
3. If it returns `{"ok":false,"error_code":401,"description":"Unauthorized"}`, the token is invalid.

#### 2. How to Retrieve Your Telegram Recipient User ID (Chat ID)
Telegram bots cannot initiate direct messages with arbitrary users until the user starts a chat first.
1. In your Telegram app, search for the user `@userinfobot` and click **Start**.
2. It will instantly reply with your profile's numeric `Id` (e.g., `987654321`).
3. Search for **your bot's username** (e.g., `@project_vigil_bot`) and click **Start** or send a message to it.
4. Set the retrieved numeric `Id` as your `Recipient User ID` in the Project Vigil dashboard under **Proactivity Settings**.

---

### 💬 Discord Integration

#### 1. How to Validate a Discord Bot Token
You can check if a Discord Bot Token is valid using a command-line HTTP fetch:
```bash
curl -H "Authorization: Bot <YOUR_DISCORD_BOT_TOKEN>" https://discord.com/api/v10/users/@me
```
*   **Valid Token**: Returns a JSON object containing the bot name, ID, and discriminator.
*   **Invalid Token**: Returns a `401: Unauthorized` error.

#### 2. How to Enable Outbound Direct Messages (DMs) to a User
Discord bot integration requires direct channel setup. For security, bots cannot direct message a user unless the user **shares a mutual server (Guild) with the bot** and has direct messaging enabled.
1. Enable **Developer Mode** on your Discord app (User Settings -> Advanced -> Developer Mode toggle).
2. Create a private Discord server or use an existing one, invite your bot to it, and ensure the bot has the `Send Messages` permission.
3. Right-click on **your own profile** in the server member list and click **Copy User ID** (this is a numeric Snowflake like `123456789012345678`).
4. Set this User ID as your `Recipient User ID` in the Project Vigil dashboard.
5. On message dispatch, Project Vigil calls `POST /users/@me/channels` using the recipient ID to open/resolve the private direct message DM channel, and routes all conversations directly.

---

## 🧪 E2E Verification Tests

You can run automated integration test suites inside the virtual environment shell:

```bash
# Run system integration tests (validates 10 distinct test cases)
python test_system.py

# Run provider messaging tests (validates platform routing logic)
python test_messaging.py

# Test local HTTPS protocol and database configuration override values
python -m unittest scratch/test_local_https_oauth.py

# Test local oauth authorize generation and direct GET loopback exchanges
python -m unittest scratch/test_localhost_oauth_loopback.py

# Test programmatic self-signed SSL cert generation with SAN extensions
python -m unittest scratch/test_self_signed_ssl_cert.py

# Test dynamic regex parsing, query mapping, and inline memory lookup substitutions
python -m unittest scratch/test_inline_memory_lookups.py
```

---

## 📅 Microsoft M365 Integration & OAuth Endpoints

Project Vigil integrates with Microsoft Graph API using standard OAuth 2.0 Authorization Code flow. It supports direct browser redirects over private local networks using HTTPS.

### 🔌 API Authentication Endpoints
- **GET `/api/auth/m365/authorize`**: Retrieves the Entra ID authorization redirection link. It resolves the callback URL dynamically based on:
  1. The custom database config `m365_redirect_uri` (e.g. `https://172.16.1.123:8001/api/auth/m365/callback`).
  2. The dynamically resolved network LAN IP address and local SSL certificate status (`https` if certificates are present).
- **GET `/api/auth/m365/callback`**: Direct code redirect receiver. Microsoft redirects the browser here with authorization code credentials. The route handles exchange parameters, updates configurations, and serves a success page.
- **GET `/api/auth/m365/config`**: Fetches active App Registration client settings (`client_id`, `tenant_id`, `client_secret`, and connection authorization status).
- **POST `/api/auth/m365/config`**: Saves/updates App Registration credentials.
