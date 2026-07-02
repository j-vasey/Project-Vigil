import React, { useState, useEffect, useRef } from "react";
import { 
  Play, 
  Pause, 
  Settings, 
  Terminal, 
  CheckCircle, 
  AlertTriangle, 
  Save, 
  RefreshCw, 
  Key, 
  Clock, 
  Bot, 
  User, 
  Radio,
  ShieldCheck
} from "lucide-react";

const API_BASE = window.location.port === "5173" || window.location.port === "5174" 
  ? `${window.location.protocol}//${window.location.hostname}:8001` 
  : "";

function App() {
  const [engineStatus, setEngineStatus] = useState("healthy");
  const [configs, setConfigs] = useState({
    llm_backend: "mock",
    llm_url: "http://localhost:11434",
    llm_model: "gemma:4",
    system_prompt: "",
    proactive_platform: "mock",
    proactive_user_id: "",
    proactive_interval_seconds: "60",
    dnd_start: "22:00",
    dnd_end: "08:00",
    telegram_token: "",
    telegram_user_id: "",
    discord_token: "",
    discord_user_id: "",
    comfyui_backend: "mock",
    comfyui_url: "http://localhost:8188",
    comfyui_ckpt: "v1-5-pruned-emaonly.safetensors",
    workspace_path: "",
    url_root: ""
  });
  const [logs, setLogs] = useState([]);
  const [recentOutreach, setRecentOutreach] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [activeTab, setActiveTab] = useState("dashboard"); // 'dashboard' or 'history'

  const [ollamaModels, setOllamaModels] = useState([]);
  const [isFetchingModels, setIsFetchingModels] = useState(false);
  const [comfyuiCheckpoints, setComfyuiCheckpoints] = useState([]);
  const [isFetchingCheckpoints, setIsFetchingCheckpoints] = useState(false);
  const [connectionTestResult, setConnectionTestResult] = useState(null);
  const [isTestingConnection, setIsTestingConnection] = useState(false);

  // M365 Calendar state hooks
  const [m365Config, setM365Config] = useState({ client_id: "", tenant_id: "common", client_secret: "", is_authorized: false });
  const [deviceFlow, setDeviceFlow] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  const [authMessage, setAuthMessage] = useState("");
  const [isSavingM365, setIsSavingM365] = useState(false);

  const [manualPlatform, setManualPlatform] = useState("mock");
  const [manualUserId, setManualUserId] = useState("mock_user_1");
  const [manualText, setManualText] = useState("");
  const [manualStatus, setManualStatus] = useState(null);
  const [isSendingManual, setIsSendingManual] = useState(false);

  const terminalEndRef = useRef(null);
  const logEventSourceRef = useRef(null);

  // --- Fetch initial state ---
  const fetchHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (res.ok) {
        const data = await res.json();
        setEngineStatus(data.engine_status);
        setRecentOutreach(data.recent_proactivity || []);
      }
    } catch (err) {
      console.error("Error fetching system health:", err);
    }
  };

  const fetchConfigs = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/config`);
      if (res.ok) {
        const data = await res.json();
        setConfigs(prev => ({ ...prev, ...data }));
      }
    } catch (err) {
      console.error("Error fetching configurations:", err);
    }
  };

  // --- Toggle Active/Paused Status ---
  const handleToggleStatus = async () => {
    setIsToggling(true);
    try {
      const res = await fetch(`${API_BASE}/api/health/toggle`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setEngineStatus(data.engine_status);
        addSystemLog(`[SYSTEM] System health status toggled to: ${data.engine_status}`);
      }
    } catch (err) {
      console.error("Error toggling engine status:", err);
    } finally {
      setIsToggling(false);
    }
  };

  // --- Save Configurations ---
  const handleSaveConfigs = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ configs })
      });
      if (res.ok) {
        addSystemLog("[SYSTEM] Configurations updated successfully on server.");
        // Retrieve fresh configs
        const data = await res.json();
        setConfigs(prev => ({ ...prev, ...data.configs }));
        // Pulse animation
        const savedAlert = document.getElementById("saved-alert");
        if (savedAlert) {
          savedAlert.classList.remove("opacity-0");
          setTimeout(() => savedAlert.classList.add("opacity-0"), 2000);
        }
      }
    } catch (err) {
      console.error("Error updating configurations:", err);
    } finally {
      setIsSaving(false);
    }
  };

  // --- Utility for manually logging UI actions ---
  const addSystemLog = (line) => {
    setLogs(prev => [...prev, `${new Date().toLocaleTimeString()} ${line}`]);
  };

  const handleConfigChange = (key, val) => {
    setConfigs(prev => ({ ...prev, [key]: val }));
  };

  // --- Fetch Ollama Models ---
  const fetchOllamaModels = async (url) => {
    if (!url) return;
    setIsFetchingModels(true);
    try {
      const res = await fetch(`${API_BASE}/api/ollama/models?url=${encodeURIComponent(url)}`);
      if (res.ok) {
        const data = await res.json();
        setOllamaModels(data.models || []);
      } else {
        setOllamaModels([]);
      }
    } catch (err) {
      console.error("Error fetching ollama models:", err);
      setOllamaModels([]);
    } finally {
      setIsFetchingModels(false);
    }
  };

  // --- Fetch ComfyUI Checkpoints ---
  const fetchComfyuiCheckpoints = async (url) => {
    setIsFetchingCheckpoints(true);
    try {
      const res = await fetch(`${API_BASE}/api/comfyui/checkpoints?url=${encodeURIComponent(url || "")}`);
      if (res.ok) {
        const data = await res.json();
        setComfyuiCheckpoints(data.checkpoints || []);
      } else {
        setComfyuiCheckpoints([]);
      }
    } catch (err) {
      console.error("Error fetching ComfyUI checkpoints:", err);
      setComfyuiCheckpoints([]);
    } finally {
      setIsFetchingCheckpoints(false);
    }
  };

  // --- Test LLM Connection ---
  const handleTestConnection = async () => {
    setIsTestingConnection(true);
    setConnectionTestResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/llm/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backend: configs.llm_backend,
          url: configs.llm_url,
          model: configs.llm_model
        })
      });
      const data = await res.json();
      if (res.ok) {
        setConnectionTestResult({
          success: true,
          message: `Success! Response: "${data.response}"`
        });
      } else {
        setConnectionTestResult({
          success: false,
          message: `Failed: ${data.detail || "Unknown error"}`
        });
      }
    } catch (err) {
      setConnectionTestResult({
        success: false,
        message: `Network error: ${err.message}`
      });
    } finally {
      setIsTestingConnection(false);
    }
  };

  // --- Send Manual Outbound Message ---
  const handleSendManualMessage = async (e) => {
    e.preventDefault();
    if (!manualText.trim()) return;
    setIsSendingManual(true);
    setManualStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/manual/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform: manualPlatform,
          user_id: manualUserId,
          text: manualText
        })
      });
      const data = await res.json();
      if (res.ok) {
        setManualStatus({
          success: true,
          message: "Message sent and routed successfully!"
        });
        setManualText(""); // Clear text on success
        addSystemLog(`[SYSTEM] Sent manual message to ${manualUserId} on '${manualPlatform}'`);
      } else {
        setManualStatus({
          success: false,
          message: `Failed: ${data.detail || "Unknown error"}`
        });
      }
    } catch (err) {
      setManualStatus({
        success: false,
        message: `Network error: ${err.message}`
      });
    } finally {
      setIsSendingManual(false);
    }
  };
  // --- Microsoft M365 Calendar Authentication Handlers ---
  const fetchM365Config = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/auth/m365/config`);
      if (resp.ok) {
        const data = await resp.json();
        setM365Config(data);
      }
    } catch (err) {
      console.error("Failed to fetch M365 config:", err);
    }
  };

  const saveM365Config = async (e) => {
    e.preventDefault();
    setIsSavingM365(true);
    try {
      const resp = await fetch(`${API_BASE}/api/auth/m365/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: m365Config.client_id,
          tenant_id: m365Config.tenant_id,
          client_secret: m365Config.client_secret || ""
        })
      });
      if (resp.ok) {
        addSystemLog("[SYSTEM] Microsoft M365 App Registration credentials updated.");
        fetchM365Config();
      } else {
        const data = await resp.json();
        alert(`Failed to save: ${data.detail || "Unknown error"}`);
      }
    } catch (err) {
      console.error("M365 save error:", err);
    } finally {
      setIsSavingM365(false);
    }
  };

  const startM365WebAuth = async () => {
    setAuthMessage("Contacting Microsoft authorization server...");
    try {
      const resp = await fetch(`${API_BASE}/api/auth/m365/authorize`);
      if (!resp.ok) {
        const errData = await resp.json();
        setAuthMessage(`Authorization redirect failed: ${errData.detail || "Unknown error"}`);
        return;
      }
      const data = await resp.json();
      if (data.authorize_url) {
        window.location.href = data.authorize_url;
      } else {
        setAuthMessage("Failed to retrieve authorize URL from backend.");
      }
    } catch (err) {
      setAuthMessage(`Error initiating authorization: ${err.message}`);
    }
  };

  const disconnectM365 = async () => {
    if (!confirm("Are you sure you want to disconnect Microsoft M365 access?")) return;
    try {
      const resp = await fetch(`${API_BASE}/api/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          configs: {
            m365_access_token: "",
            m365_refresh_token: "",
            m365_token_expiry: ""
          }
        })
      });
      if (resp.ok) {
        addSystemLog("[SYSTEM] Disconnected M365 Calendar account.");
        fetchM365Config();
      }
    } catch (err) {
      console.error("M365 disconnect error:", err);
    }
  };

  // Trigger Ollama model fetches when configurations dictate
  useEffect(() => {
    if (configs.llm_backend === "ollama") {
      fetchOllamaModels(configs.llm_url);
    }
  }, [configs.llm_backend, configs.llm_url]);

  // Trigger ComfyUI checkpoint fetches
  useEffect(() => {
    fetchComfyuiCheckpoints(configs.comfyui_url);
  }, [configs.comfyui_backend, configs.comfyui_url]);

  // --- Setup Server-Sent Events (SSE) log stream ---
  useEffect(() => {
    fetchHealth();
    fetchConfigs();
    fetchM365Config();

    // Subscribe to SSE
    const connectSSE = () => {
      if (logEventSourceRef.current) {
        logEventSourceRef.current.close();
      }

      const sse = new EventSource(`${API_BASE}/api/logs/stream`);
      logEventSourceRef.current = sse;

      sse.onmessage = (event) => {
        setLogs(prev => [...prev, event.data]);
      };

      sse.onerror = (err) => {
        console.error("SSE connection error. Retrying...", err);
        sse.close();
        setTimeout(connectSSE, 5000); // Reconnect in 5s
      };
    };

    connectSSE();

    // Poll health status and log audit history every 5 seconds
    const interval = setInterval(fetchHealth, 5000);

    return () => {
      if (logEventSourceRef.current) {
        logEventSourceRef.current.close();
      }
      clearInterval(interval);
    };
  }, []);

  // --- Autoscroll Logs ---
  useEffect(() => {
    if (autoScroll && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll]);

  // Helper to format log level colors in console
  const formatLogLine = (logLine) => {
    if (logLine.includes("[ERROR]")) {
      return <span className="text-rose-400">{logLine}</span>;
    } else if (logLine.includes("[WARNING]")) {
      return <span className="text-amber-400">{logLine}</span>;
    } else if (logLine.includes("[SYSTEM]")) {
      return <span className="text-fuchsia-400 font-semibold">{logLine}</span>;
    } else if (logLine.includes("[INFO]")) {
      return <span className="text-emerald-400">{logLine}</span>;
    } else if (logLine.includes("[Proactivity]")) {
      return <span className="text-cyan-400">{logLine}</span>;
    } else if (logLine.includes("[Orchestrator]")) {
      return <span className="text-violet-400">{logLine}</span>;
    }
    return <span>{logLine}</span>;
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans select-none antialiased selection:bg-purple-500/30">
      
      {/* Header */}
      <header className="border-b border-slate-900 bg-slate-950/80 backdrop-blur-xl sticky top-0 z-50 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-purple-600 to-indigo-600 flex items-center justify-center shadow-lg shadow-purple-900/20 ring-1 ring-purple-500/30">
            <Bot className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight bg-gradient-to-r from-purple-400 to-indigo-200 bg-clip-text text-transparent">
              Project Vigil
            </h1>
            <p className="text-xs text-slate-500">Autonomous Outbound AI Gateway</p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 ring-1 ring-slate-800">
            <Radio className={`h-4 w-4 ${engineStatus === "healthy" ? "text-emerald-500 animate-pulse" : "text-amber-500"}`} />
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {engineStatus === "healthy" ? "ACTIVE" : "PAUSED"}
            </span>
          </div>

          <button
            onClick={handleToggleStatus}
            disabled={isToggling}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all duration-300 shadow-md ${
              engineStatus === "healthy"
                ? "bg-amber-600/20 hover:bg-amber-600 text-amber-200 ring-1 ring-amber-500/30 hover:shadow-amber-900/20 cursor-pointer"
                : "bg-emerald-600/20 hover:bg-emerald-600 text-emerald-200 ring-1 ring-emerald-500/30 hover:shadow-emerald-900/20 cursor-pointer"
            }`}
          >
            {engineStatus === "healthy" ? (
              <>
                <Pause className="h-4 w-4" /> Pause Engine
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> Resume Engine
              </>
            )}
          </button>
        </div>
      </header>

      {/* Navigation tabs */}
      <div className="bg-slate-950/40 px-6 py-2 border-b border-slate-900 flex gap-2">
        <button 
          onClick={() => setActiveTab("dashboard")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors cursor-pointer ${
            activeTab === "dashboard" ? "bg-slate-900 text-purple-400 ring-1 ring-slate-800" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Dashboard Control
        </button>
        <button 
          onClick={() => setActiveTab("history")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors cursor-pointer ${
            activeTab === "history" ? "bg-slate-900 text-purple-400 ring-1 ring-slate-800" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Proactivity Audit Log ({recentOutreach.length})
        </button>
        <button 
          onClick={() => setActiveTab("m365")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors cursor-pointer ${
            activeTab === "m365" ? "bg-slate-900 text-purple-400 ring-1 ring-slate-800" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          M365 Calendar Link
        </button>
      </div>

      {/* Main Panel Content */}
      <main className="flex-1 p-6 grid grid-cols-1 lg:grid-cols-12 gap-6 overflow-hidden">
        
        {activeTab === "dashboard" && (
          <>
            {/* Settings (Left Side) */}
            <section className="lg:col-span-5 flex flex-col gap-6">
              <form onSubmit={handleSaveConfigs} className="bg-slate-900/40 border border-slate-900 rounded-xl p-6 flex flex-col gap-5 shadow-xl relative overflow-hidden ring-1 ring-white/5">
                <div className="flex items-center justify-between border-b border-slate-900 pb-3">
                  <div className="flex items-center gap-2">
                    <Settings className="h-5 w-5 text-purple-400" />
                    <h2 className="font-semibold text-slate-200">Gateway Configuration</h2>
                  </div>
                  <span id="saved-alert" className="text-xs text-emerald-400 opacity-0 transition-opacity duration-300 font-semibold flex items-center gap-1">
                    <CheckCircle className="h-3 w-3" /> Saved!
                  </span>
                </div>

                {/* LLM Backend selection */}
                <div className="flex flex-col gap-2">
                  <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">AI Response Backend</label>
                  <select
                    value={configs.llm_backend}
                    onChange={(e) => handleConfigChange("llm_backend", e.target.value)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                  >
                    <option value="mock">Mock Test Generator (No API required)</option>
                    <option value="ollama">Ollama (Local LLM API - Gemma 4/Gemma 2)</option>
                    <option value="kobold">KoboldAI (Local KoboldCPP Engine)</option>
                  </select>
                </div>

                {/* LLM Coordinate inputs */}
                {configs.llm_backend !== "mock" && (
                  <>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div className="flex flex-col gap-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">API URL</label>
                        <input
                          type="text"
                          value={configs.llm_url}
                          onChange={(e) => handleConfigChange("llm_url", e.target.value)}
                          placeholder="http://localhost:11434"
                          className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                        />
                      </div>
                      {configs.llm_backend === "ollama" && (
                        <div className="flex flex-col gap-2">
                          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center justify-between">
                            <span>Model</span>
                            {isFetchingModels && (
                              <span className="text-[10px] text-purple-400 animate-pulse lowercase font-normal">
                                fetching...
                              </span>
                            )}
                          </label>
                          {ollamaModels.length > 0 ? (
                            <select
                              value={configs.llm_model}
                              onChange={(e) => handleConfigChange("llm_model", e.target.value)}
                              className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors cursor-pointer"
                            >
                              <option value="">-- Select Model --</option>
                              {ollamaModels.map((model) => (
                                <option key={model} value={model}>
                                  {model}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              value={configs.llm_model}
                              onChange={(e) => handleConfigChange("llm_model", e.target.value)}
                              placeholder="gemma:4"
                              className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                            />
                          )}
                        </div>
                      )}
                    </div>

                    <div className="flex flex-col gap-2 pt-1">
                      <button
                        type="button"
                        onClick={handleTestConnection}
                        disabled={isTestingConnection}
                        className="w-full bg-slate-900 border border-slate-800 hover:bg-slate-850 text-slate-300 font-semibold py-1.5 px-3 rounded-lg text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isTestingConnection ? (
                          <>
                            <RefreshCw className="h-3 w-3 animate-spin" /> Testing Connection...
                          </>
                        ) : (
                          <>
                            <Radio className="h-3.5 w-3.5 text-purple-400" /> Test Connection to Model
                          </>
                        )}
                      </button>
                      {connectionTestResult && (
                        <div
                          className={`text-xs p-2.5 rounded-lg border leading-snug font-mono ${
                            connectionTestResult.success
                              ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-300 border-rose-500/20"
                          }`}
                        >
                          {connectionTestResult.message}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* ComfyUI Image Generation Backend */}
                <div className="border-t border-slate-900 pt-4 flex flex-col gap-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-300">
                    <Radio className="h-4 w-4 text-purple-400" />
                    ComfyUI Image Generation Settings
                  </div>
                  
                  <div className="flex flex-col gap-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Image Backend Mode</label>
                    <select
                      value={configs.comfyui_backend || "mock"}
                      onChange={(e) => handleConfigChange("comfyui_backend", e.target.value)}
                      className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors cursor-pointer"
                    >
                      <option value="mock">Mock Test Generator (Returns teal PNG placeholder)</option>
                      <option value="comfyui">ComfyUI (Submit to real Stable Diffusion instance)</option>
                    </select>
                  </div>

                  {configs.comfyui_backend === "comfyui" && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div className="flex flex-col gap-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">ComfyUI API URL</label>
                        <input
                          type="text"
                          value={configs.comfyui_url || "http://localhost:8188"}
                          onChange={(e) => handleConfigChange("comfyui_url", e.target.value)}
                          placeholder="http://localhost:8188"
                          className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                        />
                      </div>
                      <div className="flex flex-col gap-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center justify-between">
                          <span>Checkpoint (Model)</span>
                          {isFetchingCheckpoints && (
                            <span className="text-[10px] text-purple-400 animate-pulse lowercase font-normal">
                              fetching...
                            </span>
                          )}
                        </label>
                        {comfyuiCheckpoints.length > 0 ? (
                          <select
                            value={configs.comfyui_ckpt || ""}
                            onChange={(e) => handleConfigChange("comfyui_ckpt", e.target.value)}
                            className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors cursor-pointer"
                          >
                            <option value="">-- Select Checkpoint --</option>
                            {comfyuiCheckpoints.map((ckpt) => (
                              <option key={ckpt} value={ckpt}>
                                {ckpt}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="text"
                            value={configs.comfyui_ckpt || "v1-5-pruned-emaonly.safetensors"}
                            onChange={(e) => handleConfigChange("comfyui_ckpt", e.target.value)}
                            placeholder="e.g. v1-5-pruned-emaonly.safetensors"
                            className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                          />
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* System Persona Prompt */}
                <div className="flex flex-col gap-2">
                  <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">System Persona / Instructions</label>
                  <textarea
                    rows={3}
                    value={configs.system_prompt}
                    onChange={(e) => handleConfigChange("system_prompt", e.target.value)}
                    placeholder="Enter system instructions for conversation context generation..."
                    className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors resize-none"
                  />
                </div>

                {/* Credentials & Access Keys */}
                <div className="border-t border-slate-900 pt-4 flex flex-col gap-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-300">
                    <Key className="h-4 w-4 text-indigo-400" />
                    Credentials & Access Keys
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Telegram Bot Token</label>
                      <input
                        type="password"
                        value={configs.telegram_token}
                        onChange={(e) => handleConfigChange("telegram_token", e.target.value)}
                        placeholder="e.g. 123456789:ABCDefgh..."
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Discord Bot Token</label>
                      <input
                        type="password"
                        value={configs.discord_token}
                        onChange={(e) => handleConfigChange("discord_token", e.target.value)}
                        placeholder="e.g. MTk4MzI1..."
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Telegram User ID</label>
                      <input
                        type="text"
                        value={configs.telegram_user_id || ""}
                        onChange={(e) => handleConfigChange("telegram_user_id", e.target.value)}
                        placeholder="e.g. 8920268999"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Discord User ID</label>
                      <input
                        type="text"
                        value={configs.discord_user_id || ""}
                        onChange={(e) => handleConfigChange("discord_user_id", e.target.value)}
                        placeholder="e.g. 141626964044808192"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2 sm:col-span-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">MCP File System Directories / Drive Access (Comma-separated)</label>
                      <input
                        type="text"
                        value={configs.workspace_path || ""}
                        onChange={(e) => handleConfigChange("workspace_path", e.target.value)}
                        placeholder="e.g. C:\Users\Josh, E:\ (comma-separated list for multiple directories/drives)"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2 sm:col-span-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Gateway Public URL Root (for OAuth / Webhooks)</label>
                      <input
                        type="text"
                        value={configs.url_root || ""}
                        onChange={(e) => handleConfigChange("url_root", e.target.value)}
                        placeholder="e.g. https://127.0.0.1:8003"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                  </div>
                </div>

                {/* Outbound Proactivity Target Configurations */}
                <div className="border-t border-slate-900 pt-4 flex flex-col gap-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-300">
                    <Clock className="h-4 w-4 text-indigo-400" />
                    Proactivity Settings
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Channel Target</label>
                      <select
                        value={configs.proactive_platform}
                        onChange={(e) => handleConfigChange("proactive_platform", e.target.value)}
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      >
                        <option value="mock">mock</option>
                        <option value="telegram">telegram</option>
                        <option value="discord">discord</option>
                      </select>
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Recipient User ID</label>
                      <input
                        type="text"
                        value={configs.proactive_user_id}
                        onChange={(e) => handleConfigChange("proactive_user_id", e.target.value)}
                        placeholder="mock_user_1"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-4">
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Interval (sec)</label>
                      <input
                        type="number"
                        min="10"
                        value={configs.proactive_interval_seconds}
                        onChange={(e) => handleConfigChange("proactive_interval_seconds", e.target.value)}
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">DND Start</label>
                      <input
                        type="text"
                        placeholder="22:00"
                        value={configs.dnd_start}
                        onChange={(e) => handleConfigChange("dnd_start", e.target.value)}
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors text-center"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">DND End</label>
                      <input
                        type="text"
                        placeholder="08:00"
                        value={configs.dnd_end}
                        onChange={(e) => handleConfigChange("dnd_end", e.target.value)}
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors text-center"
                      />
                    </div>
                  </div>
                </div>

                {/* Submit button */}
                <button
                  type="submit"
                  disabled={isSaving}
                  className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-semibold py-2.5 rounded-lg shadow-lg hover:shadow-purple-900/30 transition-all text-sm mt-2 cursor-pointer"
                >
                  {isSaving ? (
                    <>
                      <RefreshCw className="h-4 w-4 animate-spin" /> Saving Settings...
                    </>
                  ) : (
                    <>
                      <Save className="h-4 w-4" /> Save Configuration
                    </>
                  )}
                </button>
              </form>

              {/* Manual Message Dispatcher Card */}
              <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-6 flex flex-col gap-4 shadow-xl ring-1 ring-white/5">
                <div className="flex items-center gap-2 border-b border-slate-900 pb-3">
                  <Play className="h-5 w-5 text-indigo-400" />
                  <h2 className="font-semibold text-slate-200">Manual Outbound Message</h2>
                </div>
                
                <form onSubmit={handleSendManualMessage} className="flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Channel</label>
                      <select
                        value={manualPlatform}
                        onChange={(e) => {
                          const platform = e.target.value;
                          setManualPlatform(platform);
                          if (platform === "telegram") {
                            setManualUserId(configs.telegram_user_id || "");
                          } else if (platform === "discord") {
                            setManualUserId(configs.discord_user_id || "");
                          } else {
                            setManualUserId(configs.proactive_user_id || "mock_user_1");
                          }
                        }}
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors cursor-pointer"
                      >
                        <option value="mock">mock</option>
                        <option value="telegram">telegram</option>
                        <option value="discord">discord</option>
                      </select>
                    </div>
                    
                    <div className="flex flex-col gap-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Recipient ID</label>
                      <input
                        type="text"
                        value={manualUserId}
                        onChange={(e) => setManualUserId(e.target.value)}
                        placeholder="mock_user_1 or telegram chat id"
                        className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                      />
                    </div>
                  </div>
                  
                  <div className="flex flex-col gap-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Message Text</label>
                    <textarea
                      rows={3}
                      value={manualText}
                      onChange={(e) => setManualText(e.target.value)}
                      placeholder="Type a manual message here. Tip: Use [IMAGE: prompt] to test image generation!"
                      className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors resize-none"
                    />
                  </div>
                  
                  <button
                    type="submit"
                    disabled={isSendingManual || !manualText.trim()}
                    className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-semibold py-2 rounded-lg shadow-lg hover:shadow-indigo-900/30 transition-all text-sm cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isSendingManual ? (
                      <>
                        <RefreshCw className="h-4 w-4 animate-spin" /> Sending...
                      </>
                    ) : (
                      <>
                        <Radio className="h-4 w-4" /> Send Outbound Message
                      </>
                    )}
                  </button>
                  
                  {manualStatus && (
                    <div className={`text-xs p-2.5 rounded-lg border font-mono ${
                      manualStatus.success
                        ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                        : "bg-rose-500/10 text-rose-300 border-rose-500/20"
                    }`}>
                      {manualStatus.message}
                    </div>
                  )}
                </form>
              </div>
            </section>

            {/* Logging terminal (Right Side) */}
            <section className="lg:col-span-7 flex flex-col bg-slate-900/40 border border-slate-900 rounded-xl shadow-xl overflow-hidden ring-1 ring-white/5">
              <div className="flex items-center justify-between border-b border-slate-900 px-6 py-4 bg-slate-950/40">
                <div className="flex items-center gap-2">
                  <Terminal className="h-5 w-5 text-indigo-400" />
                  <h2 className="font-semibold text-slate-200">Real-time Logging Terminal</h2>
                </div>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={autoScroll}
                      onChange={(e) => setAutoScroll(e.target.checked)}
                      className="rounded bg-slate-950 border-slate-800 text-purple-600 focus:ring-purple-500 focus:ring-offset-slate-900 cursor-pointer"
                    />
                    Autoscroll
                  </label>
                  <button
                    onClick={() => setLogs([])}
                    className="text-xs text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
                  >
                    Clear Console
                  </button>
                </div>
              </div>

              {/* Console log box */}
              <div className="flex-1 p-4 bg-slate-950/80 font-mono text-xs overflow-y-auto min-h-[480px] max-h-[600px] flex flex-col gap-1.5 leading-relaxed">
                {logs.length === 0 ? (
                  <div className="text-slate-600 italic">No incoming logs. Active events will show here.</div>
                ) : (
                  logs.map((log, index) => (
                    <div key={index} className="border-b border-slate-950/20 pb-0.5">
                      {formatLogLine(log)}
                    </div>
                  ))
                )}
                <div ref={terminalEndRef} />
              </div>
            </section>
          </>
        )}

        {activeTab === "history" && (
          /* Audit history logs (Entire Screen when selected) */
          <section className="lg:col-span-12 flex flex-col bg-slate-900/40 border border-slate-900 rounded-xl shadow-xl overflow-hidden ring-1 ring-white/5">
            <div className="flex items-center justify-between border-b border-slate-900 px-6 py-4 bg-slate-950/40">
              <div className="flex items-center gap-2">
                <Clock className="h-5 w-5 text-purple-400" />
                <h2 className="font-semibold text-slate-200">Autonomous Proactive Outreach Logs</h2>
              </div>
              <button 
                onClick={fetchHealth}
                className="flex items-center gap-1.5 text-xs bg-slate-900 hover:bg-slate-850 px-2.5 py-1.5 rounded-lg border border-slate-800 text-slate-300 transition-all cursor-pointer"
              >
                <RefreshCw className="h-3 w-3" /> Refresh
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm border-collapse">
                <thead className="bg-slate-950/50 text-slate-400 uppercase tracking-wider text-xs font-semibold border-b border-slate-900">
                  <tr>
                    <th className="px-6 py-3">Log ID</th>
                    <th className="px-6 py-3">Timestamp (Local)</th>
                    <th className="px-6 py-3">Trigger Reason</th>
                    <th className="px-6 py-3">Status / Output Dispatched</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-900">
                  {recentOutreach.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-6 py-8 text-center text-slate-500 italic">
                        No proactive logs recorded in SQLite database.
                      </td>
                    </tr>
                  ) : (
                    recentOutreach.map((item) => {
                      const isSkipped = item.reason_code.includes("SKIPPED");
                      return (
                        <tr key={item.id} className="hover:bg-slate-900/20 transition-colors">
                          <td className="px-6 py-4 font-mono text-xs text-slate-400">#{item.id}</td>
                          <td className="px-6 py-4 text-slate-300">
                            {new Date(item.execution_time).toLocaleString()}
                          </td>
                          <td className="px-6 py-4">
                            <span className="font-semibold text-indigo-300">{item.reason_code}</span>
                          </td>
                          <td className="px-6 py-4 max-w-lg truncate">
                            {isSkipped ? (
                              <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
                                <AlertTriangle className="h-3 w-3" /> Skipped (DND or Paused)
                              </span>
                            ) : (
                              <span className="text-slate-400 font-mono text-xs block truncate" title={item.message_dispatched}>
                                {item.message_dispatched}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {activeTab === "m365" && (
          <section className="lg:col-span-12 flex flex-col gap-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Credentials Setup Card */}
              <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-6 flex flex-col gap-5 shadow-xl relative overflow-hidden ring-1 ring-white/5">
                <div className="flex items-center gap-2 border-b border-slate-900 pb-3">
                  <Settings className="h-5 w-5 text-purple-400" />
                  <h2 className="font-semibold text-slate-200">M365 Entra ID Configuration</h2>
                </div>
                <form onSubmit={saveM365Config} className="flex flex-col gap-4">
                  <div className="flex flex-col gap-2 bg-slate-950/60 p-3 rounded-lg border border-slate-850/50">
                    <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Microsoft Redirect / Callback URI</label>
                    <div className="text-xs font-mono text-indigo-300 break-all select-all">
                      {m365Config.redirect_uri || "https://127.0.0.1:8003/api/auth/m365/callback"}
                    </div>
                    <p className="text-[10px] text-slate-500 leading-normal mt-1">
                      Copy this redirect URI and configure it in your Microsoft App Registration under Web Redirect URIs. Change URL Root on the Dashboard to modify this address.
                    </p>
                  </div>
                  <div className="flex flex-col gap-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Application (Client) ID</label>
                    <input
                      type="text"
                      required
                      value={m365Config.client_id || ""}
                      onChange={(e) => setM365Config({...m365Config, client_id: e.target.value})}
                      placeholder="e.g. 00000000-0000-0000-0000-000000000000"
                      className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Directory (Tenant) ID</label>
                    <input
                      type="text"
                      required
                      value={m365Config.tenant_id || "common"}
                      onChange={(e) => setM365Config({...m365Config, tenant_id: e.target.value})}
                      placeholder="e.g. common or tenant-uuid"
                      className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Application Client Secret</label>
                    <input
                      type="password"
                      value={m365Config.client_secret || ""}
                      onChange={(e) => setM365Config({...m365Config, client_secret: e.target.value})}
                      placeholder="e.g. Value of your App Registration secret"
                      className="bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 transition-colors"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={isSavingM365}
                    className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-2 rounded-lg transition-colors text-sm cursor-pointer disabled:opacity-50"
                  >
                    Save App Registration Settings
                  </button>
                </form>
              </div>

              {/* Authentication Status Card */}
              <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-6 flex flex-col gap-5 shadow-xl relative overflow-hidden ring-1 ring-white/5">
                <div className="flex items-center gap-2 border-b border-slate-900 pb-3">
                  <ShieldCheck className="h-5 w-5 text-purple-400" />
                  <h2 className="font-semibold text-slate-200">Connection & Authorization</h2>
                </div>

                <div className="flex flex-col gap-4">
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-slate-400 font-semibold">Status:</span>
                    {m365Config.is_authorized ? (
                      <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-bold">
                        Connected / Authorized
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-rose-500/10 text-rose-300 border border-rose-500/20 font-bold">
                        Disconnected
                      </span>
                    )}
                  </div>

                  {!m365Config.is_authorized ? (
                    <div className="flex flex-col gap-4">
                      <button
                        onClick={startM365WebAuth}
                        className="flex items-center justify-center gap-2 bg-purple-600 hover:bg-purple-500 text-white font-semibold py-2.5 rounded-lg transition-colors text-sm cursor-pointer shadow-lg hover:shadow-purple-500/10"
                      >
                        Sign on with Microsoft
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2">
                      <p className="text-xs text-slate-400 leading-relaxed">
                        Project Vigil is linked to Outlook Calendar under Client ID <span className="font-mono bg-slate-950 px-1.5 py-0.5 rounded text-indigo-300">{m365Config.client_id}</span>. The companion can read upcoming agenda logs and schedule new calendar events dynamically.
                      </p>
                      <button
                        onClick={disconnectM365}
                        className="mt-2 flex items-center justify-center gap-2 bg-rose-950/20 hover:bg-rose-950/40 border border-rose-900/30 text-rose-300 font-semibold py-2 rounded-lg transition-colors text-sm cursor-pointer"
                      >
                        Disconnect Link
                      </button>
                    </div>
                  )}

                  {authMessage && (
                    <div className="text-xs p-3 rounded-lg border font-mono bg-slate-950 border-slate-850 text-purple-300">
                      {authMessage}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}
      </main>

      {/* Footer */}
      <footer className="px-6 py-4 border-t border-slate-900 bg-slate-950 flex items-center justify-between text-xs text-slate-600">
        <div>Project Vigil Self-Hosted Assistant Gateway</div>
        <div>v1.0.0 &bull; Local System Running</div>
      </footer>
    </div>
  );
}

export default App;
