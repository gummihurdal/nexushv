import { useState, useEffect, useRef } from "react";

/**
 * NexusHV — HA Dashboard Panel
 * Drop this into the main console as a new nav item.
 * Polls /ha/status every 3s from the HA daemon (port 8081).
 */

const C = {
  bg:"#1b1e27",panel:"#1f2330",card:"#252938",border:"#2e3347",
  toolbar:"#0f111a",header:"#0c0e15",
  blue:"#4a9fd4",green:"#5cb85c",yellow:"#f0ad4e",red:"#d9534f",
  gray:"#8a8fa8",text:"#d4d8e8",dim:"#5a607a",
};
const font="'IBM Plex Sans',system-ui,sans-serif";
const mono="'IBM Plex Mono',monospace";

// ── Simulated HA state (replace with real API fetch in production) ─────────
const MOCK_STATE = {
  local_ip: "10.0.1.10",
  is_master: true,
  peers: {
    "10.0.1.11": { ip:"10.0.1.11", hostname:"esxi-prod-02", state:"alive",   last_hb: Date.now()/1000-0.8, fenced:false },
    "10.0.1.12": { ip:"10.0.1.12", hostname:"esxi-dev-01",  state:"alive",   last_hb: Date.now()/1000-1.1, fenced:false },
    "10.0.1.13": { ip:"10.0.1.13", hostname:"esxi-dev-02",  state:"suspect", last_hb: Date.now()/1000-4.2, fenced:false },
  },
  vm_policies: {
    "prod-db-primary":  { name:"prod-db-primary",  priority:1, max_restarts:3, restart_count:0, restart_delay_s:0 },
    "prod-web-01":      { name:"prod-web-01",       priority:1, max_restarts:3, restart_count:0, restart_delay_s:5 },
    "k8s-master-01":    { name:"k8s-master-01",     priority:2, max_restarts:3, restart_count:0, restart_delay_s:10 },
    "k8s-worker-01":    { name:"k8s-worker-01",     priority:2, max_restarts:3, restart_count:1, restart_delay_s:15 },
    "dev-sandbox-01":   { name:"dev-sandbox-01",    priority:0, max_restarts:1, restart_count:0, restart_delay_s:0 },
    "win-rdp-01":       { name:"win-rdp-01",        priority:3, max_restarts:2, restart_count:0, restart_delay_s:30 },
    "backup-appliance": { name:"backup-appliance",  priority:3, max_restarts:1, restart_count:0, restart_delay_s:60 },
  },
  events: [],
};

const PRIORITY_LABEL = { 0:"Do Not Restart", 1:"High", 2:"Medium", 3:"Low" };
const PRIORITY_COLOR = { 0:C.dim, 1:C.red, 2:C.yellow, 3:C.green };
const STATE_COLOR = { alive:C.green, suspect:C.yellow, isolated:"#f97316", dead:C.red, fencing:"#e879f9" };
const STATE_LABEL = { alive:"Connected", suspect:"Suspect", isolated:"Isolated", dead:"Dead", fencing:"Fencing" };

const Tag = ({label,col=C.blue,small})=>(
  <span style={{background:col+"22",color:col,border:`1px solid ${col}44`,borderRadius:3,
    fontSize:small?9:10,padding:small?"0px 4px":"1px 7px",fontFamily:mono,fontWeight:700,letterSpacing:0.5,whiteSpace:"nowrap"}}>
    {label}
  </span>
);

const HBPulse = ({active}) => {
  const [on,setOn]=useState(true);
  useEffect(()=>{
    if(!active)return;
    const id=setInterval(()=>setOn(v=>!v),800);
    return ()=>clearInterval(id);
  },[active]);
  return <div style={{width:8,height:8,borderRadius:"50%",background:active?(on?C.green:"#1a3a1a"):C.red,transition:"background 0.3s",flexShrink:0}}/>;
};

// ── Simulate a failover for demo ──────────────────────────────────────────
function buildTimeline(events) {
  return [
    ...events,
    { ts:Date.now()/1000-420, event_type:"host_failed",    host:"10.0.1.13", vm:null,             detail:"No heartbeat for 6s" },
    { ts:Date.now()/1000-417, event_type:"fencing",        host:"10.0.1.13", vm:null,             detail:"STONITH via IPMI — power off" },
    { ts:Date.now()/1000-414, event_type:"vm_restarting",  host:"10.0.1.11", vm:"k8s-master-01",  detail:"Priority: High" },
    { ts:Date.now()/1000-412, event_type:"vm_started",     host:"10.0.1.11", vm:"k8s-master-01",  detail:"Restarted in 2.1s" },
    { ts:Date.now()/1000-410, event_type:"vm_restarting",  host:"10.0.1.10", vm:"k8s-worker-01",  detail:"Priority: High" },
    { ts:Date.now()/1000-408, event_type:"vm_started",     host:"10.0.1.10", vm:"k8s-worker-01",  detail:"Restarted in 1.8s" },
    { ts:Date.now()/1000-390, event_type:"vm_restarting",  host:"10.0.1.11", vm:"dev-sandbox-01", detail:"Priority: Medium" },
    { ts:Date.now()/1000-388, event_type:"vm_started",     host:"10.0.1.11", vm:"dev-sandbox-01", detail:"Restarted in 1.4s" },
  ].sort((a,b)=>b.ts-a.ts);
}

const EVENT_ICON = {
  host_failed:   { icon:"⚠", col:C.red },
  fencing:       { icon:"⚡", col:"#e879f9" },
  vm_restarting: { icon:"↻", col:C.yellow },
  vm_started:    { icon:"✓", col:C.green },
  vm_failed:     { icon:"✗", col:C.red },
};

const fmtTime = (ts) => {
  const d = new Date(ts*1000);
  return d.toTimeString().slice(0,8);
};
const fmtAgo = (ts) => {
  const s = Math.round(Date.now()/1000 - ts);
  if(s<60) return `${s}s ago`;
  if(s<3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
};

// ── Policy editor modal ───────────────────────────────────────────────────
const PolicyModal = ({vm, policy, onSave, onClose}) => {
  const [p,setP]=useState({...policy});
  return (
    <div style={{position:"fixed",inset:0,background:"#00000090",zIndex:300,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:font}}>
      <div style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:6,width:420,boxShadow:"0 20px 60px #000"}}>
        <div style={{background:C.header,padding:"14px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",borderBottom:`1px solid ${C.border}`,borderRadius:"6px 6px 0 0"}}>
          <span style={{color:C.text,fontWeight:600}}>Edit HA Policy — {vm}</span>
          <button onClick={onClose} style={{background:"none",border:"none",color:C.dim,fontSize:18,cursor:"pointer"}}>✕</button>
        </div>
        <div style={{padding:24,display:"flex",flexDirection:"column",gap:20}}>
          <div>
            <div style={{color:C.gray,fontSize:12,marginBottom:8}}>Restart Priority</div>
            <div style={{display:"flex",gap:8}}>
              {[[0,"Disabled"],[1,"High"],[2,"Medium"],[3,"Low"]].map(([v,l])=>(
                <button key={v} onClick={()=>setP(x=>({...x,priority:v}))}
                  style={{flex:1,padding:"8px 0",borderRadius:4,fontSize:12,cursor:"pointer",fontFamily:font,fontWeight:600,
                    background:p.priority===v?PRIORITY_COLOR[v]+"22":"transparent",
                    color:p.priority===v?PRIORITY_COLOR[v]:C.dim,
                    border:`1px solid ${p.priority===v?PRIORITY_COLOR[v]+"55":C.border}`}}>
                  {l}
                </button>
              ))}
            </div>
          </div>
          <Row label="Max Restarts">
            <input type="number" min={0} max={10} value={p.max_restarts}
              onChange={e=>setP(x=>({...x,max_restarts:+e.target.value}))}
              style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,color:C.text,
                padding:"7px 10px",fontSize:13,fontFamily:mono,width:80,outline:"none"}}/>
          </Row>
          <Row label="Restart Delay (seconds)">
            <input type="number" min={0} max={300} value={p.restart_delay_s}
              onChange={e=>setP(x=>({...x,restart_delay_s:+e.target.value}))}
              style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,color:C.text,
                padding:"7px 10px",fontSize:13,fontFamily:mono,width:80,outline:"none"}}/>
          </Row>
          <div style={{background:C.card,borderRadius:5,padding:"10px 14px",color:C.dim,fontSize:12,lineHeight:1.6}}>
            {p.priority===0&&"⚠ VM will not restart on host failure."}
            {p.priority===1&&"✓ Restarted first — reserve for databases and core services."}
            {p.priority===2&&"✓ Restarted after High priority VMs have started."}
            {p.priority===3&&`✓ Restarted last${p.restart_delay_s?` after ${p.restart_delay_s}s delay`:""}.`}
          </div>
        </div>
        <div style={{padding:"12px 24px",borderTop:`1px solid ${C.border}`,display:"flex",gap:8,justifyContent:"flex-end"}}>
          <Btn secondary onClick={onClose}>Cancel</Btn>
          <Btn onClick={()=>{onSave(vm,p);onClose();}}>Save Policy</Btn>
        </div>
      </div>
    </div>
  );
};

const Row=({label,children})=>(
  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
    <span style={{color:C.gray,fontSize:12}}>{label}</span>
    {children}
  </div>
);
const Btn=({children,onClick,secondary,danger})=>(
  <button onClick={onClick} style={{
    background:danger?C.red+"22":secondary?"transparent":C.blue+"22",
    color:danger?C.red:secondary?C.gray:C.blue,
    border:`1px solid ${danger?C.red+"44":secondary?C.border:C.blue+"44"}`,
    borderRadius:4,padding:"7px 16px",fontSize:12,cursor:"pointer",fontFamily:font,fontWeight:600}}>
    {children}
  </button>
);

// ── Main HA Dashboard ──────────────────────────────────────────────────────
export default function HADashboard() {
  const [state,setState]=useState(MOCK_STATE);
  const [events,setEvents]=useState(buildTimeline([]));
  const [editVM,setEditVM]=useState(null);
  const [tab,setTab]=useState("overview");
  const [simulating,setSimulating]=useState(false);
  const [simStep,setSimStep]=useState(-1);
  const [haConnected,setHaConnected]=useState(false);
  const timerRef=useRef();

  // Fetch real HA state from API proxy
  useEffect(()=>{
    const load = () => {
      fetch("/api/ha/status")
        .then(r=>r.json())
        .then(data=>{
          if(data && !data.error && data.peers){
            setState(data);
            setHaConnected(true);
            if(data.events && data.events.length>0){
              setEvents(prev=>{
                const merged=[...data.events,...prev.filter(e=>!data.events.some(de=>Math.abs(de.ts-e.ts)<0.1))];
                return merged.sort((a,b)=>b.ts-a.ts).slice(0,100);
              });
            }
          }
        })
        .catch(()=>setHaConnected(false));
    };
    load();
    const id=setInterval(load,3000);
    return ()=>clearInterval(id);
  },[]);

  // Pulse last_hb to keep HB fresh in demo (only if not connected to real HA)
  useEffect(()=>{
    if(haConnected) return;
    const id=setInterval(()=>{
      setState(s=>({...s,peers:Object.fromEntries(
        Object.entries(s.peers).map(([ip,p])=>[ip,p.state==="alive"?{...p,last_hb:Date.now()/1000-Math.random()*1.5}:p])
      )}));
    },1200);
    return ()=>clearInterval(id);
  },[haConnected]);

  const savePolicy=(vm,policy)=>{
    setState(s=>({...s,vm_policies:{...s.vm_policies,[vm]:{...policy,name:vm}}}));
  };

  // Simulate a host failure end-to-end
  const simulateFailure = () => {
    if(simulating)return;
    setSimulating(true);
    setSimStep(0);

    // If connected to real HA, trigger via API
    if(haConnected){
      fetch("/api/ha/simulate/fail/10.0.1.13", { method: "POST" })
        .then(()=>{
          // The real-time polling will pick up state changes
          let step = 0;
          const iv = setInterval(()=>{
            setSimStep(step++);
            if(step >= 5){ clearInterval(iv); setSimulating(false); setSimStep(-1); }
          }, 1500);
        })
        .catch(()=>{ setSimulating(false); setSimStep(-1); });
      return;
    }

    const steps = [
      ()=>setState(s=>({...s,peers:{...s.peers,"10.0.1.13":{...s.peers["10.0.1.13"],state:"suspect"}}})),
      ()=>{ setState(s=>({...s,peers:{...s.peers,"10.0.1.13":{...s.peers["10.0.1.13"],state:"dead"}}}));
            setEvents(e=>[{ts:Date.now()/1000,event_type:"host_failed",host:"10.0.1.13",vm:null,detail:"No heartbeat for 6s"},...e]); },
      ()=>{ setState(s=>({...s,peers:{...s.peers,"10.0.1.13":{...s.peers["10.0.1.13"],state:"fencing"}}}));
            setEvents(e=>[{ts:Date.now()/1000,event_type:"fencing",host:"10.0.1.13",vm:null,detail:"STONITH via IPMI — powering off"},...e]); },
      ()=>{ setState(s=>({...s,peers:{...s.peers,"10.0.1.13":{...s.peers["10.0.1.13"],fenced:true,state:"dead"}}}));
            setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_restarting",host:"10.0.1.11",vm:"prod-db-primary",detail:"Priority: High — restarting"},...e]); },
      ()=>setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_started",host:"10.0.1.11",vm:"prod-db-primary",detail:"✓ Restarted in 1.9s"},...e]),
      ()=>setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_restarting",host:"10.0.1.10",vm:"k8s-master-01",detail:"Priority: High — restarting"},...e]),
      ()=>setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_started",host:"10.0.1.10",vm:"k8s-master-01",detail:"✓ Restarted in 2.1s"},...e]),
      ()=>setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_restarting",host:"10.0.1.11",vm:"win-rdp-01",detail:"Priority: Low — delay 30s"},...e]),
      ()=>{ setEvents(e=>[{ts:Date.now()/1000,event_type:"vm_started",host:"10.0.1.11",vm:"win-rdp-01",detail:"✓ Restarted in 1.6s"},...e]);
            setSimulating(false); setSimStep(-1); },
    ];
    let i=0;
    const run=()=>{ if(i<steps.length){steps[i]();setSimStep(i);i++;timerRef.current=setTimeout(run,i<=2?2000:1200);} };
    run();
  };

  useEffect(()=>()=>clearTimeout(timerRef.current),[]);

  const localHost = { ip:state.local_ip, hostname:"esxi-prod-01", state:"alive", last_hb:Date.now()/1000-0.5 };
  const allPeers  = [localHost, ...Object.values(state.peers)];
  const aliveCount = allPeers.filter(p=>p.state==="alive").length;
  const totalCount = allPeers.length;
  const policies  = state.vm_policies;

  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",fontFamily:font,background:C.bg,color:C.text}}>

      {/* Header */}
      <div style={{background:C.header,padding:"12px 20px",borderBottom:`1px solid ${C.border}`,
        display:"flex",alignItems:"center",justifyContent:"space-between",flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <div style={{width:32,height:32,background:`linear-gradient(135deg,${C.red},${C.yellow})`,borderRadius:6,
            display:"flex",alignItems:"center",justifyContent:"center",fontSize:16}}>⛨</div>
          <div>
            <div style={{color:C.text,fontWeight:700,fontSize:14}}>High Availability</div>
            <div style={{color:C.dim,fontSize:11}}>NexusHV HA — Production Cluster</div>
          </div>
        </div>
        <div style={{display:"flex",gap:10,alignItems:"center"}}>
          <Tag label={state.is_master?"HA Master":"HA Secondary"} col={state.is_master?C.blue:C.gray}/>
          <Tag label={`${aliveCount}/${totalCount} Hosts`} col={aliveCount===totalCount?C.green:C.yellow}/>
          <button onClick={simulateFailure} disabled={simulating}
            style={{background:simulating?C.border:C.red+"22",color:simulating?C.dim:C.red,
              border:`1px solid ${simulating?C.border:C.red+"44"}`,borderRadius:4,padding:"7px 14px",
              fontSize:12,cursor:simulating?"default":"pointer",fontFamily:font,fontWeight:600,transition:"all 0.2s"}}>
            {simulating?"⟳ Failover Running...":"⚡ Simulate Host Failure"}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{display:"flex",background:C.toolbar,borderBottom:`1px solid ${C.border}`,flexShrink:0}}>
        {[["overview","Overview"],["hosts","Hosts & Heartbeat"],["policies","VM Restart Policies"],["events","Failover Events"]].map(([id,label])=>(
          <div key={id} onClick={()=>setTab(id)}
            style={{padding:"10px 20px",fontSize:12,cursor:"pointer",
              color:tab===id?C.blue:C.dim,
              borderBottom:tab===id?`2px solid ${C.blue}`:"2px solid transparent",
              background:tab===id?C.tabAct:undefined,transition:"all 0.15s"}}>
            {label}
          </div>
        ))}
      </div>

      <div style={{flex:1,overflow:"auto",padding:20}}>

        {/* ── Overview ── */}
        {tab==="overview"&&(
          <div style={{display:"flex",flexDirection:"column",gap:16}}>

            {/* Status cards */}
            <div style={{display:"flex",gap:12}}>
              {[
                {label:"HA Status",value:"Enabled",sub:"Cluster protected",col:C.green},
                {label:"Protected VMs",value:Object.values(policies).filter(p=>p.priority>0).length,sub:"will restart on failure",col:C.blue},
                {label:"Admission Control",value:"Enabled",sub:"1-host failure tolerance",col:C.blue},
                {label:"Failovers (24h)",value:events.filter(e=>e.event_type==="host_failed").length,sub:"host failures",col:events.filter(e=>e.event_type==="host_failed").length>0?C.yellow:C.green},
              ].map(c=>(
                <div key={c.label} style={{flex:1,background:C.card,border:`1px solid ${C.border}`,borderRadius:6,padding:16}}>
                  <div style={{color:C.dim,fontSize:11,marginBottom:6}}>{c.label}</div>
                  <div style={{color:c.col,fontSize:24,fontWeight:700,fontFamily:mono,marginBottom:4}}>{c.value}</div>
                  <div style={{color:C.dim,fontSize:11}}>{c.sub}</div>
                </div>
              ))}
            </div>

            {/* Admission control */}
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:6,padding:16}}>
              <div style={{color:C.text,fontWeight:600,marginBottom:12}}>Admission Control — Failover Capacity</div>
              <div style={{display:"flex",gap:24}}>
                {[
                  {label:"CPU Failover Capacity",used:61,reserved:25,col:C.blue},
                  {label:"Memory Failover Capacity",used:69,reserved:25,col:"#a78bfa"},
                ].map(m=>(
                  <div key={m.label} style={{flex:1}}>
                    <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
                      <span style={{color:C.dim,fontSize:12}}>{m.label}</span>
                      <span style={{color:C.dim,fontSize:11,fontFamily:mono}}>{m.used}% used</span>
                    </div>
                    <div style={{height:12,background:C.border,borderRadius:3,overflow:"hidden",position:"relative"}}>
                      <div style={{width:`${m.used}%`,height:"100%",background:m.col,borderRadius:3}}/>
                      {/* Reserved band */}
                      <div style={{position:"absolute",right:0,top:0,width:`${m.reserved}%`,height:"100%",
                        background:`repeating-linear-gradient(45deg,${C.yellow}22,${C.yellow}22 3px,transparent 3px,transparent 8px)`,
                        borderLeft:`1px dashed ${C.yellow}88`}}/>
                    </div>
                    <div style={{display:"flex",justifyContent:"space-between",marginTop:4}}>
                      <span style={{color:C.dim,fontSize:10}}>Current usage</span>
                      <span style={{color:C.yellow,fontSize:10}}>{m.reserved}% reserved for failover</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Failover sequence diagram */}
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:6,padding:16}}>
              <div style={{color:C.text,fontWeight:600,marginBottom:14}}>Failover Sequence</div>
              <div style={{display:"flex",gap:0,alignItems:"center",flexWrap:"wrap"}}>
                {[
                  {step:"1",label:"Detect Failure",detail:"HB timeout 6s",col:C.red,   active:simStep>=0},
                  {step:"2",label:"STONITH Fence", detail:"IPMI power-off",col:"#e879f9",active:simStep>=2},
                  {step:"3",label:"Admission Ctrl",detail:"Check capacity",col:C.blue,  active:simStep>=3},
                  {step:"4",label:"Sort by Priority",detail:"High → Low",  col:C.yellow,active:simStep>=3},
                  {step:"5",label:"Restart VMs",   detail:"Pick best host",col:C.green, active:simStep>=4},
                ].map((s,i,arr)=>(
                  <div key={s.step} style={{display:"flex",alignItems:"center"}}>
                    <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,opacity:s.active?1:0.35,transition:"opacity 0.5s"}}>
                      <div style={{width:40,height:40,borderRadius:"50%",background:s.col+(s.active?"33":"11"),
                        border:`2px solid ${s.col+(s.active?"":"44")}`,display:"flex",alignItems:"center",
                        justifyContent:"center",color:s.col,fontWeight:700,fontSize:14,
                        boxShadow:s.active?`0 0 12px ${s.col}44`:undefined}}>
                        {s.step}
                      </div>
                      <div style={{color:s.active?C.text:C.dim,fontSize:11,fontWeight:600,textAlign:"center"}}>{s.label}</div>
                      <div style={{color:C.dim,fontSize:10,textAlign:"center"}}>{s.detail}</div>
                    </div>
                    {i<arr.length-1&&<div style={{width:32,height:2,background:C.border,margin:"0 4px",marginBottom:20}}/>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Hosts & Heartbeat ── */}
        {tab==="hosts"&&(
          <div style={{display:"flex",flexDirection:"column",gap:10}}>
            {allPeers.map(peer=>{
              const age = Date.now()/1000 - (peer.last_hb||0);
              const isLocal = peer.ip === state.local_ip;
              return (
                <div key={peer.ip} style={{background:C.card,border:`1px solid ${peer.state==="dead"||peer.state==="fencing"?C.red+"66":peer.state==="suspect"?C.yellow+"44":C.border}`,
                  borderRadius:6,padding:16,transition:"border-color 0.4s"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <div style={{display:"flex",gap:12,alignItems:"center"}}>
                      <HBPulse active={peer.state==="alive"}/>
                      <div>
                        <div style={{display:"flex",gap:8,alignItems:"center"}}>
                          <span style={{color:C.text,fontWeight:600}}>{peer.hostname||peer.ip}</span>
                          {isLocal&&<Tag label="This Host" col={C.blue} small/>}
                          {state.is_master&&isLocal&&<Tag label="HA Master" col="#a78bfa" small/>}
                        </div>
                        <div style={{color:C.dim,fontSize:11,marginTop:2,fontFamily:mono}}>{peer.ip}</div>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:10,alignItems:"center"}}>
                      <div style={{textAlign:"right"}}>
                        <div style={{color:C.dim,fontSize:10,marginBottom:2}}>Last Heartbeat</div>
                        <div style={{color:age<2?C.green:age<5?C.yellow:C.red,fontSize:12,fontFamily:mono}}>
                          {peer.state==="alive"?`${age.toFixed(1)}s ago`:"—"}
                        </div>
                      </div>
                      {peer.fenced&&<Tag label="FENCED" col={C.red}/>}
                      <Tag label={STATE_LABEL[peer.state]||peer.state} col={STATE_COLOR[peer.state]||C.gray}/>
                    </div>
                  </div>
                  {(peer.state==="suspect"||peer.state==="dead"||peer.state==="fencing")&&(
                    <div style={{marginTop:10,background:C.red+"0d",border:`1px solid ${C.red}33`,borderRadius:4,padding:"8px 12px",fontSize:12,color:C.gray}}>
                      {peer.state==="suspect"&&`⚠ Heartbeat missed — last seen ${age.toFixed(1)}s ago. Monitoring...`}
                      {peer.state==="fencing"&&"⚡ STONITH fencing in progress — waiting for IPMI confirmation..."}
                      {peer.state==="dead"&&`✗ Host declared dead${peer.fenced?" — fenced successfully":""}.  VMs will be restarted on surviving hosts.`}
                    </div>
                  )}
                  {/* Heartbeat timeline bar */}
                  <div style={{marginTop:10}}>
                    <div style={{color:C.dim,fontSize:10,marginBottom:4}}>Heartbeat history (last 30s)</div>
                    <div style={{display:"flex",gap:2}}>
                      {Array.from({length:30},(_,i)=>{
                        const ok = peer.state==="alive" || i < 24;
                        return <div key={i} style={{flex:1,height:8,borderRadius:1,
                          background:ok?C.green+(i>26?"":"88"):i>26?C.red:C.yellow,transition:"background 0.3s"}}/>;
                      })}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* ── VM Restart Policies ── */}
        {tab==="policies"&&(
          <div style={{display:"flex",flexDirection:"column",gap:1}}>
            <div style={{display:"grid",gridTemplateColumns:"1fr 120px 100px 100px 120px",
              padding:"8px 16px",background:C.toolbar,borderRadius:"5px 5px 0 0",
              color:C.dim,fontSize:11,fontWeight:600,letterSpacing:0.3}}>
              <span>Virtual Machine</span><span>Priority</span><span>Max Restarts</span><span>Restarted</span><span>Delay</span>
            </div>
            {Object.values(policies).map((p,i)=>(
              <div key={p.name} onClick={()=>setEditVM(p.name)}
                style={{display:"grid",gridTemplateColumns:"1fr 120px 100px 100px 120px",
                  padding:"10px 16px",background:i%2===0?C.card:C.panel,
                  cursor:"pointer",transition:"background 0.1s",alignItems:"center",
                  borderBottom:`1px solid ${C.border}22`}}
                onMouseEnter={e=>e.currentTarget.style.background=C.blue+"0d"}
                onMouseLeave={e=>e.currentTarget.style.background=i%2===0?C.card:C.panel}>
                <span style={{color:C.text,fontSize:12,fontWeight:500}}>{p.name}</span>
                <span><Tag label={PRIORITY_LABEL[p.priority]} col={PRIORITY_COLOR[p.priority]}/></span>
                <span style={{color:C.dim,fontSize:12,fontFamily:mono}}>{p.max_restarts}</span>
                <span style={{color:p.restart_count>0?C.yellow:C.dim,fontSize:12,fontFamily:mono}}>
                  {p.restart_count}/{p.max_restarts}
                </span>
                <span style={{color:C.dim,fontSize:12,fontFamily:mono}}>{p.restart_delay_s?`${p.restart_delay_s}s`:"—"}</span>
              </div>
            ))}
            <div style={{padding:"10px 16px",color:C.dim,fontSize:11,background:C.toolbar,borderRadius:"0 0 5px 5px"}}>
              Click any row to edit the restart policy for that VM.
            </div>
          </div>
        )}

        {/* ── Failover Events ── */}
        {tab==="events"&&(
          <div style={{display:"flex",flexDirection:"column",gap:0}}>
            <div style={{display:"grid",gridTemplateColumns:"80px 120px 140px 1fr",
              padding:"8px 16px",background:C.toolbar,borderRadius:"5px 5px 0 0",
              color:C.dim,fontSize:11,fontWeight:600}}>
              <span>Time</span><span>Event</span><span>Host</span><span>Detail</span>
            </div>
            {events.slice(0,40).map((e,i)=>{
              const meta = EVENT_ICON[e.event_type]||{icon:"·",col:C.gray};
              return (
                <div key={i} style={{display:"grid",gridTemplateColumns:"80px 120px 140px 1fr",
                  padding:"9px 16px",background:i%2===0?C.card:C.panel,
                  borderBottom:`1px solid ${C.border}22`,alignItems:"center"}}>
                  <span style={{color:C.dim,fontSize:11,fontFamily:mono}}>{fmtTime(e.ts)}</span>
                  <span style={{display:"flex",alignItems:"center",gap:5}}>
                    <span style={{color:meta.col,fontSize:14}}>{meta.icon}</span>
                    <Tag label={e.event_type.replace("_"," ")} col={meta.col} small/>
                  </span>
                  <span style={{color:C.dim,fontSize:11,fontFamily:mono}}>{e.host}</span>
                  <span style={{color:C.text,fontSize:12}}>
                    {e.vm&&<span style={{color:C.blue,marginRight:8}}>{e.vm}</span>}
                    {e.detail}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {editVM&&(
        <PolicyModal vm={editVM} policy={policies[editVM]} onSave={savePolicy} onClose={()=>setEditVM(null)}/>
      )}
    </div>
  );
}
