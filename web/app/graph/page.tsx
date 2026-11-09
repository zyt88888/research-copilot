"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorState, Skeleton } from "../../components/State";

type Node = {
  id: string; title?: string; label?: string; year?: number | null;
  authors?: string[]; pagerank: number; in_degree: number; out_degree?: number;
  role: string; size: number; num_pages?: number; arxiv_id?: string;
};
type Link = { source: string; target: string; title?: string; kind?: string };
type FutureItem = {
  paper_id: string; paper_title: string; year?: number | null; page: number;
  text: string; status: string; resolved_by?: { id: string; title: string } | null;
};

const W = 1080;
const H = 620;

export default function GraphPage() {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [future, setFuture] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [hoverLink, setHoverLink] = useState<Link | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  async function load() {
    setLoading(true); setErr(null);
    try {
      const g = await fetch("/api/backend/api/graph").then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      setNodes(g.nodes || []); setLinks(g.links || []); setStats(g.stats);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // 前沿 Idea 计算较慢，独立加载，不阻塞图谱主体
    fetch("/api/backend/api/future")
      .then(r => r.json()).then(setFuture).catch(() => setFuture(null));
  }

  useEffect(() => { load(); }, []);

  const positions = useMemo(() => layoutForce(nodes, links), [nodes, links]);
  const byId = useMemo(() => Object.fromEntries(nodes.map(n => [n.id, n])), [nodes]);

  const visible = (n: Node) => !hidden[n.role];
  const visNodes = nodes.filter(visible);
  const visLinks = links.filter(l => {
    const a = byId[l.source]; const b = byId[l.target];
    return a && b && visible(a) && visible(b);
  });

  const neighbors = useMemo(() => {
    const map: Record<string, Set<string>> = {};
    for (const l of links) {
      (map[l.source] ||= new Set()).add(l.target);
      (map[l.target] ||= new Set()).add(l.source);
    }
    return map;
  }, [links]);

  const active = hover || selected;
  const isDim = (id: string) =>
    !!active && id !== active && !(neighbors[active]?.has(id));
  const labelVisible = (n: Node) =>
    !!active
      ? (n.id === active || neighbors[active]?.has(n.id) || queryMatch(n))
      : n.pagerank >= prCut(nodes) || queryMatch(n);
  function queryMatch(n: Node) {
    const q = query.trim().toLowerCase();
    return !!q && ((n.title || n.label || "").toLowerCase().includes(q));
  }

  const zoom = (factor: number, cx = W / 2, cy = H / 2) => {
    setView(v => {
      const k = Math.max(0.5, Math.min(3, v.k * factor));
      const ratio = k / v.k;
      return { k, x: cx - (cx - v.x) * ratio, y: cy - (cy - v.y) * ratio };
    });
  };

  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = ((e.clientX - rect.left) / rect.width) * W;
    const cy = ((e.clientY - rect.top) / rect.height) * H;
    zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, cx, cy);
  };

  // 用 Pointer Events，鼠标 / 触屏 / 触控笔统一走同一套拖拽逻辑
  const startDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  };
  const onDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const scale = W / e.currentTarget.getBoundingClientRect().width;
    setView(v => ({ ...v,
      x: drag.current!.vx + (e.clientX - drag.current!.x) * scale,
      y: drag.current!.vy + (e.clientY - drag.current!.y) * scale }));
  };
  const endDrag = () => { drag.current = null; };

  const sel = selected ? byId[selected] : null;

  return (
    <div className="grid">
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>🕸 局部引用图谱</h2>
          <div className="row" style={{ gap: 6 }}>
            <input type="text" placeholder="搜索论文标题…" value={query}
              onChange={e => setQuery(e.target.value)} style={{ width: 190 }} />
            <button className="btn ghost" onClick={() => zoom(1.2)}>＋</button>
            <button className="btn ghost" onClick={() => zoom(1 / 1.2)}>－</button>
            <button className="btn ghost" onClick={() => setView({ k: 1, x: 0, y: 0 })}>重置</button>
          </div>
        </div>

        {stats && (
          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
            <span className="chip">节点 {stats.papers}</span>
            <span className="chip">边 {stats.edges}</span>
            <span className="chip ok" style={{ cursor: "pointer", opacity: hidden.bedrock ? 0.4 : 1 }}
              onClick={() => setHidden(h => ({ ...h, bedrock: !h.bedrock }))}>
              基石论文 {stats.bedrock}
            </span>
            <span className="chip warn" style={{ cursor: "pointer", opacity: hidden.frontier ? 0.4 : 1 }}
              onClick={() => setHidden(h => ({ ...h, frontier: !h.frontier }))}>
              前沿论文 {stats.frontier}
            </span>
            <span className="chip" style={{ cursor: "pointer", opacity: hidden.normal ? 0.4 : 1 }}
              onClick={() => setHidden(h => ({ ...h, normal: !h.normal }))}>
              普通 {stats.papers - stats.bedrock - stats.frontier}
            </span>
            <span className="chip">匹配率 {stats.refs_total ? ((stats.matched / stats.refs_total) * 100).toFixed(0) + "%" : "-"}</span>
          </div>
        )}

        {loading ? (
          <div style={{ marginTop: 16 }}>
            <div className="sk-label">正在构建引用图谱…</div>
            <Skeleton rows={7} />
          </div>
        ) : err ? (
          <div style={{ marginTop: 12 }}>
            <ErrorState title="图谱载入失败" message={err} onRetry={load} />
          </div>
        ) : nodes.length === 0 ? (
          <Empty icon="🕸" title="图谱还是空的"
            desc="上传论文后，系统会自动从参考文献构建引用关系、计算 PageRank 并识别前沿 Idea。"
            action={<a className="btn ghost" href="/">前往文库</a>} />
        ) : (
        <>
        <div className="graph-scroll">
        <svg width="100%" viewBox={`0 0 ${W} ${H}`}
          style={{ marginTop: 12, border: "1px solid var(--line)", borderRadius: 10,
            background: "#fcfcfd", cursor: drag.current ? "grabbing" : "grab", touchAction: "none" }}
          onWheel={onWheel} onPointerDown={startDrag} onPointerMove={onDrag}
          onPointerUp={endDrag} onPointerLeave={endDrag} onPointerCancel={endDrag}
          onClick={() => setSelected(null)}>
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8" fill="#cbd5e1" />
            </marker>
            <marker id="arrow-hi" markerWidth="9" markerHeight="9" refX="9" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9" fill="#f59e0b" />
            </marker>
          </defs>

          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {visLinks.map((l, i) => {
              const a = positions[l.source]; const b = positions[l.target];
              if (!a || !b) return null;
              const hi = !!active && (l.source === active || l.target === active);
              const isVersion = l.kind === "version";
              return (
                <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={hi ? "#f59e0b" : isVersion ? "#a78bfa" : "#cbd5e1"}
                  strokeWidth={hi ? 2 : isVersion ? 1.8 : 1}
                  strokeDasharray={isVersion ? "6 4" : undefined}
                  opacity={active && !hi ? 0.25 : 1}
                  markerEnd={hi ? "url(#arrow-hi)" : "url(#arrow)"}
                  onMouseEnter={() => setHoverLink(l)} onMouseLeave={() => setHoverLink(null)}
                  style={{ cursor: "pointer" }} />
              );
            })}

            {visNodes.map(n => {
              const p = positions[n.id];
              if (!p) return null;
              const color = n.role === "bedrock" ? "#f59e0b"
                : n.role === "frontier" ? "#10b981" : "#6366f1";
              const r = Math.max(5, Math.min(24, n.size * 0.42));
              const dim = isDim(n.id);
              return (
                <g key={n.id} opacity={dim ? 0.25 : 1}
                  style={{ cursor: "pointer" }}
                  onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                  onClick={(e) => { e.stopPropagation(); setSelected(n.id); }}>
                  <circle cx={p.x} cy={p.y} r={r + 9} fill="transparent" />
                  {selected === n.id && (
                    <circle cx={p.x} cy={p.y} r={r + 5} fill="none" stroke={color} strokeWidth={2} />
                  )}
                  <circle cx={p.x} cy={p.y} r={r} fill={color} fillOpacity={0.35}
                    stroke={color} strokeWidth={1.5} />
                  {(hover === n.id || selected === n.id || queryMatch(n)) && (
                    <circle cx={p.x} cy={p.y} r={r} fill={color} fillOpacity={0.85} />
                  )}
                  {labelVisible(n) && (
                    <text x={p.x + r + 4} y={p.y + 4} fontSize={11}
                      fill={queryMatch(n) ? "#b45309" : "#334155"}
                      fontWeight={n.role === "bedrock" ? 600 : 400}
                      style={{ pointerEvents: "none" }}>
                      {(n.label || n.title || "").slice(0, 18)}{n.year ? ` · ${n.year}` : ""}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
        </div>

        <div className="row" style={{ justifyContent: "space-between", marginTop: 6, flexWrap: "wrap" }}>
          <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
            ● 金=基石（PageRank 高/被引多）　● 绿=前沿（年份新/入度低）　● 蓝=普通
            　·虚线紫=版本关联　·滚轮缩放 / 拖动平移
          </div>
          {hoverLink && (
            <div className="chip" style={{ fontSize: 12 }}>
              {hoverLink.kind === "version" ? "版本关联" : "引用"}：
              {(byId[hoverLink.source]?.label || "").slice(0, 18)} → {(byId[hoverLink.target]?.label || "").slice(0, 18)}
              {hoverLink.title ? `（${hoverLink.title.slice(0, 26)}）` : ""}
            </div>
          )}
        </div>

        {sel && (
          <div className="card" style={{ marginTop: 12, background: "#f8fafc" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0, fontSize: 15 }}>{sel.title}</h3>
              <button className="btn ghost" onClick={() => setSelected(null)}>关闭</button>
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6 }}>
              {(sel.authors || []).slice(0, 6).join(", ")} · {sel.year || "n.d."} · {sel.arxiv_id || ""} · {sel.num_pages || "?"} 页
            <br />
              角色 <strong>{sel.role}</strong>　PageRank <strong>{sel.pagerank}</strong>
              　被引 <strong>{sel.in_degree}</strong>　引用 <strong>{sel.out_degree ?? 0}</strong>
            </div>
            <div className="row" style={{ marginTop: 10, gap: 8 }}>
              <a className="btn ghost" href={`/reader?id=${sel.id}`}>📖 打开阅读器</a>
              <a className="btn ghost" href={`/reader?id=${sel.id}&page=1`}>📄 第 1 页</a>
            </div>
          </div>
        )}

        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, color: "var(--ink-3)", marginBottom: 6 }}>
            论文列表（按 PageRank 排序，点击即选中节点）
          </div>
          <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
            {nodes.filter(visible).map((n, idx) => (
              <div key={n.id}
                onClick={() => { setSelected(n.id); setHover(n.id); }}
                style={{ padding: "7px 10px", fontSize: 13, cursor: "pointer",
                  borderBottom: "1px solid var(--line)",
                  background: selected === n.id ? "#fef3c7" : "transparent",
                  display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ color: "var(--ink-3)", width: 22, flex: "0 0 auto" }}>{idx + 1}</span>
                <span style={{ width: 8, height: 8, borderRadius: "50%", flex: "0 0 auto",
                  background: n.role === "bedrock" ? "#f59e0b"
                    : n.role === "frontier" ? "#10b981" : "#6366f1" }} />
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {n.label || n.title}
                </span>
                <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
                  {n.year || ""} · PR {n.pagerank}
                </span>
              </div>
            ))}
          </div>
        </div>
        </>
        )}
      </section>

      {future && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>🔮 前沿 Idea（{future.open_count}/{future.total} 未被解决）</h3>
          <div style={{ maxHeight: 340, overflowY: "auto", marginTop: 8 }}>
            {future.items.map((it: FutureItem, i: number) => (
              <div key={i} style={{
                borderLeft: `3px solid ${it.status === "open" ? "#10b981" : "#cbd5e1"}`,
                padding: "6px 10px", margin: "6px 0", background: "#f8fafc", fontSize: 13 }}>
                <strong>[{it.status === "open" ? "OPEN" : "SOLVED"}]</strong> {it.text}
                <div style={{ color: "var(--ink-3)", fontSize: 12 }}>
                  — <a href={`/reader?id=${it.paper_id}&page=${it.page}`} style={{ color: "inherit" }}>
                    {it.paper_title}（第 {it.page} 页）
                  </a>
                  {it.status !== "open" && it.resolved_by
                    && `，已由 ${it.resolved_by.title.slice(0, 36)} 解决`}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ---- 小型力导向布局（不引入 d3） ----
function seeded(i: number) {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

function prCut(nodes: Node[]) {
  if (!nodes.length) return 0;
  const arr = nodes.map(n => n.pagerank).sort((a, b) => b - a);
  return arr[Math.min(arr.length - 1, Math.floor(arr.length * 0.45))];
}

function layoutForce(nodes: Node[], links: Link[]) {
  const n = Math.max(nodes.length, 1);
  const k = Math.sqrt((W * H) / n) * 1.15;
  const pos: Record<string, { x: number; y: number }> = {};
  nodes.forEach((nd, i) => {
    const ang = 2 * Math.PI * seeded(i);
    const rad = 0.28 * Math.min(W, H) * (0.55 + 0.45 * seeded(i + 100));
    pos[nd.id] = { x: W / 2 + Math.cos(ang) * rad * 1.5, y: H / 2 + Math.sin(ang) * rad };
  });

  // Fruchterman-Reingold：每轮重置位移 + 温度退火，避免速度累积把节点弹到边界
  let temp = W / 8;
  for (let iter = 0; iter < 240; iter++) {
    const disp: Record<string, { x: number; y: number }> = {};
    for (const nd of nodes) disp[nd.id] = { x: 0, y: 0 };

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos[nodes[i].id], b = pos[nodes[j].id];
        let dx = a.x - b.x, dy = a.y - b.y;
        let dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 0.01) { dx = seeded(i + j) - 0.5; dy = seeded(i * 7 + j) - 0.5; dist = 0.01; }
        // 大节点（基石论文）互相排斥更强，减少堆叠
        const w = 0.8 + Math.min(0.8, (nodes[i].pagerank + nodes[j].pagerank) * 3);
        const rep = ((k * k) / dist) * w;
        const ux = dx / dist, uy = dy / dist;
        disp[nodes[i].id].x += ux * rep; disp[nodes[i].id].y += uy * rep;
        disp[nodes[j].id].x -= ux * rep; disp[nodes[j].id].y -= uy * rep;
      }
    }

    for (const l of links) {
      const a = pos[l.source], b = pos[l.target];
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(0.01, Math.sqrt(dx * dx + dy * dy));
      const att = (dist * dist) / k;
      const ux = dx / dist, uy = dy / dist;
      disp[l.source].x += ux * att; disp[l.source].y += uy * att;
      disp[l.target].x -= ux * att; disp[l.target].y -= uy * att;
    }

    for (const nd of nodes) {
      const p = pos[nd.id], d = disp[nd.id];
      p.x += (W / 2 - p.x) * 0.006;
      p.y += (H / 2 - p.y) * 0.006;
      const len = Math.max(0.01, Math.sqrt(d.x * d.x + d.y * d.y));
      const step = Math.min(len, temp);
      p.x += (d.x / len) * step;
      p.y += (d.y / len) * step;
      p.x = Math.max(50, Math.min(W - 50, p.x));
      p.y = Math.max(40, Math.min(H - 40, p.y));
    }
    temp = Math.max(0.6, temp * 0.965);
  }

  // 自适应铺满画布：按包围盒等比缩放，避免节点全挤在中心
  const vals = Object.values(pos);
  const x0 = Math.min(...vals.map(p => p.x)), x1 = Math.max(...vals.map(p => p.x));
  const y0 = Math.min(...vals.map(p => p.y)), y1 = Math.max(...vals.map(p => p.y));
  const spanX = Math.max(1, x1 - x0), spanY = Math.max(1, y1 - y0);
  const scale = Math.min(6, Math.min((W - 180) / spanX, (H - 140) / spanY));
  for (const p of vals) {
    p.x = W / 2 + (p.x - (x0 + x1) / 2) * scale;
    p.y = H / 2 + (p.y - (y0 + y1) / 2) * scale;
  }

  // 重叠消解：距离过近的节点对按半径推开
  const minGap = Math.min(120, Math.max(26, (W * H) / (n * 340)));
  for (let pass = 0; pass < 40; pass++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos[nodes[i].id], b = pos[nodes[j].id];
        let dx = b.x - a.x, dy = b.y - a.y;
        let dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 0.01) { dx = seeded(i + j * 3) - 0.5; dy = seeded(i * 5 + j) - 0.5; dist = 0.01; }
        if (dist < minGap) {
          const push = (minGap - dist) / 2;
          const ux = dx / dist, uy = dy / dist;
          a.x = Math.max(50, Math.min(W - 50, a.x - ux * push));
          a.y = Math.max(40, Math.min(H - 40, a.y - uy * push));
          b.x = Math.max(50, Math.min(W - 50, b.x + ux * push));
          b.y = Math.max(40, Math.min(H - 40, b.y + uy * push));
          moved = true;
        }
      }
    }
    if (!moved) break;
  }

  return pos;
}
