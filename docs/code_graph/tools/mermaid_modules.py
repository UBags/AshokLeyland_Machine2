"""Mermaid flowchart of the live module import graph, clustered by package.
Edges into ubiquitous hub modules are dropped (listed separately) so the graph stays readable."""
import json, os, re
g = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph.json") + ""))
HUBS = {"Configuration", "BaseUtils", "utils", "utils.CosThetaPrintUtils", "utils.SuppressedExceptions",
        "utils.CosThetaColors", "utils.Constants", "utils.CosThetaFileUtils", "logutils", "logutils.Logger",
        "logutils.SlaveLoggers", "logutils.AbstractSlaveLogger", "logutils.LogSettings", "utils.RedisUtils",
        "utils.QRCodeHelper", "camera.VisionConfig", "statemachine.StateMachine", "processors.GenericQueueProcessor",
        "utils.WorkerHealth", "utils.WindowsConsole", "camera", "frontend", "endpoints", "persistence"}
nid = lambda m: re.sub(r"\W", "_", m)
groups = {}
for m in g["reachable"]:
    if m in HUBS: continue
    pkg = m.rsplit(".", 1)[0] if "." in m else "(root)"
    groups.setdefault(pkg, []).append(m)
out = ["flowchart LR"]
for pkg, ms in sorted(groups.items()):
    out.append(f'  subgraph {nid("pkg_"+pkg)}["{pkg}"]')
    for m in sorted(ms):
        out.append(f'    {nid(m)}["{m.rsplit(".",1)[-1]}<br/><small>{g["loc"][m]} lines</small>"]')
    out.append("  end")
for m in g["reachable"]:
    if m in HUBS: continue
    for d in g["edges"].get(m, []):
        if d not in HUBS: out.append(f"  {nid(m)} --> {nid(d)}")
    for d in g["lazy"].get(m, []):
        if d not in HUBS and d not in g["edges"].get(m, []): out.append(f"  {nid(m)} -.-> {nid(d)}")
print("\n".join(out))
