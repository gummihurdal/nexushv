import { useState, useEffect, useRef } from "react";
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const C = {
  bg: "#1b1e27", panel: "#1f2330", card: "#252938", border: "#2e3347",
  toolbar: "#0f111a", header: "#0c0e15",
  blue: "#4a9fd4", green: "#5cb85c", yellow: "#f0ad4e", red: "#d9534f",
  purple: "#a78bfa", cyan: "#00b4d8",
  gray: "#8a8fa8", text: "#d4d8e8", dim: "#5a607a",
};
const font = "'IBM Plex Sans', system-ui, sans-serif";
const mono = "'IBM Plex Mono', monospace";

const GaugeRing = ({ value, label, color, size = 80 }) => {
  const r = (size - 8) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (value / 100) * circ;
  const statusColor = value > 90 ? C.red : value > 70 ? C.yellow : color;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={C.border} strokeWidth={6} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={statusColor} strokeWidth={6}
          strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 0.8s ease" }} />
      </svg>
      <div style={{ position: "relative", marginTop: -size + 4, height: size - 8, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <div style={{ color: statusColor, fontSize: size > 70 ? 20 : 16, fontWeight: 700, fontFamily: mono }}>{Math.round(value)}%</div>
      </div>
      <div style={{ color: C.dim, fontSize: 11, fontWeight: 600, marginTop: 2 }}>{label}</div>
    </div>
  );
};

const StatCard = ({ label, value, sub, color = C.blue, icon }) => (
  <div style={{
    background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
    padding: "16px 20px", flex: 1, minWidth: 140,
  }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
      <div>
        <div style={{ color: C.dim, fontSize: 11, fontWeight: 600, letterSpacing: 0.3, marginBottom: 8 }}>{label}</div>
        <div style={{ color, fontSize: 28, fontWeight: 700, fontFamily: mono, lineHeight: 1 }}>{value}</div>
        {sub && <div style={{ color: C.dim, fontSize: 11, marginTop: 4 }}>{sub}</div>}
      </div>
      {icon && <div style={{ fontSize: 20, opacity: 0.3 }}>{icon}</div>}
    </div>
  </div>
);

const MiniChart = ({ data, color = C.blue, height = 50 }) => (
  <ResponsiveContainer width="100%" height={height}>
    <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
      <defs>
        <linearGradient id={`mc${color.replace("#","")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5}
        fill={`url(#mc${color.replace("#","")})`} dot={false} isAnimationActive={false} />
      <Tooltip
        contentStyle={{ background: C.card, border: `1px solid ${C.border}`, fontSize: 11, borderRadius: 4 }}
        formatter={v => [`${Math.round(v)}%`]}
        labelFormatter={() => ""}
      />
    </AreaChart>
  </ResponsiveContainer>
);

export default function Dashboard({ onNavigate }) {
  const [overview, setOverview] = useState(null);
  const [wsData, setWsData] = useState(null);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [ramHistory, setRamHistory] = useState([]);
  const [recommendations, setRecommendations] = useState(null);
  const wsRef = useRef(null);

  // Fetch overview
  useEffect(() => {
    const load = () => {
      fetch("/api/dashboard/overview").then(r => r.json()).then(setOverview).catch(() => {});
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  // Fetch right-sizing recommendations
  useEffect(() => {
    fetch("/api/recommendations/rightsizing").then(r => r.json()).then(setRecommendations).catch(() => {});
  }, []);

  // WebSocket for real-time metrics
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/metrics`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setWsData(data);
      setCpuHistory(prev => {
        const next = [...prev, { t: prev.length, v: data.host_cpu }];
        return next.length > 60 ? next.slice(-60) : next;
      });
      setRamHistory(prev => {
        const next = [...prev, { t: prev.length, v: data.host_ram }];
        return next.length > 60 ? next.slice(-60) : next;
      });
    };

    return () => ws.close();
  }, []);

  if (!overview) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", background: C.bg, color: C.dim }}>
        Loading dashboard...
      </div>
    );
  }

  const d = overview;
  const uptime = d.host?.uptime_seconds || 0;
  const uptimeStr = uptime > 86400
    ? `${Math.floor(uptime / 86400)}d ${Math.floor((uptime % 86400) / 3600)}h`
    : uptime > 3600
    ? `${Math.floor(uptime / 3600)}h ${Math.floor((uptime % 3600) / 60)}m`
    : `${Math.floor(uptime / 60)}m`;

  return (
    <div style={{ height: "100%", overflow: "auto", background: C.bg, fontFamily: font, color: C.text }}>
      <div style={{ padding: 20, maxWidth: 1400, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: C.text }}>Cluster Dashboard</h2>
            <div style={{ color: C.dim, fontSize: 12, marginTop: 2 }}>
              Real-time overview {d.demo_mode ? "(Demo Mode)" : ""}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {d.ai_available && (
              <span style={{ background: C.green + "22", color: C.green, border: `1px solid ${C.green}44`,
                borderRadius: 4, fontSize: 10, padding: "3px 8px", fontFamily: mono, fontWeight: 700 }}>
                AI Online
              </span>
            )}
            <span style={{ color: C.dim, fontSize: 11, fontFamily: mono }}>Uptime: {uptimeStr}</span>
          </div>
        </div>

        {/* Top stat cards */}
        <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
          <StatCard label="VIRTUAL MACHINES" value={d.vms.total} sub={`${d.vms.on} running, ${d.vms.off} off`} color={C.blue} icon="🖥" />
          <StatCard label="TOTAL vCPU" value={d.resources.total_vcpu} sub="allocated across VMs" color={C.purple} icon="⚡" />
          <StatCard label="TOTAL RAM" value={`${d.resources.total_ram_gb}G`} sub="allocated across VMs" color={C.cyan} icon="📊" />
          <StatCard label="ALERTS" value={d.alerts.unacknowledged}
            sub={d.alerts.unacknowledged > 0 ? "unacknowledged" : "all clear"}
            color={d.alerts.unacknowledged > 0 ? C.red : C.green} icon="🔔" />
        </div>

        {/* Gauges + Charts row */}
        <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>

          {/* Resource Gauges */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: 20, display: "flex", gap: 24, alignItems: "center", justifyContent: "center" }}>
            <GaugeRing value={wsData?.host_cpu ?? d.host.cpu_pct} label="CPU" color={C.blue} size={90} />
            <GaugeRing value={wsData?.host_ram ?? d.host.ram_pct} label="Memory" color={C.purple} size={90} />
            <GaugeRing value={d.host.disk_pct} label="Disk" color={C.green} size={90} />
          </div>

          {/* CPU Chart */}
          <div style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "16px 20px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ color: C.dim, fontSize: 11, fontWeight: 600 }}>CPU USAGE (LIVE)</span>
              <span style={{ color: C.blue, fontSize: 12, fontFamily: mono }}>{Math.round(wsData?.host_cpu ?? d.host.cpu_pct)}%</span>
            </div>
            {cpuHistory.length > 2 ? (
              <MiniChart data={cpuHistory} color={C.blue} height={80} />
            ) : (
              <div style={{ height: 80, display: "flex", alignItems: "center", justifyContent: "center", color: C.dim, fontSize: 11 }}>
                Collecting data...
              </div>
            )}
          </div>

          {/* RAM Chart */}
          <div style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "16px 20px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ color: C.dim, fontSize: 11, fontWeight: 600 }}>MEMORY USAGE (LIVE)</span>
              <span style={{ color: C.purple, fontSize: 12, fontFamily: mono }}>{Math.round(wsData?.host_ram ?? d.host.ram_pct)}%</span>
            </div>
            {ramHistory.length > 2 ? (
              <MiniChart data={ramHistory} color={C.purple} height={80} />
            ) : (
              <div style={{ height: 80, display: "flex", alignItems: "center", justifyContent: "center", color: C.dim, fontSize: 11 }}>
                Collecting data...
              </div>
            )}
          </div>
        </div>

        {/* VM Status + Storage row */}
        <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>

          {/* VM List */}
          <div style={{ flex: 2, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden" }}>
            <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: C.dim, fontSize: 11, fontWeight: 700, letterSpacing: 0.5 }}>VIRTUAL MACHINES</span>
              <button onClick={() => onNavigate("console")} style={{
                background: "transparent", color: C.blue, border: "none", fontSize: 11,
                cursor: "pointer", fontFamily: font,
              }}>
                View All →
              </button>
            </div>
            {wsData?.vms?.map(vm => (
              <div key={vm.name} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "8px 16px", borderBottom: `1px solid ${C.border}22`,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{
                    width: 6, height: 6, borderRadius: "50%",
                    background: vm.state === "poweredOn" ? C.green : vm.state === "suspended" ? C.yellow : C.red,
                  }} />
                  <span style={{ color: C.text, fontSize: 12 }}>{vm.name}</span>
                </div>
                <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ color: C.dim, fontSize: 9 }}>CPU</div>
                    <div style={{ color: vm.cpu_pct > 80 ? C.red : C.blue, fontSize: 11, fontFamily: mono }}>
                      {Math.round(vm.cpu_pct)}%
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ color: C.dim, fontSize: 9 }}>RAM</div>
                    <div style={{ color: (vm.ram_pct || 0) > 85 ? C.red : C.purple, fontSize: 11, fontFamily: mono }}>
                      {Math.round(vm.ram_pct || 0)}%
                    </div>
                  </div>
                </div>
              </div>
            )) || (
              <div style={{ padding: 16, color: C.dim, fontSize: 12, textAlign: "center" }}>
                Waiting for WebSocket data...
              </div>
            )}
          </div>

          {/* Storage */}
          <div style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden" }}>
            <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}` }}>
              <span style={{ color: C.dim, fontSize: 11, fontWeight: 700, letterSpacing: 0.5 }}>STORAGE</span>
            </div>
            <StoragePools />
          </div>
        </div>

        {/* Recommendations */}
        {recommendations?.recommendations?.length > 0 && (
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden", marginBottom: 20 }}>
            <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: C.dim, fontSize: 11, fontWeight: 700, letterSpacing: 0.5 }}>
                AI RIGHT-SIZING RECOMMENDATIONS
              </span>
              <span style={{ color: C.cyan, fontSize: 10, fontFamily: mono }}>
                {recommendations.summary.total} suggestions
              </span>
            </div>
            {recommendations.recommendations.slice(0, 5).map((rec, i) => (
              <div key={i} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "10px 16px", borderBottom: `1px solid ${C.border}22`,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{
                    background: rec.type.includes("downsize") ? C.green + "22" : C.yellow + "22",
                    color: rec.type.includes("downsize") ? C.green : C.yellow,
                    border: `1px solid ${rec.type.includes("downsize") ? C.green : C.yellow}44`,
                    borderRadius: 3, fontSize: 9, padding: "2px 6px", fontFamily: mono, fontWeight: 700,
                  }}>
                    {rec.type.includes("downsize") ? "SAVE" : "GROW"}
                  </span>
                  <div>
                    <div style={{ color: C.text, fontSize: 12, fontWeight: 500 }}>{rec.vm}</div>
                    <div style={{ color: C.dim, fontSize: 11 }}>{rec.reason}</div>
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ color: C.text, fontSize: 11, fontFamily: mono }}>
                    {rec.current} → {rec.recommended}
                  </div>
                  <div style={{ color: C.dim, fontSize: 10 }}>{rec.savings}</div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Quick actions */}
        <div style={{ display: "flex", gap: 12 }}>
          {[
            { label: "Run AI Health Scan", icon: "🔍", action: () => onNavigate("ai"), color: C.cyan },
            { label: "Simulate Failover", icon: "⚡", action: () => onNavigate("ha"), color: C.red },
            { label: "Create New VM", icon: "＋", action: () => onNavigate("console"), color: C.green },
            { label: "View API Docs", icon: "📄", action: () => window.open("/api/docs", "_blank"), color: C.blue },
          ].map(btn => (
            <button key={btn.label} onClick={btn.action} style={{
              flex: 1, background: btn.color + "11", border: `1px solid ${btn.color}33`,
              borderRadius: 6, padding: "14px 16px", cursor: "pointer",
              display: "flex", alignItems: "center", gap: 10,
              color: btn.color, fontSize: 12, fontWeight: 600, fontFamily: font,
              transition: "all 0.15s",
            }}>
              <span style={{ fontSize: 16 }}>{btn.icon}</span>
              {btn.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function StoragePools() {
  const [pools, setPools] = useState([]);

  useEffect(() => {
    fetch("/api/storage").then(r => r.json()).then(setPools).catch(() => {});
  }, []);

  return (
    <div style={{ padding: "8px 16px" }}>
      {pools.map(s => {
        const usedPct = s.capacity_gb ? Math.round((s.capacity_gb - s.free_gb) / s.capacity_gb * 100) : 0;
        return (
          <div key={s.name} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <span style={{ color: C.text }}>{s.name}</span>
              <span style={{ color: C.dim, fontFamily: mono, fontSize: 10 }}>{usedPct}%</span>
            </div>
            <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: "hidden" }}>
              <div style={{
                width: `${usedPct}%`, height: "100%", borderRadius: 3,
                background: usedPct > 85 ? C.red : usedPct > 70 ? C.yellow : C.green,
                transition: "width 0.6s",
              }} />
            </div>
            <div style={{ color: C.dim, fontSize: 10, marginTop: 2 }}>
              {Math.round(s.free_gb).toLocaleString()} GB free
            </div>
          </div>
        );
      })}
    </div>
  );
}
