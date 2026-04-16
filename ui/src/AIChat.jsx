import { useState, useEffect, useRef } from "react";

const C = {
  bg: "#1b1e27", panel: "#1f2330", card: "#252938", border: "#2e3347",
  toolbar: "#0f111a", header: "#0c0e15",
  blue: "#4a9fd4", green: "#5cb85c", yellow: "#f0ad4e", red: "#d9534f",
  gray: "#8a8fa8", text: "#d4d8e8", dim: "#5a607a", accent: "#00b4d8",
};
const font = "'IBM Plex Sans', system-ui, sans-serif";
const mono = "'IBM Plex Mono', monospace";

const SUGGESTIONS = [
  "Why is VM prod-db-primary showing high CPU usage?",
  "Explain EPT violation handling in KVM",
  "Run a proactive health scan of my cluster",
  "How do I set up SR-IOV for maximum network performance?",
  "What's the best QCOW2 cache mode for PostgreSQL?",
  "How does live migration work under the hood?",
];

export default function AIChat() {
  const [messages, setMessages] = useState([
    { role: "assistant", content: "I'm **NEXUS AI**, your PhD-level virtualization administrator. I monitor your NexusHV cluster, diagnose issues, and provide expert guidance on KVM/QEMU, storage, networking, HA, and performance tuning.\n\nI have real-time access to your cluster state. Ask me anything, or I can run a proactive health scan." },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [aiHealth, setAIHealth] = useState(null);
  const [scanResults, setScanResults] = useState(null);
  const chatEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetch("/api/ai/health").then(r => r.json()).then(setAIHealth).catch(() => {});
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async (text) => {
    if (!text.trim() || loading) return;
    const userMsg = text.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);

    // Try WebSocket streaming first for real-time token display
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/ai/stream`;

    try {
      const streamWs = new WebSocket(wsUrl);
      let tokens = [];

      // Add empty assistant message that we'll update
      setMessages(prev => [...prev, { role: "assistant", content: "" }]);

      streamWs.onopen = () => {
        streamWs.send(JSON.stringify({ message: userMsg }));
      };

      streamWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.token) {
          tokens.push(data.token);
          const fullText = tokens.join("");
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: "assistant", content: fullText };
            return updated;
          });
        }
        if (data.done) {
          streamWs.close();
          setLoading(false);
        }
      };

      streamWs.onerror = () => {
        // Fallback to REST API if WebSocket fails
        streamWs.close();
        fallbackChat(userMsg);
      };

      streamWs.onclose = () => {
        if (tokens.length === 0) {
          // WebSocket closed without sending — fallback
          fallbackChat(userMsg);
        }
      };
    } catch (e) {
      fallbackChat(userMsg);
    }
  };

  const fallbackChat = async (userMsg) => {
    try {
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg }),
      });
      const data = await res.json();
      setMessages(prev => {
        const updated = [...prev];
        // Replace last message if it's empty (from failed stream), or append
        if (updated.length > 0 && updated[updated.length - 1].role === "assistant" && updated[updated.length - 1].content === "") {
          updated[updated.length - 1] = { role: "assistant", content: data.response || "No response received." };
        } else {
          updated.push({ role: "assistant", content: data.response || "No response received." });
        }
        return updated;
      });
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: `Connection error: ${e.message}. Make sure the NexusHV API is running on port 8080.` }]);
    }
    setLoading(false);
  };

  const runScan = async () => {
    setLoading(true);
    setMessages(prev => [...prev, { role: "user", content: "Run a proactive health scan of my cluster" }]);
    try {
      const res = await fetch("/api/ai/scan", { method: "POST" });
      const data = await res.json();
      setScanResults(data.issues);
      const issueText = data.issues?.map(i =>
        `**[${i.severity}]** ${i.title}\n- Component: ${i.component}\n- ${i.technical_detail?.slice(0, 300) || ""}\n- Remediation: ${i.remediation || "N/A"}`
      ).join("\n\n") || "No issues detected.";
      setMessages(prev => [...prev, { role: "assistant", content: `**Proactive Health Scan Complete**\n\n${issueText}` }]);
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: `Scan error: ${e.message}` }]);
    }
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const renderContent = (text) => {
    // Simple markdown-ish rendering
    const lines = text.split("\n");
    return lines.map((line, i) => {
      let processed = line
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code style="background:#252938;padding:1px 4px;border-radius:3px;font-family:IBM Plex Mono,monospace;font-size:11px">$1</code>');

      if (line.startsWith("```")) {
        return <div key={i} style={{ borderTop: `1px solid ${C.border}`, margin: "4px 0" }} />;
      }
      if (line.startsWith("- ")) {
        return <div key={i} style={{ paddingLeft: 12, marginBottom: 2 }} dangerouslySetInnerHTML={{ __html: "&#8226; " + processed.slice(2) }} />;
      }
      return <div key={i} style={{ marginBottom: line === "" ? 8 : 2 }} dangerouslySetInnerHTML={{ __html: processed || "&nbsp;" }} />;
    });
  };

  const ollamaOk = aiHealth?.ollama_running;
  const modelOk = aiHealth?.model_available;

  return (
    <div style={{ display: "flex", height: "100%", fontFamily: font, background: C.bg }}>
      {/* Chat area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        {/* Header */}
        <div style={{ background: C.header, padding: "12px 20px", borderBottom: `1px solid ${C.border}`,
          display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 32, height: 32, background: `linear-gradient(135deg, ${C.accent}, ${C.blue})`,
              borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
              AI
            </div>
            <div>
              <div style={{ color: C.text, fontWeight: 700, fontSize: 14 }}>NEXUS AI</div>
              <div style={{ color: C.dim, fontSize: 11 }}>PhD-Level Virtualization Administrator</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <StatusTag label={ollamaOk ? "Ollama Online" : "Ollama Offline"} ok={ollamaOk} />
            <StatusTag label={modelOk ? "Model Loaded" : "No Model"} ok={modelOk} />
            <button onClick={runScan} disabled={loading} style={{
              background: loading ? C.border : `${C.green}22`, color: loading ? C.dim : C.green,
              border: `1px solid ${loading ? C.border : C.green + "44"}`, borderRadius: 4,
              padding: "6px 14px", fontSize: 12, cursor: loading ? "default" : "pointer",
              fontFamily: font, fontWeight: 600,
            }}>
              {loading ? "Scanning..." : "Run Health Scan"}
            </button>
          </div>
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflow: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          {messages.map((msg, i) => (
            <div key={i} style={{
              display: "flex", justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
            }}>
              <div style={{
                maxWidth: "85%", padding: "12px 16px", borderRadius: 8,
                background: msg.role === "user" ? `${C.blue}22` : C.card,
                border: `1px solid ${msg.role === "user" ? C.blue + "33" : C.border}`,
                color: C.text, fontSize: 13, lineHeight: 1.7,
              }}>
                {msg.role === "assistant" && (
                  <div style={{ color: C.accent, fontSize: 10, fontWeight: 700, marginBottom: 6, letterSpacing: 0.5 }}>NEXUS AI</div>
                )}
                {renderContent(msg.content)}
              </div>
            </div>
          ))}
          {loading && (
            <div style={{ display: "flex" }}>
              <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "12px 16px" }}>
                <div style={{ color: C.accent, fontSize: 10, fontWeight: 700, marginBottom: 6 }}>NEXUS AI</div>
                <div style={{ color: C.dim, fontSize: 13 }}>
                  <span className="typing">Analyzing cluster state</span>
                  <style>{`.typing::after { content: '...'; animation: dots 1.5s steps(4) infinite; } @keyframes dots { 0%,20%{content:'.'} 40%{content:'..'} 60%,100%{content:'...'}}`}</style>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Suggestions */}
        {messages.length <= 1 && (
          <div style={{ padding: "0 20px 12px", display: "flex", flexWrap: "wrap", gap: 6 }}>
            {SUGGESTIONS.map((s, i) => (
              <button key={i} onClick={() => sendMessage(s)} style={{
                background: `${C.blue}11`, color: C.blue, border: `1px solid ${C.blue}33`,
                borderRadius: 16, padding: "5px 12px", fontSize: 11, cursor: "pointer",
                fontFamily: font, transition: "all 0.15s",
              }}>
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Input */}
        <div style={{ padding: "12px 20px", borderTop: `1px solid ${C.border}`, background: C.toolbar, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask NEXUS AI about your cluster..."
              style={{
                flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
                color: C.text, padding: "10px 14px", fontSize: 13, fontFamily: font, outline: "none",
              }}
            />
            <button onClick={() => sendMessage(input)} disabled={loading || !input.trim()} style={{
              background: loading || !input.trim() ? C.border : `${C.blue}22`,
              color: loading || !input.trim() ? C.dim : C.blue,
              border: `1px solid ${loading || !input.trim() ? C.border : C.blue + "44"}`,
              borderRadius: 6, padding: "10px 20px", fontSize: 13, cursor: loading ? "default" : "pointer",
              fontFamily: font, fontWeight: 600,
            }}>
              Send
            </button>
          </div>
        </div>
      </div>

      {/* Sidebar — cluster quick view */}
      <ClusterSidebar />
    </div>
  );
}

function ClusterSidebar() {
  const [data, setData] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [vms, host, storage] = await Promise.all([
          fetch("/api/vms").then(r => r.json()),
          fetch("/api/hosts/local").then(r => r.json()),
          fetch("/api/storage").then(r => r.json()),
        ]);
        setData({ vms, host, storage });
      } catch {}
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  if (!data) return null;

  return (
    <div style={{ width: 260, borderLeft: `1px solid ${C.border}`, background: C.panel, overflow: "auto", flexShrink: 0 }}>
      <div style={{ padding: "12px 14px", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ color: C.dim, fontSize: 10, fontWeight: 700, letterSpacing: 0.5, marginBottom: 8 }}>CLUSTER CONTEXT</div>
        <div style={{ color: C.text, fontSize: 12, fontWeight: 600 }}>{data.host.hostname}</div>
        <div style={{ color: C.dim, fontSize: 11, marginTop: 2 }}>
          CPU: {data.host.cpu_pct}% | RAM: {data.host.ram_used_gb}/{data.host.ram_total_gb} GB
        </div>
      </div>

      <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ color: C.dim, fontSize: 10, fontWeight: 700, letterSpacing: 0.5, marginBottom: 8 }}>
          VIRTUAL MACHINES ({data.vms.filter(v => v.state === "poweredOn").length}/{data.vms.length})
        </div>
        {data.vms.map(vm => (
          <div key={vm.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", fontSize: 11 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{
                width: 6, height: 6, borderRadius: "50%",
                background: vm.state === "poweredOn" ? C.green : vm.state === "suspended" ? C.yellow : C.red,
              }} />
              <span style={{ color: C.text }}>{vm.name}</span>
            </div>
            <span style={{ color: C.dim, fontFamily: mono, fontSize: 10 }}>
              {vm.state === "poweredOn" ? `${Math.round(vm.cpu_pct)}%` : "off"}
            </span>
          </div>
        ))}
      </div>

      <div style={{ padding: "10px 14px" }}>
        <div style={{ color: C.dim, fontSize: 10, fontWeight: 700, letterSpacing: 0.5, marginBottom: 8 }}>STORAGE</div>
        {data.storage.map(s => {
          const usedPct = Math.round((s.capacity_gb - s.free_gb) / s.capacity_gb * 100);
          return (
            <div key={s.name} style={{ marginBottom: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                <span style={{ color: C.text }}>{s.name}</span>
                <span style={{ color: C.dim, fontFamily: mono, fontSize: 10 }}>{usedPct}%</span>
              </div>
              <div style={{ height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${usedPct}%`, height: "100%", borderRadius: 2,
                  background: usedPct > 85 ? C.red : usedPct > 70 ? C.yellow : C.green }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusTag({ label, ok }) {
  const col = ok ? C.green : C.yellow;
  return (
    <span style={{
      background: col + "22", color: col, border: `1px solid ${col}44`,
      borderRadius: 3, fontSize: 10, padding: "2px 8px", fontFamily: mono,
      fontWeight: 700, letterSpacing: 0.5,
    }}>
      {ok ? "\u2713" : "\u2717"} {label}
    </span>
  );
}
