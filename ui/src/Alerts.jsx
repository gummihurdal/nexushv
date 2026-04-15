import { useState, useEffect } from "react";

const C = {
  bg: "#1b1e27", panel: "#1f2330", card: "#252938", border: "#2e3347",
  toolbar: "#0f111a", header: "#0c0e15",
  blue: "#4a9fd4", green: "#5cb85c", yellow: "#f0ad4e", red: "#d9534f",
  purple: "#a78bfa", cyan: "#00b4d8",
  gray: "#8a8fa8", text: "#d4d8e8", dim: "#5a607a",
};
const font = "'IBM Plex Sans', system-ui, sans-serif";
const mono = "'IBM Plex Mono', monospace";

const SEV_CONFIG = {
  CRITICAL: { color: C.red, icon: "!!", bg: C.red + "15" },
  WARNING: { color: C.yellow, icon: "!", bg: C.yellow + "10" },
  INFO: { color: C.blue, icon: "i", bg: C.blue + "08" },
};

export default function AlertsPanel() {
  const [alerts, setAlerts] = useState([]);
  const [filter, setFilter] = useState("all"); // all, unack, critical, warning, info
  const [loading, setLoading] = useState(true);

  const loadAlerts = () => {
    const params = filter === "unack" ? "?acknowledged=false" : "";
    fetch(`/api/alerts${params}`)
      .then(r => r.json())
      .then(data => { setAlerts(Array.isArray(data) ? data : []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadAlerts();
    const id = setInterval(loadAlerts, 10000);
    return () => clearInterval(id);
  }, [filter]);

  const acknowledge = async (id) => {
    try {
      await fetch(`/api/alerts/${id}/acknowledge`, { method: "POST" });
      loadAlerts();
    } catch {}
  };

  const filtered = alerts.filter(a => {
    if (filter === "all") return true;
    if (filter === "unack") return !a.acknowledged;
    return a.severity?.toLowerCase() === filter;
  });

  const counts = {
    total: alerts.length,
    unack: alerts.filter(a => !a.acknowledged).length,
    critical: alerts.filter(a => a.severity === "CRITICAL").length,
    warning: alerts.filter(a => a.severity === "WARNING").length,
  };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: C.bg, fontFamily: font, color: C.text }}>

      {/* Header */}
      <div style={{
        background: C.header, padding: "12px 20px", borderBottom: `1px solid ${C.border}`,
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 32, height: 32, background: `linear-gradient(135deg, ${C.yellow}, ${C.red})`,
            borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16,
          }}>
            !
          </div>
          <div>
            <div style={{ color: C.text, fontWeight: 700, fontSize: 14 }}>Alerts & Notifications</div>
            <div style={{ color: C.dim, fontSize: 11 }}>System health alerts and event notifications</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {counts.critical > 0 && (
            <span style={{ background: C.red + "22", color: C.red, border: `1px solid ${C.red}44`,
              borderRadius: 3, fontSize: 10, padding: "3px 10px", fontFamily: mono, fontWeight: 700 }}>
              {counts.critical} Critical
            </span>
          )}
          {counts.warning > 0 && (
            <span style={{ background: C.yellow + "22", color: C.yellow, border: `1px solid ${C.yellow}44`,
              borderRadius: 3, fontSize: 10, padding: "3px 10px", fontFamily: mono, fontWeight: 700 }}>
              {counts.warning} Warning
            </span>
          )}
          <span style={{ background: C.green + "22", color: C.green, border: `1px solid ${C.green}44`,
            borderRadius: 3, fontSize: 10, padding: "3px 10px", fontFamily: mono, fontWeight: 700 }}>
            {counts.unack} Unacknowledged
          </span>
        </div>
      </div>

      {/* Filter bar */}
      <div style={{
        display: "flex", background: C.toolbar, borderBottom: `1px solid ${C.border}`, padding: "0 20px",
        gap: 0, flexShrink: 0,
      }}>
        {[
          ["all", "All"],
          ["unack", "Unacknowledged"],
          ["critical", "Critical"],
          ["warning", "Warning"],
          ["info", "Info"],
        ].map(([id, label]) => (
          <button key={id} onClick={() => setFilter(id)} style={{
            background: filter === id ? C.bg : "transparent",
            color: filter === id ? C.blue : C.dim,
            border: "none",
            borderBottom: filter === id ? `2px solid ${C.blue}` : "2px solid transparent",
            padding: "10px 16px", fontSize: 12, cursor: "pointer",
            fontFamily: font, fontWeight: 600,
          }}>
            {label}
          </button>
        ))}
      </div>

      {/* Alert list */}
      <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
        {loading ? (
          <div style={{ textAlign: "center", color: C.dim, padding: 40 }}>Loading alerts...</div>
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: "center", color: C.dim, padding: 40 }}>
            <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.3 }}>&#10003;</div>
            <div style={{ fontSize: 14 }}>No alerts to show</div>
            <div style={{ fontSize: 12, marginTop: 4 }}>
              {filter === "all" ? "Run an AI Health Scan to check for issues." : "Try a different filter."}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {filtered.map(alert => {
              const sev = SEV_CONFIG[alert.severity] || SEV_CONFIG.INFO;
              return (
                <div key={alert.id} style={{
                  background: alert.acknowledged ? C.card : sev.bg,
                  border: `1px solid ${alert.acknowledged ? C.border : sev.color + "44"}`,
                  borderRadius: 6, padding: "14px 18px",
                  opacity: alert.acknowledged ? 0.6 : 1,
                  transition: "all 0.2s",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flex: 1 }}>
                      <div style={{
                        width: 24, height: 24, borderRadius: 4,
                        background: sev.color + "33", color: sev.color,
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontSize: 11, fontWeight: 700, flexShrink: 0,
                      }}>
                        {sev.icon}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                          <span style={{
                            background: sev.color + "22", color: sev.color, borderRadius: 3,
                            fontSize: 9, padding: "1px 6px", fontFamily: mono, fontWeight: 700,
                          }}>
                            {alert.severity}
                          </span>
                          <span style={{ color: C.dim, fontSize: 10, fontFamily: mono }}>
                            {alert.component}
                          </span>
                        </div>
                        <div style={{ color: C.text, fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                          {alert.title}
                        </div>
                        {alert.detail && (
                          <div style={{ color: C.gray, fontSize: 12, lineHeight: 1.5, maxHeight: 60, overflow: "hidden" }}>
                            {alert.detail.slice(0, 200)}
                          </div>
                        )}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0, marginLeft: 12 }}>
                      <span style={{ color: C.dim, fontSize: 10, fontFamily: mono }}>
                        {alert.ts ? new Date(alert.ts).toLocaleString() : ""}
                      </span>
                      {!alert.acknowledged && (
                        <button onClick={() => acknowledge(alert.id)} style={{
                          background: C.green + "22", color: C.green,
                          border: `1px solid ${C.green}44`, borderRadius: 4,
                          padding: "4px 10px", fontSize: 11, cursor: "pointer",
                          fontFamily: font, fontWeight: 600,
                        }}>
                          Acknowledge
                        </button>
                      )}
                      {alert.acknowledged && (
                        <span style={{ color: C.dim, fontSize: 10 }}>
                          Acked{alert.ack_by ? ` by ${alert.ack_by}` : ""}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
