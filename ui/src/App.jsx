import { useState, useEffect } from "react";
import VSphere from "../nexushv-console.jsx";
import HADashboard from "../nexushv_ha_dashboard.jsx";
import AIChat from "./AIChat.jsx";
import Dashboard from "./Dashboard.jsx";
import AlertsPanel from "./Alerts.jsx";

const C = {
  bg: "#1b1e27", header: "#0c0e15", border: "#2e3347",
  blue: "#4a9fd4", green: "#5cb85c", red: "#d9534f", yellow: "#f0ad4e",
  dim: "#5a607a", text: "#d4d8e8", accent: "#00b4d8",
};
const font = "'IBM Plex Sans', system-ui, sans-serif";
const mono = "'IBM Plex Mono', monospace";

const tabs = [
  { id: "dashboard", label: "Dashboard",          icon: "home" },
  { id: "console",   label: "Management Console", icon: "grid" },
  { id: "ha",        label: "HA Failover",        icon: "shield" },
  { id: "alerts",    label: "Alerts",             icon: "bell" },
  { id: "ai",        label: "NEXUS AI",           icon: "cpu" },
];

const TabIcon = ({ type }) => {
  const s = { width: 14, height: 14, fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" };
  if (type === "grid") return <svg viewBox="0 0 24 24" style={s}><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>;
  if (type === "shield") return <svg viewBox="0 0 24 24" style={s}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>;
  if (type === "cpu") return <svg viewBox="0 0 24 24" style={s}><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>;
  if (type === "home") return <svg viewBox="0 0 24 24" style={s}><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>;
  if (type === "bell") return <svg viewBox="0 0 24 24" style={s}><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>;
  return null;
};

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [alertCount, setAlertCount] = useState(0);
  const [theme, setTheme] = useState("dark"); // dark or light

  // Poll alert count
  useEffect(() => {
    const load = () => {
      fetch("/api/alerts?acknowledged=false")
        .then(r => r.json())
        .then(data => setAlertCount(Array.isArray(data) ? data.length : 0))
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", fontFamily: font }}>
      {/* Top-level tab bar */}
      <div style={{
        height: 38, background: C.header, borderBottom: `1px solid ${C.border}`,
        display: "flex", alignItems: "center", paddingLeft: 8, gap: 0, flexShrink: 0,
      }}>
        {tabs.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
            background: activeTab === tab.id ? C.bg : "transparent",
            color: activeTab === tab.id ? C.blue : C.dim,
            border: "none",
            borderBottom: activeTab === tab.id ? `2px solid ${C.blue}` : "2px solid transparent",
            padding: "6px 16px",
            fontSize: 12, fontFamily: font, fontWeight: 600,
            cursor: "pointer",
            display: "flex", alignItems: "center", gap: 6,
            transition: "all 0.15s",
            height: "100%",
            position: "relative",
          }}>
            <TabIcon type={tab.icon} />
            {tab.label}
            {tab.id === "alerts" && alertCount > 0 && (
              <span style={{
                background: C.red, color: "#fff", borderRadius: 8,
                fontSize: 9, fontWeight: 700, padding: "1px 5px",
                minWidth: 16, textAlign: "center", lineHeight: "14px",
              }}>
                {alertCount}
              </span>
            )}
          </button>
        ))}
        <div style={{ flex: 1 }} />

        {/* Connection indicator */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, paddingRight: 12 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.green }} />
          <span style={{ color: C.dim, fontSize: 10, fontFamily: mono }}>Connected</span>
        </div>

        <div style={{ color: C.dim, fontSize: 10, paddingRight: 12, fontFamily: mono }}>
          NexusHV v2.0.0
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        {activeTab === "dashboard" && <Dashboard onNavigate={setActiveTab} />}
        {activeTab === "console" && <VSphere />}
        {activeTab === "ha" && <HADashboard />}
        {activeTab === "alerts" && <AlertsPanel />}
        {activeTab === "ai" && <AIChat />}
      </div>
    </div>
  );
}
