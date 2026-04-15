import { useState, useEffect, useRef } from "react";
import { AreaChart, Area, ResponsiveContainer, Tooltip } from "recharts";

// ── Palette (vSphere-familiar: steel blue + dark gray + accent green) ──────
const C = {
  bg:       "#1b1e27",
  sidebar:  "#13151c",
  panel:    "#1f2330",
  card:     "#252938",
  border:   "#2e3347",
  blue:     "#4a9fd4",
  blueHov:  "#5bb4ee",
  green:    "#5cb85c",
  yellow:   "#f0ad4e",
  red:      "#d9534f",
  gray:     "#8a8fa8",
  text:     "#d4d8e8",
  dim:      "#5a607a",
  accent:   "#00b4d8",
  toolbar:  "#0f111a",
  tab:      "#161924",
  tabAct:   "#1f2330",
  header:   "#0c0e15",
};

const font = "'IBM Plex Sans', system-ui, sans-serif";
const mono = "'IBM Plex Mono', monospace";

// ── Data ──────────────────────────────────────────────────────────────────
const genSparkline = (n=30,base=30,v=25) =>
  Array.from({length:n},(_,i)=>({t:i, v:Math.max(1,Math.min(99,base+(Math.random()-0.5)*v*2))}));

const DATACENTER = {
  name: "SNB-DC-01",
  clusters: [
    {
      id:"cl1", name:"Production-Cluster", ha:true, drs:true,
      hosts:[
        { id:"h1", name:"esxi-prod-01.snb.internal", ip:"10.0.1.10", model:"Dell PowerEdge R750",
          cpu:{sockets:2,cores:16,ghz:3.2,used:61}, ram:{total:256,used:178}, status:"connected",
          vms:["vm1","vm2","vm6"] },
        { id:"h2", name:"esxi-prod-02.snb.internal", ip:"10.0.1.11", model:"Dell PowerEdge R750",
          cpu:{sockets:2,cores:16,ghz:3.2,used:38}, ram:{total:256,used:102}, status:"connected",
          vms:["vm3","vm4","vm7"] },
      ]
    },
    {
      id:"cl2", name:"Dev-Cluster", ha:false, drs:false,
      hosts:[
        { id:"h3", name:"esxi-dev-01.snb.internal", ip:"10.0.1.20", model:"HPE ProLiant DL380",
          cpu:{sockets:2,cores:12,ghz:2.8,used:22}, ram:{total:128,used:48}, status:"connected",
          vms:["vm5"] },
        { id:"h4", name:"esxi-dev-02.snb.internal", ip:"10.0.1.21", model:"HPE ProLiant DL380",
          cpu:{sockets:2,cores:12,ghz:2.8,used:5}, ram:{total:128,used:18}, status:"maintenance",
          vms:[] },
      ]
    }
  ]
};

const DATASTORES = [
  {id:"ds1",name:"datastore-nvme-01",type:"VMFS 6",capacity:10240,free:3841,hosts:["h1","h2"],status:"normal"},
  {id:"ds2",name:"datastore-san-01", type:"VMFS 6",capacity:40960,free:22528,hosts:["h1","h2","h3"],status:"normal"},
  {id:"ds3",name:"nfs-backup-01",   type:"NFS 4.1",capacity:20480,free:16384,hosts:["h1","h2"],status:"normal"},
];

const NETWORKS = [
  {id:"net1",name:"VM Network",      type:"vSphere Standard Switch",vlan:0,  ports:120,uplink:"vmnic0"},
  {id:"net2",name:"vMotion-Network", type:"vSphere Standard Switch",vlan:100, ports:8, uplink:"vmnic1"},
  {id:"net3",name:"Storage-Network", type:"vSphere Distributed Switch",vlan:200,ports:8,uplink:"vmnic2"},
  {id:"net4",name:"Management",      type:"vSphere Standard Switch",vlan:0,  ports:8, uplink:"vmnic0"},
];

const initVMs = [
  {id:"vm1",name:"prod-db-primary",  os:"Ubuntu 22.04",cpu:8, ram:32, disk:500,hostId:"h1",state:"poweredOn", ip:"10.0.2.10",snapshot:true,  cpuSpark:genSparkline(30,65,30), ramSpark:genSparkline(30,82,12)},
  {id:"vm2",name:"prod-web-01",      os:"Debian 12",   cpu:4, ram:16, disk:100,hostId:"h1",state:"poweredOn", ip:"10.0.2.11",snapshot:false, cpuSpark:genSparkline(30,28,20), ramSpark:genSparkline(30,55,18)},
  {id:"vm3",name:"k8s-master-01",    os:"Rocky Linux 9",cpu:4,ram:8,  disk:80, hostId:"h2",state:"poweredOn", ip:"10.0.2.20",snapshot:true,  cpuSpark:genSparkline(30,18,15), ramSpark:genSparkline(30,42,15)},
  {id:"vm4",name:"k8s-worker-01",    os:"Rocky Linux 9",cpu:4,ram:8,  disk:80, hostId:"h2",state:"poweredOn", ip:"10.0.2.21",snapshot:false, cpuSpark:genSparkline(30,15,12), ramSpark:genSparkline(30,38,14)},
  {id:"vm5",name:"dev-sandbox-01",   os:"Ubuntu 20.04", cpu:2,ram:4,  disk:50, hostId:"h3",state:"poweredOff",ip:"—",         snapshot:false, cpuSpark:genSparkline(30,0,1),  ramSpark:genSparkline(30,0,1)},
  {id:"vm6",name:"win-rdp-01",       os:"Windows Server 2022",cpu:4,ram:16,disk:200,hostId:"h1",state:"suspended",ip:"10.0.2.30",snapshot:true,cpuSpark:genSparkline(30,2,3),ramSpark:genSparkline(30,60,5)},
  {id:"vm7",name:"backup-appliance", os:"Ubuntu 20.04", cpu:2,ram:4,  disk:4000,hostId:"h2",state:"poweredOn",ip:"10.0.2.40",snapshot:false, cpuSpark:genSparkline(30,5,6),  ramSpark:genSparkline(30,25,8)},
];

const TASKS = [
  {id:1,task:"Power On virtual machine",target:"dev-sandbox-01",    status:"Completed",time:"09:42:11",pct:100},
  {id:2,task:"Create virtual machine snapshot",target:"prod-db-primary",status:"Completed",time:"09:38:03",pct:100},
  {id:3,task:"Migrate virtual machine",target:"win-rdp-01",         status:"Completed",time:"09:22:47",pct:100},
  {id:4,task:"Reconfigure virtual machine",target:"k8s-master-01",  status:"Completed",time:"08:55:12",pct:100},
];

const allHosts = DATACENTER.clusters.flatMap(c=>c.hosts);
const findHost = id => allHosts.find(h=>h.id===id);
const findCluster = hid => DATACENTER.clusters.find(c=>c.hosts.some(h=>h.id===hid));

// ── Helpers ───────────────────────────────────────────────────────────────
const pct = (used,total) => Math.round(used/total*100);
const fmtGB = n => n>=1024 ? `${(n/1024).toFixed(1)} TB` : `${n} GB`;

const StateIcon = ({state}) => {
  const map={poweredOn:["▶","#5cb85c"],poweredOff:["■",C.red],suspended:["⏸",C.yellow]};
  const [ic,col]=map[state]||["?","#888"];
  return <span style={{color:col,fontSize:10,marginRight:5}}>{ic}</span>;
};

const UsageBar = ({pct:p,w=120,warn=70,crit=90}) => {
  const col = p>=crit?C.red:p>=warn?C.yellow:C.green;
  return (
    <div style={{display:"flex",alignItems:"center",gap:6}}>
      <div style={{width:w,height:7,background:C.border,borderRadius:2,overflow:"hidden"}}>
        <div style={{width:`${p}%`,height:"100%",background:col,borderRadius:2,transition:"width 0.6s"}}/>
      </div>
      <span style={{color:C.gray,fontSize:11,fontFamily:mono,minWidth:32}}>{p}%</span>
    </div>
  );
};

const Tag = ({label,col=C.blue}) => (
  <span style={{background:col+"22",color:col,border:`1px solid ${col}44`,borderRadius:3,
    fontSize:10,padding:"1px 6px",fontFamily:mono,fontWeight:700,letterSpacing:0.5}}>{label}</span>
);

const Spark = ({data,color=C.blue}) => (
  <ResponsiveContainer width={80} height={28}>
    <AreaChart data={data} margin={{top:2,right:0,left:0,bottom:0}}>
      <defs>
        <linearGradient id={`g${color.replace("#","")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.4}/>
          <stop offset="100%" stopColor={color} stopOpacity={0}/>
        </linearGradient>
      </defs>
      <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5}
        fill={`url(#g${color.replace("#","")})`} dot={false} isAnimationActive={false}/>
    </AreaChart>
  </ResponsiveContainer>
);

// ── vMotion Wizard ────────────────────────────────────────────────────────
const VMotionWizard = ({vm, onClose, onComplete}) => {
  const [step,setStep]=useState(0);
  const [destHost,setDestHost]=useState("");
  const [priority,setPriority]=useState("high");
  const [progress,setProgress]=useState(0);
  const [migLog,setMigLog]=useState([]);
  const currentHost = findHost(vm.hostId);
  const eligibleHosts = allHosts.filter(h=>h.id!==vm.hostId&&h.status==="connected");

  const startMigration = () => {
    setStep(3);
    const logs=[
      "Validating migration compatibility...",
      "Checking CPU compatibility between hosts...",
      "Verifying shared storage access...",
      "Initiating pre-copy memory phase...",
      "Transmitting memory pages (pass 1/3)...",
      "Transmitting memory pages (pass 2/3)...",
      "Transmitting memory pages (pass 3/3)...",
      "Entering quiesce phase — suspending VM briefly...",
      "Switching execution to destination host...",
      "Updating network routing tables...",
      "Resuming VM on destination host...",
      "Migration complete. Total downtime: 0ms",
    ];
    let i=0; let p=0;
    const iv=setInterval(()=>{
      if(i<logs.length){
        setMigLog(l=>[...l,{msg:logs[i],t:new Date().toTimeString().slice(0,8)}]);
        p=Math.min(100,Math.round((i+1)/logs.length*100));
        setProgress(p); i++;
      } else { clearInterval(iv); setTimeout(()=>{ onComplete(vm.id,destHost); onClose(); },600); }
    },600);
  };

  const steps=["Select Destination","Compatibility Check","Review","Migrating"];
  const dh=findHost(destHost);

  return (
    <div style={{position:"fixed",inset:0,background:"#00000090",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:font}}>
      <div style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:6,width:620,maxHeight:"88vh",overflow:"hidden",display:"flex",flexDirection:"column",boxShadow:"0 20px 60px #00000080"}}>

        {/* Header */}
        <div style={{background:C.header,padding:"14px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",borderBottom:`1px solid ${C.border}`}}>
          <div>
            <div style={{color:C.text,fontWeight:600,fontSize:14}}>Migrate Virtual Machine</div>
            <div style={{color:C.dim,fontSize:12,marginTop:2}}>{vm.name}</div>
          </div>
          <button onClick={onClose} style={{background:"none",border:"none",color:C.dim,fontSize:18,cursor:"pointer"}}>✕</button>
        </div>

        {/* Step indicators */}
        <div style={{display:"flex",background:C.toolbar,borderBottom:`1px solid ${C.border}`}}>
          {steps.map((s,i)=>(
            <div key={s} style={{flex:1,padding:"10px 8px",textAlign:"center",fontSize:11,
              color:step===i?C.blue:step>i?C.green:C.dim,
              borderBottom:step===i?`2px solid ${C.blue}`:step>i?`2px solid ${C.green}`:"2px solid transparent",
              transition:"all 0.2s",display:"flex",alignItems:"center",justifyContent:"center",gap:5}}>
              <span style={{background:step>i?C.green:step===i?C.blue:C.border,color:"#fff",
                borderRadius:"50%",width:18,height:18,display:"inline-flex",alignItems:"center",
                justifyContent:"center",fontSize:10,fontWeight:700,flexShrink:0}}>
                {step>i?"✓":i+1}
              </span>
              {s}
            </div>
          ))}
        </div>

        <div style={{flex:1,overflow:"auto",padding:24}}>

          {/* Step 0 — select destination */}
          {step===0&&(
            <div style={{display:"flex",flexDirection:"column",gap:16}}>
              <InfoRow label="Source Host" value={`${currentHost?.name} (${currentHost?.ip})`}/>
              <InfoRow label="VM" value={vm.name}/>
              <div style={{color:C.text,fontSize:13,fontWeight:600,marginTop:4}}>Select Destination Host</div>
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                {eligibleHosts.map(h=>{
                  const cl=findCluster(h.id);
                  const sel=destHost===h.id;
                  return (
                    <div key={h.id} onClick={()=>setDestHost(h.id)}
                      style={{border:`1px solid ${sel?C.blue:C.border}`,borderRadius:5,padding:"12px 16px",cursor:"pointer",
                        background:sel?C.blue+"11":C.card,transition:"all 0.15s"}}>
                      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                        <div>
                          <div style={{color:sel?C.blue:C.text,fontWeight:600,fontSize:13}}>{h.name}</div>
                          <div style={{color:C.dim,fontSize:11,marginTop:2}}>{h.ip} · {cl?.name} · {h.model}</div>
                        </div>
                        <div style={{display:"flex",gap:16,alignItems:"center"}}>
                          <div style={{textAlign:"right"}}>
                            <div style={{color:C.dim,fontSize:10,marginBottom:3}}>CPU</div>
                            <UsageBar pct={h.cpu.used} w={80}/>
                          </div>
                          <div style={{textAlign:"right"}}>
                            <div style={{color:C.dim,fontSize:10,marginBottom:3}}>Memory</div>
                            <UsageBar pct={pct(h.ram.used,h.ram.total)} w={80}/>
                          </div>
                          {sel&&<span style={{color:C.blue,fontSize:18}}>✓</span>}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div>
                <div style={{color:C.text,fontSize:13,fontWeight:600,marginBottom:10}}>Migration Priority</div>
                {[["high","High (Reserve CPU for migration)"],["low","Low (Use idle CPU cycles only)"]].map(([v,l])=>(
                  <label key={v} style={{display:"flex",alignItems:"center",gap:8,cursor:"pointer",marginBottom:8,color:C.gray,fontSize:13}}>
                    <input type="radio" value={v} checked={priority===v} onChange={()=>setPriority(v)} style={{accentColor:C.blue}}/>
                    {l}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Step 1 — compatibility check */}
          {step===1&&dh&&(
            <div style={{display:"flex",flexDirection:"column",gap:12}}>
              <div style={{color:C.text,fontSize:13,fontWeight:600}}>Compatibility Check</div>
              {[
                [true,  "CPU compatibility","EVC mode: Intel Skylake — compatible"],
                [true,  "Shared storage",   `${DATASTORES[0].name} accessible on both hosts`],
                [true,  "Network",          "VM Network available on destination"],
                [true,  "Memory",           `${dh.ram.total-dh.ram.used} GB free — sufficient for VM (${vm.ram} GB)`],
                [true,  "vMotion network",  "vMotion-Network (VLAN 100) configured on both hosts"],
                [true,  "Snapshot state",   vm.snapshot?"Snapshots present — migration supported":"No snapshots"],
              ].map(([ok,label,detail],i)=>(
                <div key={i} style={{display:"flex",gap:12,alignItems:"flex-start",padding:"10px 14px",
                  background:ok?C.green+"0d":C.red+"0d",border:`1px solid ${ok?C.green+"33":C.red+"33"}`,borderRadius:5}}>
                  <span style={{color:ok?C.green:C.red,fontSize:16,marginTop:1}}>{ok?"✓":"✗"}</span>
                  <div>
                    <div style={{color:C.text,fontSize:13,fontWeight:600}}>{label}</div>
                    <div style={{color:C.dim,fontSize:12,marginTop:2}}>{detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Step 2 — review */}
          {step===2&&dh&&(
            <div style={{display:"flex",flexDirection:"column",gap:12}}>
              <div style={{color:C.text,fontSize:13,fontWeight:600}}>Review Migration Settings</div>
              <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden"}}>
                {[
                  ["Virtual Machine", vm.name],
                  ["Operation",       "vMotion (live migration)"],
                  ["Source Host",     `${currentHost?.name} (${currentHost?.ip})`],
                  ["Destination",     `${dh.name} (${dh.ip})`],
                  ["Priority",        priority==="high"?"High":"Low"],
                  ["Estimated Time",  "~45 seconds"],
                  ["Downtime",        "< 1ms (imperceptible)"],
                ].map(([k,v],i)=>(
                  <div key={k} style={{display:"flex",padding:"10px 16px",borderBottom:`1px solid ${C.border}`,
                    background:i%2===0?C.card:C.panel}}>
                    <div style={{color:C.dim,fontSize:12,width:160,flexShrink:0}}>{k}</div>
                    <div style={{color:C.text,fontSize:12,fontFamily:k==="Estimated Time"||k==="Downtime"?font:mono}}>{v}</div>
                  </div>
                ))}
              </div>
              <div style={{background:C.blue+"11",border:`1px solid ${C.blue}33`,borderRadius:5,padding:"10px 14px",color:C.gray,fontSize:12,lineHeight:1.6}}>
                ℹ️ The virtual machine will remain powered on and fully accessible throughout migration.
                Users will experience no interruption.
              </div>
            </div>
          )}

          {/* Step 3 — migrating */}
          {step===3&&(
            <div style={{display:"flex",flexDirection:"column",gap:16}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <div style={{color:C.text,fontSize:13,fontWeight:600}}>
                  {progress<100?"Migrating...":"Migration Complete"}
                </div>
                <Tag label={`${progress}%`} col={progress<100?C.blue:C.green}/>
              </div>
              <div style={{height:8,background:C.border,borderRadius:4,overflow:"hidden"}}>
                <div style={{width:`${progress}%`,height:"100%",background:progress<100?C.blue:C.green,
                  borderRadius:4,transition:"width 0.5s",boxShadow:progress<100?`0 0 8px ${C.blue}`:undefined}}/>
              </div>
              <div style={{background:C.toolbar,border:`1px solid ${C.border}`,borderRadius:5,
                padding:12,maxHeight:220,overflow:"auto",fontFamily:mono,fontSize:11}}>
                {migLog.map((l,i)=>(
                  <div key={i} style={{display:"flex",gap:10,marginBottom:4}}>
                    <span style={{color:C.dim}}>{l.t}</span>
                    <span style={{color:i===migLog.length-1&&progress===100?C.green:C.gray}}>{l.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {step<3&&(
          <div style={{padding:"14px 24px",borderTop:`1px solid ${C.border}`,display:"flex",gap:8,justifyContent:"flex-end",background:C.toolbar}}>
            <Btn secondary onClick={onClose}>Cancel</Btn>
            {step>0&&<Btn secondary onClick={()=>setStep(s=>s-1)}>← Back</Btn>}
            {step<2&&<Btn disabled={step===0&&!destHost} onClick={()=>setStep(s=>s+1)}>Next →</Btn>}
            {step===2&&<Btn onClick={startMigration} col={C.green}>Start Migration</Btn>}
          </div>
        )}
      </div>
    </div>
  );
};

const InfoRow = ({label,value}) => (
  <div style={{display:"flex",gap:12}}>
    <span style={{color:C.dim,fontSize:12,width:120,flexShrink:0}}>{label}</span>
    <span style={{color:C.text,fontSize:12,fontFamily:mono}}>{value}</span>
  </div>
);

const Btn = ({children,onClick,secondary,col,disabled}) => (
  <button onClick={disabled?undefined:onClick} style={{
    background:disabled?"#1a1e27":secondary?"transparent":col?col+"22":C.blue+"22",
    color:disabled?C.dim:secondary?C.gray:col||C.blue,
    border:`1px solid ${disabled?"#333":secondary?C.border:col?col+"44":C.blue+"55"}`,
    borderRadius:4,padding:"7px 16px",fontSize:12,cursor:disabled?"not-allowed":"pointer",
    fontFamily:font,fontWeight:600,whiteSpace:"nowrap",opacity:disabled?0.5:1,transition:"all 0.15s"
  }}>{children}</button>
);

// ── Create VM Modal ────────────────────────────────────────────────────────
const CreateVMModal = ({onClose,onCreate}) => {
  const [step,setStep]=useState(0);
  const [form,setForm]=useState({name:"",os:"Ubuntu 22.04",cpu:2,ram:4,disk:50,hostId:"h1",netId:"net1",dsId:"ds1"});
  const set=(k,v)=>setForm(f=>({...f,[k]:v}));
  const steps=["Name & OS","Compute","Storage","Network","Ready to Complete"];

  return (
    <div style={{position:"fixed",inset:0,background:"#00000090",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:font}}>
      <div style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:6,width:580,boxShadow:"0 20px 60px #00000080",display:"flex",flexDirection:"column",maxHeight:"88vh",overflow:"hidden"}}>
        <div style={{background:C.header,padding:"14px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",borderBottom:`1px solid ${C.border}`}}>
          <div style={{color:C.text,fontWeight:600,fontSize:14}}>New Virtual Machine</div>
          <button onClick={onClose} style={{background:"none",border:"none",color:C.dim,fontSize:18,cursor:"pointer"}}>✕</button>
        </div>
        <div style={{display:"flex",background:C.toolbar,borderBottom:`1px solid ${C.border}`}}>
          {steps.map((s,i)=>(
            <div key={s} style={{flex:1,padding:"9px 4px",textAlign:"center",fontSize:10,
              color:step===i?C.blue:step>i?C.green:C.dim,
              borderBottom:step===i?`2px solid ${C.blue}`:step>i?`2px solid ${C.green}`:"2px solid transparent"}}>
              {i+1}. {s}
            </div>
          ))}
        </div>
        <div style={{flex:1,overflow:"auto",padding:24}}>
          {step===0&&(
            <div style={{display:"flex",flexDirection:"column",gap:16}}>
              <FInput label="Virtual machine name" value={form.name} onChange={v=>set("name",v)} placeholder="e.g. prod-web-03"/>
              <FSelect label="Guest OS" value={form.os} onChange={v=>set("os",v)} opts={["Ubuntu 22.04","Ubuntu 20.04","Debian 12","Rocky Linux 9","AlmaLinux 9","Windows Server 2022","Windows Server 2019","FreeBSD 14"]}/>
              <FSelect label="Compute Resource" value={form.hostId} onChange={v=>set("hostId",v)}
                opts={allHosts.filter(h=>h.status==="connected").map(h=>({v:h.id,l:`${h.name} — ${findCluster(h.id)?.name}`}))}/>
            </div>
          )}
          {step===1&&(
            <div style={{display:"flex",flexDirection:"column",gap:20}}>
              <div>
                <FRange label={`CPU: ${form.cpu} vCPU`} min={1} max={64} value={form.cpu} onChange={v=>set("cpu",+v)}/>
                <div style={{color:C.blue,fontSize:24,fontWeight:700,textAlign:"center",marginTop:8,fontFamily:mono}}>{form.cpu} vCPU</div>
              </div>
              <div>
                <FRange label={`Memory: ${form.ram} GB`} min={1} max={512} value={form.ram} onChange={v=>set("ram",+v)}/>
                <div style={{color:"#a78bfa",fontSize:24,fontWeight:700,textAlign:"center",marginTop:8,fontFamily:mono}}>{form.ram} GB</div>
              </div>
            </div>
          )}
          {step===2&&(
            <div style={{display:"flex",flexDirection:"column",gap:16}}>
              <FRange label={`Disk: ${form.disk} GB`} min={10} max={50000} step={10} value={form.disk} onChange={v=>set("disk",+v)}/>
              <div style={{color:C.green,fontSize:24,fontWeight:700,textAlign:"center",fontFamily:mono}}>{fmtGB(form.disk)}</div>
              <FSelect label="Datastore" value={form.dsId} onChange={v=>set("dsId",v)}
                opts={DATASTORES.map(d=>({v:d.id,l:`${d.name} — ${fmtGB(d.free)} free (${d.type})`}))}/>
            </div>
          )}
          {step===3&&(
            <FSelect label="Network" value={form.netId} onChange={v=>set("netId",v)}
              opts={NETWORKS.map(n=>({v:n.id,l:`${n.name} (${n.type}${n.vlan?`, VLAN ${n.vlan}`:""})`}))}/>
          )}
          {step===4&&(
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden"}}>
              {[
                ["Name",form.name||"(unnamed)"],
                ["Guest OS",form.os],
                ["Host",allHosts.find(h=>h.id===form.hostId)?.name||"—"],
                ["CPU",`${form.cpu} vCPU`],
                ["Memory",`${form.ram} GB`],
                ["Disk",fmtGB(form.disk)],
                ["Datastore",DATASTORES.find(d=>d.id===form.dsId)?.name||"—"],
                ["Network",NETWORKS.find(n=>n.id===form.netId)?.name||"—"],
              ].map(([k,v],i)=>(
                <div key={k} style={{display:"flex",padding:"9px 16px",background:i%2===0?C.card:C.panel,borderBottom:`1px solid ${C.border}`}}>
                  <div style={{color:C.dim,fontSize:12,width:120,flexShrink:0}}>{k}</div>
                  <div style={{color:C.text,fontSize:12,fontFamily:mono}}>{v}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{padding:"12px 24px",borderTop:`1px solid ${C.border}`,display:"flex",gap:8,justifyContent:"flex-end",background:C.toolbar}}>
          <Btn secondary onClick={onClose}>Cancel</Btn>
          {step>0&&<Btn secondary onClick={()=>setStep(s=>s-1)}>← Back</Btn>}
          {step<4&&<Btn onClick={()=>setStep(s=>s+1)}>Next →</Btn>}
          {step===4&&<Btn col={C.green} onClick={()=>{onCreate(form);onClose();}}>Finish</Btn>}
        </div>
      </div>
    </div>
  );
};

const FInput=({label,value,onChange,placeholder})=>(
  <label style={{color:C.gray,fontSize:12}}>{label}
    <input value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder}
      style={{display:"block",marginTop:5,width:"100%",boxSizing:"border-box",background:C.card,border:`1px solid ${C.border}`,
        borderRadius:4,color:C.text,padding:"8px 10px",fontSize:12,fontFamily:mono,outline:"none"}}/>
  </label>
);
const FSelect=({label,value,onChange,opts})=>(
  <label style={{color:C.gray,fontSize:12}}>{label}
    <select value={value} onChange={e=>onChange(e.target.value)}
      style={{display:"block",marginTop:5,width:"100%",background:C.card,border:`1px solid ${C.border}`,
        borderRadius:4,color:C.text,padding:"8px 10px",fontSize:12,fontFamily:mono,outline:"none"}}>
      {opts.map(o=>typeof o==="string"?<option key={o}>{o}</option>:<option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  </label>
);
const FRange=({label,min,max,step=1,value,onChange})=>(
  <label style={{color:C.gray,fontSize:12}}>{label}
    <input type="range" min={min} max={max} step={step} value={value} onChange={e=>onChange(e.target.value)}
      style={{display:"block",marginTop:6,width:"100%",accentColor:C.blue}}/>
  </label>
);

// ── Context Menu ──────────────────────────────────────────────────────────
const CtxMenu = ({x,y,vm,onAction,onClose}) => {
  useEffect(()=>{
    const h=()=>onClose(); window.addEventListener("click",h);
    return ()=>window.removeEventListener("click",h);
  },[onClose]);

  const items=[
    vm.state==="poweredOff"?{l:"Power On",a:"start",col:C.green}:null,
    vm.state==="poweredOn"?{l:"Power Off",a:"stop",col:C.red}:null,
    vm.state==="poweredOn"?{l:"Suspend",a:"suspend",col:C.yellow}:null,
    vm.state!=="poweredOff"?{l:"Guest Reboot",a:"reboot",col:C.gray}:null,
    "sep",
    {l:"Migrate (vMotion)...",a:"vmotion",col:C.blue,disabled:vm.state==="poweredOff"},
    {l:"Clone...",a:"clone",col:C.gray},
    {l:"Create Snapshot...",a:"snapshot",col:C.gray},
    "sep",
    {l:"Edit Settings...",a:"edit",col:C.gray},
    {l:"Open Console",a:"console",col:C.gray},
    "sep",
    {l:"Remove from Inventory",a:"remove",col:C.red},
  ].filter(Boolean);

  return (
    <div onClick={e=>e.stopPropagation()} style={{position:"fixed",left:x,top:y,zIndex:300,
      background:C.card,border:`1px solid ${C.border}`,borderRadius:5,minWidth:200,
      boxShadow:"0 8px 32px #000000aa",fontFamily:font,fontSize:12,overflow:"hidden"}}>
      {items.map((item,i)=>item==="sep"?(
        <div key={i} style={{height:1,background:C.border,margin:"2px 0"}}/>
      ):(
        <div key={item.a} onClick={()=>{if(!item.disabled){onAction(item.a);onClose();}}}
          style={{padding:"7px 14px",color:item.disabled?C.dim:item.col,cursor:item.disabled?"default":"pointer",
            transition:"background 0.1s"}}
          onMouseEnter={e=>{if(!item.disabled)e.target.style.background=C.border}}
          onMouseLeave={e=>e.target.style.background="transparent"}>
          {item.l}
        </div>
      ))}
    </div>
  );
};

// ── Main App ──────────────────────────────────────────────────────────────
export default function VSphere() {
  const [vms,setVMs]=useState(initVMs);
  const [tasks,setTasks]=useState(TASKS);
  const [nav,setNav]=useState("vms");
  const [tab,setTab]=useState("summary");
  const [selectedVM,setSelectedVM]=useState(null);
  const [selectedHost,setSelectedHost]=useState(null);
  const [vmotion,setVMotion]=useState(null);
  const [createVM,setCreateVM]=useState(false);
  const [ctx,setCtx]=useState(null);
  const [treeExp,setTreeExp]=useState({dc:true,cl1:true,cl2:false});
  const [tick,setTick]=useState(0);
  const [search,setSearch]=useState("");

  useEffect(()=>{
    const id=setInterval(()=>{
      setTick(t=>t+1);
      setVMs(prev=>prev.map(vm=>vm.state!=="poweredOn"?vm:({
        ...vm,
        cpuSpark:[...vm.cpuSpark.slice(1),{t:vm.cpuSpark[vm.cpuSpark.length-1].t+1,v:Math.max(1,Math.min(99,vm.cpuSpark[vm.cpuSpark.length-1].v+(Math.random()-.5)*10))}],
        ramSpark:[...vm.ramSpark.slice(1),{t:vm.ramSpark[vm.ramSpark.length-1].t+1,v:Math.max(1,Math.min(99,vm.ramSpark[vm.ramSpark.length-1].v+(Math.random()-.5)*4))}],
      })));
    },2500);
    return ()=>clearInterval(id);
  },[]);

  const vmAction=(id,action)=>{
    setVMs(prev=>prev.map(vm=>vm.id!==id?vm:{
      ...vm,
      state:action==="start"?"poweredOn":action==="stop"?"poweredOff":action==="suspend"?"suspended":vm.state,
      ip:action==="stop"?"—":vm.ip!=="—"?vm.ip:`10.0.2.${Math.floor(Math.random()*200+10)}`,
    }));
    const vm=vms.find(v=>v.id===id);
    const label={start:"Power On virtual machine",stop:"Power Off virtual machine",suspend:"Suspend virtual machine",reboot:"Restart guest OS"}[action]||action;
    setTasks(t=>[{id:Date.now(),task:label,target:vm?.name,status:"Completed",time:new Date().toTimeString().slice(0,8),pct:100},...t.slice(0,9)]);
  };

  const handleVMotionComplete=(vmId,destHostId)=>{
    setVMs(prev=>prev.map(vm=>vm.id!==vmId?vm:{...vm,hostId:destHostId}));
    const vm=vms.find(v=>v.id===vmId);
    const dh=findHost(destHostId);
    setTasks(t=>[{id:Date.now(),task:"Migrate virtual machine",target:`${vm?.name} → ${dh?.name}`,status:"Completed",time:new Date().toTimeString().slice(0,8),pct:100},...t.slice(0,9)]);
  };

  const handleVMAction=(action,vm)=>{
    if(action==="vmotion"&&vm.state!=="poweredOff") setVMotion(vm);
    else if(["start","stop","suspend","reboot"].includes(action)) vmAction(vm.id,action);
  };

  const filteredVMs=vms.filter(vm=>!search||vm.name.toLowerCase().includes(search.toLowerCase())||vm.os.toLowerCase().includes(search.toLowerCase()));
  const selVM=selectedVM?vms.find(v=>v.id===selectedVM):null;
  const selHost=selectedHost?allHosts.find(h=>h.id===selectedHost):null;

  const navItems=[
    {id:"vms",label:"Virtual Machines",icon:"🖥"},
    {id:"hosts",label:"Hosts",icon:"🖧"},
    {id:"clusters",label:"Clusters",icon:"⛃"},
    {id:"datastores",label:"Datastores",icon:"🗄"},
    {id:"networks",label:"Networking",icon:"🔗"},
  ];

  const totalVMs=vms.length;
  const runningVMs=vms.filter(v=>v.state==="poweredOn").length;
  const totalHosts=allHosts.length;
  const connHosts=allHosts.filter(h=>h.status==="connected").length;

  return (
    <div style={{display:"flex",flexDirection:"column",height:"100vh",fontFamily:font,background:C.bg,color:C.text,fontSize:13,userSelect:"none"}}>

      {/* ── Top bar ── */}
      <div style={{height:46,background:C.header,borderBottom:`1px solid ${C.border}`,display:"flex",alignItems:"center",padding:"0 16px",gap:16,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          <div style={{width:28,height:28,background:`linear-gradient(135deg,${C.blue},#2563eb)`,borderRadius:6,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14}}>⚡</div>
          <div>
            <div style={{color:C.text,fontWeight:700,fontSize:13,letterSpacing:0.5}}>NexusHV</div>
            <div style={{color:C.dim,fontSize:10}}>Hypervisor Manager</div>
          </div>
        </div>
        <div style={{width:1,height:28,background:C.border,margin:"0 4px"}}/>
        <div style={{color:C.dim,fontSize:12}}>{DATACENTER.name}</div>
        <div style={{flex:1}}/>
        <div style={{display:"flex",alignItems:"center",gap:8,background:C.card,border:`1px solid ${C.border}`,borderRadius:4,padding:"5px 10px"}}>
          <span style={{color:C.dim,fontSize:12}}>🔍</span>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search..."
            style={{background:"none",border:"none",color:C.text,fontSize:12,outline:"none",width:140,fontFamily:font}}/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <Tag label={`${runningVMs}/${totalVMs} VMs`} col={C.green}/>
          <Tag label={`${connHosts}/${totalHosts} Hosts`} col={C.blue}/>
        </div>
        <button onClick={()=>setCreateVM(true)}
          style={{background:`${C.blue}22`,color:C.blue,border:`1px solid ${C.blue}44`,borderRadius:4,
            padding:"6px 14px",fontSize:12,cursor:"pointer",fontWeight:600,display:"flex",alignItems:"center",gap:6}}>
          ＋ New VM
        </button>
        <div style={{color:C.dim,fontSize:12}}>admin@vsphere.local</div>
      </div>

      <div style={{display:"flex",flex:1,overflow:"hidden"}}>

        {/* ── Left nav ── */}
        <div style={{width:200,background:C.sidebar,borderRight:`1px solid ${C.border}`,display:"flex",flexDirection:"column",flexShrink:0}}>
          <div style={{padding:"12px 12px 6px",color:C.dim,fontSize:10,letterSpacing:1,fontWeight:700}}>NAVIGATOR</div>
          {navItems.map(item=>(
            <div key={item.id} onClick={()=>{setNav(item.id);setSelectedVM(null);setSelectedHost(null);setTab("summary");}}
              style={{display:"flex",alignItems:"center",gap:8,padding:"8px 12px",cursor:"pointer",fontSize:12,
                background:nav===item.id?C.blue+"15":"transparent",
                color:nav===item.id?C.blue:C.gray,
                borderLeft:nav===item.id?`2px solid ${C.blue}`:"2px solid transparent",
                transition:"all 0.15s"}}>
              <span>{item.icon}</span>{item.label}
            </div>
          ))}

          <div style={{padding:"16px 12px 6px",color:C.dim,fontSize:10,letterSpacing:1,fontWeight:700}}>INVENTORY</div>

          {/* DC Tree */}
          <TreeNode label={DATACENTER.name} icon="🏢" depth={0} expanded={treeExp.dc}
            onToggle={()=>setTreeExp(e=>({...e,dc:!e.dc}))} active={false}/>
          {treeExp.dc&&DATACENTER.clusters.map(cl=>(
            <div key={cl.id}>
              <TreeNode label={cl.name} icon="⛃" depth={1} expanded={treeExp[cl.id]}
                onToggle={()=>setTreeExp(e=>({...e,[cl.id]:!e[cl.id]}))} active={false}/>
              {treeExp[cl.id]&&cl.hosts.map(h=>(
                <TreeNode key={h.id} label={h.name.split(".")[0]} icon={h.status==="connected"?"🟢":"🟡"} depth={2}
                  active={selectedHost===h.id}
                  onClick={()=>{setSelectedHost(h.id);setSelectedVM(null);setNav("hosts");setTab("summary");}}/>
              ))}
            </div>
          ))}
        </div>

        {/* ── Main content ── */}
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>

          {/* Toolbar */}
          <div style={{height:38,background:C.toolbar,borderBottom:`1px solid ${C.border}`,display:"flex",alignItems:"center",padding:"0 12px",gap:6,flexShrink:0}}>
            {nav==="vms"&&selVM&&(<>
              <ToolBtn disabled={selVM.state==="poweredOn"} onClick={()=>vmAction(selVM.id,"start")} label="▶ Power On"/>
              <ToolBtn disabled={selVM.state!=="poweredOn"} onClick={()=>vmAction(selVM.id,"stop")} label="■ Power Off" danger/>
              <ToolBtn disabled={selVM.state!=="poweredOn"} onClick={()=>vmAction(selVM.id,"suspend")} label="⏸ Suspend"/>
              <ToolBtn disabled={selVM.state!=="poweredOn"} onClick={()=>vmAction(selVM.id,"reboot")} label="↺ Reboot"/>
              <div style={{width:1,height:20,background:C.border,margin:"0 4px"}}/>
              <ToolBtn disabled={selVM.state==="poweredOff"} onClick={()=>setVMotion(selVM)} label="↗ Migrate..." accent/>
              <ToolBtn label="📷 Snapshot"/>
              <ToolBtn label="⎘ Clone"/>
            </>)}
            {(!selVM)&&<span style={{color:C.dim,fontSize:12}}>Select a virtual machine to see actions</span>}
          </div>

          {/* Content area */}
          <div style={{flex:1,overflow:"auto",display:"flex",flexDirection:"column"}}>

            {/* VM list */}
            {nav==="vms"&&(
              <div style={{display:"flex",flex:1,overflow:"hidden"}}>
                <div style={{flex:1,overflow:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead>
                      <tr style={{background:C.toolbar,borderBottom:`1px solid ${C.border}`}}>
                        {["","Name","State","Host","OS","CPU","Memory","CPU Usage","Mem Usage","IP Address"].map(h=>(
                          <th key={h} style={{padding:"8px 10px",textAlign:"left",color:C.dim,fontWeight:600,fontSize:11,letterSpacing:0.3,whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filteredVMs.map(vm=>{
                        const cpuVal=vm.cpuSpark[vm.cpuSpark.length-1]?.v||0;
                        const ramVal=vm.ramSpark[vm.ramSpark.length-1]?.v||0;
                        const host=findHost(vm.hostId);
                        const sel=selectedVM===vm.id;
                        return (
                          <tr key={vm.id}
                            onClick={()=>{setSelectedVM(vm.id);setTab("summary");}}
                            onContextMenu={e=>{e.preventDefault();setCtx({x:e.clientX,y:e.clientY,vm});}}
                            style={{background:sel?C.blue+"15":undefined,cursor:"pointer",borderBottom:`1px solid ${C.border}`,transition:"background 0.1s"}}
                            onMouseEnter={e=>{if(!sel)e.currentTarget.style.background=C.card}}
                            onMouseLeave={e=>{if(!sel)e.currentTarget.style.background="transparent"}}>
                            <td style={{padding:"6px 10px"}}><StateIcon state={vm.state}/></td>
                            <td style={{padding:"6px 10px",color:sel?C.blue:C.text,fontWeight:sel?600:400}}>{vm.name}</td>
                            <td style={{padding:"6px 10px"}}>
                              <Tag label={vm.state==="poweredOn"?"On":vm.state==="poweredOff"?"Off":"Suspended"}
                                col={vm.state==="poweredOn"?C.green:vm.state==="poweredOff"?C.red:C.yellow}/>
                            </td>
                            <td style={{padding:"6px 10px",color:C.dim,fontFamily:mono,fontSize:11}}>{host?.name.split(".")[0]}</td>
                            <td style={{padding:"6px 10px",color:C.gray}}>{vm.os}</td>
                            <td style={{padding:"6px 10px",color:C.dim,fontFamily:mono}}>{vm.cpu}vCPU</td>
                            <td style={{padding:"6px 10px",color:C.dim,fontFamily:mono}}>{vm.ram}GB</td>
                            <td style={{padding:"6px 4px"}}>
                              {vm.state==="poweredOn"?<div style={{display:"flex",alignItems:"center",gap:4}}><Spark data={vm.cpuSpark} color={C.blue}/><span style={{fontSize:10,color:C.dim,fontFamily:mono}}>{Math.round(cpuVal)}%</span></div>:<span style={{color:C.dim,fontSize:11}}>—</span>}
                            </td>
                            <td style={{padding:"6px 4px"}}>
                              {vm.state==="poweredOn"?<div style={{display:"flex",alignItems:"center",gap:4}}><Spark data={vm.ramSpark} color="#a78bfa"/><span style={{fontSize:10,color:C.dim,fontFamily:mono}}>{Math.round(ramVal)}%</span></div>:<span style={{color:C.dim,fontSize:11}}>—</span>}
                            </td>
                            <td style={{padding:"6px 10px",color:C.dim,fontFamily:mono,fontSize:11}}>{vm.ip}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Detail panel */}
                {selVM&&(
                  <div style={{width:340,borderLeft:`1px solid ${C.border}`,flexShrink:0,overflow:"auto",background:C.panel}}>
                    <div style={{display:"flex",borderBottom:`1px solid ${C.border}`}}>
                      {["summary","monitor","configure"].map(t=>(
                        <div key={t} onClick={()=>setTab(t)}
                          style={{flex:1,padding:"10px 0",textAlign:"center",fontSize:11,cursor:"pointer",
                            color:tab===t?C.blue:C.dim,textTransform:"capitalize",
                            borderBottom:tab===t?`2px solid ${C.blue}`:"2px solid transparent",background:tab===t?C.tabAct:C.tab}}>
                          {t}
                        </div>
                      ))}
                    </div>
                    {tab==="summary"&&<VMSummary vm={selVM}/>}
                    {tab==="monitor"&&<VMMonitor vm={selVM}/>}
                    {tab==="configure"&&<VMConfigure vm={selVM}/>}
                  </div>
                )}
              </div>
            )}

            {/* Hosts view */}
            {nav==="hosts"&&(
              <div style={{padding:20,display:"flex",flexDirection:"column",gap:16}}>
                {allHosts.map(h=>(
                  <div key={h.id} onClick={()=>setSelectedHost(h.id)}
                    style={{background:selectedHost===h.id?C.blue+"10":C.card,border:`1px solid ${selectedHost===h.id?C.blue+"44":C.border}`,borderRadius:6,padding:16,cursor:"pointer"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
                      <div>
                        <div style={{color:C.text,fontWeight:600}}>{h.name}</div>
                        <div style={{color:C.dim,fontSize:11,marginTop:2}}>{h.ip} · {h.model}</div>
                      </div>
                      <div style={{display:"flex",gap:8,alignItems:"center"}}>
                        <Tag label={findCluster(h.id)?.name||""} col={C.blue}/>
                        <Tag label={h.status==="connected"?"Connected":"Maintenance"} col={h.status==="connected"?C.green:C.yellow}/>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:24}}>
                      <StatBlock label="CPU" value={`${h.cpu.sockets}S/${h.cpu.cores}C @ ${h.cpu.ghz}GHz`}
                        sub={<UsageBar pct={h.cpu.used}/>}/>
                      <StatBlock label="Memory" value={`${h.ram.total} GB`} sub={<UsageBar pct={pct(h.ram.used,h.ram.total)}/>}/>
                      <StatBlock label="VMs" value={h.vms.length} sub={<span style={{color:C.dim,fontSize:11}}>running</span>}/>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Clusters */}
            {nav==="clusters"&&(
              <div style={{padding:20,display:"flex",flexDirection:"column",gap:16}}>
                {DATACENTER.clusters.map(cl=>(
                  <div key={cl.id} style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:6,padding:16}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
                      <div style={{color:C.text,fontWeight:600,fontSize:14}}>{cl.name}</div>
                      <div style={{display:"flex",gap:6}}>
                        {cl.ha&&<Tag label="HA" col={C.green}/>}
                        {cl.drs&&<Tag label="DRS" col={C.blue}/>}
                      </div>
                    </div>
                    <div style={{display:"flex",gap:24}}>
                      <StatBlock label="Hosts" value={cl.hosts.length}/>
                      <StatBlock label="VMs" value={cl.hosts.reduce((a,h)=>a+h.vms.length,0)}/>
                      <StatBlock label="Total CPU" value={`${cl.hosts.reduce((a,h)=>a+h.cpu.cores*2,0)} Cores`}/>
                      <StatBlock label="Total RAM" value={fmtGB(cl.hosts.reduce((a,h)=>a+h.ram.total,0))}/>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Datastores */}
            {nav==="datastores"&&(
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                <thead>
                  <tr style={{background:C.toolbar,borderBottom:`1px solid ${C.border}`}}>
                    {["Name","Type","Capacity","Free","Used","Hosts","Status"].map(h=>(
                      <th key={h} style={{padding:"9px 12px",textAlign:"left",color:C.dim,fontWeight:600,fontSize:11}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {DATASTORES.map((ds,i)=>(
                    <tr key={ds.id} style={{borderBottom:`1px solid ${C.border}`,background:i%2===0?C.bg:C.panel}}>
                      <td style={{padding:"9px 12px",color:C.text,fontWeight:500}}>{ds.name}</td>
                      <td style={{padding:"9px 12px",color:C.gray}}>{ds.type}</td>
                      <td style={{padding:"9px 12px",color:C.dim,fontFamily:mono}}>{fmtGB(ds.capacity)}</td>
                      <td style={{padding:"9px 12px",color:C.green,fontFamily:mono}}>{fmtGB(ds.free)}</td>
                      <td style={{padding:"9px 12px",minWidth:160}}>
                        <UsageBar pct={pct(ds.capacity-ds.free,ds.capacity)} w={100}/>
                      </td>
                      <td style={{padding:"9px 12px",color:C.dim}}>{ds.hosts.length} hosts</td>
                      <td style={{padding:"9px 12px"}}><Tag label="Normal" col={C.green}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* Networking */}
            {nav==="networks"&&(
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                <thead>
                  <tr style={{background:C.toolbar,borderBottom:`1px solid ${C.border}`}}>
                    {["Name","Type","VLAN","Uplink","Connected VMs","Status"].map(h=>(
                      <th key={h} style={{padding:"9px 12px",textAlign:"left",color:C.dim,fontWeight:600,fontSize:11}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {NETWORKS.map((n,i)=>(
                    <tr key={n.id} style={{borderBottom:`1px solid ${C.border}`,background:i%2===0?C.bg:C.panel}}>
                      <td style={{padding:"9px 12px",color:C.text,fontWeight:500}}>{n.name}</td>
                      <td style={{padding:"9px 12px",color:C.gray}}>{n.type}</td>
                      <td style={{padding:"9px 12px",color:C.dim,fontFamily:mono}}>{n.vlan||"—"}</td>
                      <td style={{padding:"9px 12px",color:C.dim,fontFamily:mono}}>{n.uplink}</td>
                      <td style={{padding:"9px 12px",color:C.dim}}>{n.ports}</td>
                      <td style={{padding:"9px 12px"}}><Tag label="Active" col={C.green}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* ── Recent tasks bar ── */}
          <div style={{height:130,background:C.toolbar,borderTop:`1px solid ${C.border}`,flexShrink:0,overflow:"hidden"}}>
            <div style={{padding:"6px 12px",display:"flex",alignItems:"center",gap:8,borderBottom:`1px solid ${C.border}`}}>
              <span style={{color:C.dim,fontSize:11,fontWeight:600,letterSpacing:0.5}}>RECENT TASKS</span>
            </div>
            <div style={{overflow:"auto",maxHeight:100}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                <thead>
                  <tr>
                    {["Task","Target","Status","Time"].map(h=>(
                      <th key={h} style={{padding:"4px 12px",textAlign:"left",color:C.dim,fontWeight:500,fontSize:10}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tasks.map(t=>(
                    <tr key={t.id} style={{borderBottom:`1px solid ${C.border}22`}}>
                      <td style={{padding:"4px 12px",color:C.gray}}>{t.task}</td>
                      <td style={{padding:"4px 12px",color:C.dim,fontFamily:mono}}>{t.target}</td>
                      <td style={{padding:"4px 12px"}}><Tag label={t.status} col={C.green}/></td>
                      <td style={{padding:"4px 12px",color:C.dim,fontFamily:mono}}>{t.time}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* Modals */}
      {vmotion&&<VMotionWizard vm={vmotion} onClose={()=>setVMotion(null)} onComplete={handleVMotionComplete}/>}
      {createVM&&<CreateVMModal onClose={()=>setCreateVM(false)} onCreate={()=>{}}/>}
      {ctx&&<CtxMenu x={ctx.x} y={ctx.y} vm={ctx.vm} onAction={a=>handleVMAction(a,ctx.vm)} onClose={()=>setCtx(null)}/>}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────
const TreeNode=({label,icon,depth,expanded,onToggle,onClick,active})=>(
  <div onClick={onClick||onToggle}
    style={{display:"flex",alignItems:"center",gap:5,padding:`5px ${12+depth*14}px`,cursor:"pointer",fontSize:11,
      color:active?"#4a9fd4":"#8a8fa8",background:active?"#4a9fd415":"transparent",
      borderLeft:active?"2px solid #4a9fd4":"2px solid transparent"}}>
    {onToggle&&!onClick&&<span style={{fontSize:9,color:"#5a607a"}}>{expanded?"▼":"▶"}</span>}
    <span>{icon}</span>
    <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{label}</span>
  </div>
);

const ToolBtn=({label,onClick,disabled,danger,accent})=>(
  <button onClick={disabled?undefined:onClick} style={{
    background:"transparent",color:disabled?C.dim:danger?C.red:accent?C.blue:C.gray,
    border:`1px solid ${disabled?"transparent":danger?C.red+"33":accent?C.blue+"44":"transparent"}`,
    borderRadius:3,padding:"3px 10px",fontSize:11,cursor:disabled?"default":"pointer",fontFamily:font,
    opacity:disabled?0.4:1,transition:"all 0.15s",whiteSpace:"nowrap"
  }}>{label}</button>
);

const StatBlock=({label,value,sub})=>(
  <div>
    <div style={{color:C.dim,fontSize:10,marginBottom:3,letterSpacing:0.5}}>{label}</div>
    <div style={{color:C.text,fontWeight:600,fontSize:13,fontFamily:mono,marginBottom:4}}>{value}</div>
    {sub&&<div>{sub}</div>}
  </div>
);

const VMSummary=({vm})=>{
  const host=findHost(vm.hostId);
  const cluster=findCluster(vm.hostId);
  return (
    <div style={{padding:16,display:"flex",flexDirection:"column",gap:12}}>
      <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:4}}>
        <div style={{width:36,height:36,background:C.card,border:`1px solid ${C.border}`,borderRadius:6,display:"flex",alignItems:"center",justifyContent:"center",fontSize:18}}>
          {vm.os.includes("Windows")?"🪟":vm.os.includes("Rocky")?"⛰️":"🐧"}
        </div>
        <div>
          <div style={{color:C.text,fontWeight:600}}>{vm.name}</div>
          <div style={{color:C.dim,fontSize:11}}>{vm.os}</div>
        </div>
      </div>
      {[
        ["State",<Tag label={vm.state==="poweredOn"?"Powered On":vm.state==="poweredOff"?"Powered Off":"Suspended"} col={vm.state==="poweredOn"?C.green:vm.state==="poweredOff"?C.red:C.yellow}/>],
        ["IP Address",vm.ip],
        ["Host",host?.name.split(".")[0]||"—"],
        ["Cluster",cluster?.name||"—"],
        ["CPU",`${vm.cpu} vCPU`],
        ["Memory",`${vm.ram} GB`],
        ["Disk",fmtGB(vm.disk)],
        ["Snapshots",vm.snapshot?"Yes":"No"],
      ].map(([k,v])=>(
        <div key={k} style={{display:"flex",justifyContent:"space-between",borderBottom:`1px solid ${C.border}`,paddingBottom:8}}>
          <span style={{color:C.dim,fontSize:11}}>{k}</span>
          <span style={{color:C.text,fontSize:11,fontFamily:typeof v==="string"?mono:font}}>{v}</span>
        </div>
      ))}
    </div>
  );
};

const VMMonitor=({vm})=>(
  <div style={{padding:16,display:"flex",flexDirection:"column",gap:16}}>
    <div style={{color:C.text,fontWeight:600,fontSize:13}}>Performance</div>
    {vm.state==="poweredOn"?(
      <>
        <div>
          <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
            <span style={{color:C.dim,fontSize:11}}>CPU Usage</span>
            <span style={{color:C.blue,fontSize:11,fontFamily:mono}}>{Math.round(vm.cpuSpark[vm.cpuSpark.length-1]?.v||0)}%</span>
          </div>
          <ResponsiveContainer width="100%" height={60}>
            <AreaChart data={vm.cpuSpark}>
              <defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={C.blue} stopOpacity={0.3}/><stop offset="100%" stopColor={C.blue} stopOpacity={0}/>
              </linearGradient></defs>
              <Area type="monotone" dataKey="v" stroke={C.blue} strokeWidth={1.5} fill="url(#cg)" dot={false} isAnimationActive={false}/>
              <Tooltip contentStyle={{background:C.card,border:`1px solid ${C.border}`,fontSize:11}} formatter={v=>[`${Math.round(v)}%`,"CPU"]}/>
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
            <span style={{color:C.dim,fontSize:11}}>Memory Usage</span>
            <span style={{color:"#a78bfa",fontSize:11,fontFamily:mono}}>{Math.round(vm.ramSpark[vm.ramSpark.length-1]?.v||0)}%</span>
          </div>
          <ResponsiveContainer width="100%" height={60}>
            <AreaChart data={vm.ramSpark}>
              <defs><linearGradient id="mg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.3}/><stop offset="100%" stopColor="#a78bfa" stopOpacity={0}/>
              </linearGradient></defs>
              <Area type="monotone" dataKey="v" stroke="#a78bfa" strokeWidth={1.5} fill="url(#mg)" dot={false} isAnimationActive={false}/>
              <Tooltip contentStyle={{background:C.card,border:`1px solid ${C.border}`,fontSize:11}} formatter={v=>[`${Math.round(v)}%`,"RAM"]}/>
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </>
    ):<div style={{color:C.dim,fontSize:12,textAlign:"center",padding:20}}>VM is not running</div>}
  </div>
);

const VMConfigure=({vm})=>(
  <div style={{padding:16,display:"flex",flexDirection:"column",gap:8}}>
    <div style={{color:C.text,fontWeight:600,fontSize:13,marginBottom:4}}>Hardware</div>
    {[["CPU",`${vm.cpu} vCPU`],["Memory",`${vm.ram} GB`],["Hard Disk 1",fmtGB(vm.disk)],["Network Adapter","VM Network (E1000e)"],["CD/DVD Drive","Client Device"],["SCSI Controller","VMware Paravirtual"],["Video Card","Auto-detect"]].map(([k,v])=>(
      <div key={k} style={{display:"flex",justifyContent:"space-between",padding:"7px 0",borderBottom:`1px solid ${C.border}`}}>
        <span style={{color:C.dim,fontSize:11}}>{k}</span>
        <span style={{color:C.text,fontSize:11,fontFamily:mono}}>{v}</span>
      </div>
    ))}
  </div>
);
