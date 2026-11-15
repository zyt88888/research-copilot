"use client";
import { apiBase } from "../../lib/apiBase";
import { useState } from "react";
import { ErrorState, SkeletonCard } from "../../components/State";

type ChartData = {
  chart: any;
  type: string;
  caption: string;
  table: { columns: string[]; rows: string[][] };
  summary: string;
  filename: string;
};

// 轻量 SVG 图表渲染（零第三方依赖，支持 bar / line / radar）
function MiniChart({ data }: { data: ChartData }) {
  const W = 520, H = 300, pad = { l: 50, r: 20, t: 20, b: 50 };
  const opt = data.chart || {};

  if (data.type === "radar") {
    const indicators = opt.radar?.indicator || [];
    const series = opt.series?.[0]?.data || [];
    if (!indicators.length) return <div>无雷达图数据</div>;
    const cx = W / 2, cy = H / 2, R = 100;
    const N = indicators.length;
    const angle = (i: number) => (Math.PI * 2 * i) / N - Math.PI / 2;
    const maxVal = Math.max(...indicators.map((i: any) => i.max || 1), 1);
    const point = (i: number, v: number) => {
      const r = (Math.min(v, maxVal) / maxVal) * R;
      return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
    };
    return (
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxHeight: 320 }}>
        {[0.25, 0.5, 0.75, 1].map((sc, k) => (
          <polygon key={k}
            points={indicators.map((_, i) => point(i, maxVal * sc).join(",")).join(" ")}
            fill="none" stroke="#e2e8f0" strokeWidth={1} />
        ))}
        {indicators.map((ind: any, i: number) => {
          const [x, y] = point(i, maxVal * 1.15);
          return <line key={i} x1={cx} y1={cy} x2={point(i, maxVal)[0]} y2={point(i, maxVal)[1]} stroke="#e2e8f0" />;
        })}
        {series.map((s: any, k: number) => (
          <polygon key={k}
            points={(s.value || []).map((v: number, i: number) => point(i, v).join(",")).join(" ")}
            fill={(opt.color?.[k] || "#6366f1") + "33"}
            stroke={opt.color?.[k] || "#6366f1"} strokeWidth={2} />
        ))}
        {indicators.map((ind: any, i: number) => {
          const [x, y] = point(i, maxVal * 1.25);
          return <text key={i} x={x} y={y} fontSize={10} fill="#64748b" textAnchor="middle"
            dominantBaseline="middle">{ind.name}</text>;
        })}
      </svg>
    );
  }

  // bar / line
  const labels = opt.xAxis?.data || [];
  const series = opt.series || [];
  const allVals = series.flatMap((s: any) => s.data || []);
  const maxV = Math.max(...allVals, 1);
  const minV = Math.min(...allVals, 0);
  const range = maxV - minV || 1;
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const bw = labels.length ? iw / labels.length : 0;
  const y = (v: number) => pad.t + ih - ((v - minV) / range) * ih;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxHeight: 320 }}>
      {[0, 0.25, 0.5, 0.75, 1].map((f, k) => {
        const yy = pad.t + ih - f * ih;
        const val = minV + f * range;
        return (
          <g key={k}>
            <line x1={pad.l} y1={yy} x2={W - pad.r} y2={yy} stroke="#e2e8f0" strokeWidth={1} />
            <text x={pad.l - 6} y={yy + 4} fontSize={9} fill="#94a3b8" textAnchor="end">
              {val.toFixed(1)}
            </text>
          </g>
        );
      })}
      {labels.map((lb: string, i: number) => (
        <text key={i} x={pad.l + i * bw + bw / 2} y={H - pad.b + 16} fontSize={9}
          fill="#94a3b8" textAnchor="middle" transform={`rotate(25 ${pad.l + i * bw + bw / 2} ${H - pad.b + 16})`}>
          {lb.length > 8 ? lb.slice(0, 7) + "…" : lb}
        </text>
      ))}
      {series.map((s: any, k: number) => {
        const color = opt.color?.[k] || "#6366f1";
        const sw = Math.min(bw * 0.7, 24);
        if (s.type === "line") {
          const pts = (s.data || []).map((v: number, i: number) =>
            `${pad.l + i * bw + bw / 2},${y(v)}`).join(" ");
          return (
            <g key={k}>
              <polyline points={pts} fill="none" stroke={color} strokeWidth={2} />
              {(s.data || []).map((v: number, i: number) => (
                <circle key={i} cx={pad.l + i * bw + bw / 2} cy={y(v)} r={3} fill={color} />
              ))}
            </g>
          );
        }
        return (s.data || []).map((v: number, i: number) => {
          const x = pad.l + i * bw + (bw - sw) / 2;
          const yh = y(v);
          return <rect key={`${k}-${i}`} x={x} y={yh} width={sw} height={pad.t + ih - yh}
            fill={color} rx={3} />;
        });
      })}
    </svg>
  );
}


export default function CopilotPage() {
  const [idea, setIdea] = useState("基于稀疏检索与稠密重排的学术问答 RAG 系统");
  const [outline, setOutline] = useState<any>(null);
  const [diagram, setDiagram] = useState<any>(null);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [chart, setChart] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run(endpoint: string, body: any) {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`${apiBase()}/api/copilot/${endpoint}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(`HTTP ${r.status}${detail?.detail ? ": " + detail.detail : ""}`);
      }
      return await r.json();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function runCsv() {
    if (!csvFile) { setErr("请先选择一个 CSV 文件"); return; }
    setLoading(true); setErr(null);
    try {
      const text = await csvFile.text();
      const result = await run("csv", { content: text, filename: csvFile.name });
      if (result) setChart(result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // 提供一个内置示例 CSV，一键生成
  const sampleCsv = "model,accuracy,latency_ms,params_B\nA,0.88,12,1.5\nB,0.92,18,3.0\nC,0.95,30,7.0\nD,0.90,15,2.0\nE,0.93,22,4.0";

  return (
    <div className="grid">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>✨ 写作 Copilot</h2>
        <label>研究 Idea / 方向</label>
        <input type="text" value={idea} onChange={(e) => setIdea(e.target.value)} style={{ marginTop: 6 }} />
        <div className="row" style={{ marginTop: 12, flexWrap: "wrap", gap: 8 }}>
          <button className="btn" disabled={loading}
            onClick={async () => setOutline(await run("outline", { idea }))}>📄 生成论文框架</button>
          <button className="btn ghost" disabled={loading}
            onClick={async () => setDiagram(await run("diagram", { idea, kind: "mermaid" }))}>🖼 Mermaid 架构图</button>
          <button className="btn ghost" disabled={loading}
            onClick={async () => setDiagram(await run("diagram", { idea, kind: "tikz" }))}>📐 TikZ 架构图</button>
        </div>
      </section>

      {err && (
        <div className="card">
          <ErrorState title="生成失败" message={err} />
        </div>
      )}

      {loading && <SkeletonCard rows={5} label="正在调用模型生成…" />}

      {outline && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>论文框架（{outline.mode === "llm" ? "在线生成" : "离线模板"}）</h3>
          <pre style={{ background: "#f8fafc", padding: 14, borderRadius: 10,
            overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6 }}>
{outline.markdown}
          </pre>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 8 }}>
            可用参考文献 {outline.references?.length || 0} 条
            {outline.dropped_references ? `，剔除虚构引用 ${outline.dropped_references} 条` : ""}
            {outline.notice ? ` · 注意：${outline.notice.slice(0, 80)}` : ""}
          </div>
        </section>
      )}

      {diagram && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>架构图（{diagram.mode === "llm" ? "在线生成" : "离线模板"}）</h3>
          <pre style={{ background: "#0f172a", color: "#e2e8f0", padding: 14, borderRadius: 10,
            overflow: "auto", maxHeight: 400, fontSize: 12 }}>
{diagram.code}
          </pre>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-3)" }}>
            可复制到 <a href="https://mermaid.live" target="_blank" rel="noreferrer">Mermaid Live</a> 或 LaTeX 编辑器预览。
          </div>
        </section>
      )}

      <section className="card">
        <h3 style={{ marginTop: 0 }}>📊 CSV → 图表 + Figure Caption</h3>
        <div className="row grow" style={{ alignItems: "center", gap: 8 }}>
          <input type="file" accept=".csv,text/csv"
            onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
            style={{ flex: 1 }} />
          <button className="btn ghost" disabled={loading || !csvFile} onClick={runCsv}>生成图表</button>
          <button className="btn ghost" disabled={loading}
            onClick={() => {
              const blob = new Blob([sampleCsv], { type: "text/csv" });
              const f = new File([blob], "sample_models.csv", { type: "text/csv" });
              setCsvFile(f);
            }}>
            载入示例数据
          </button>
        </div>
        {csvFile && (
          <div style={{ marginTop: 6, fontSize: 12, color: "var(--ink-3)" }}>
            已选择：{csvFile.name}（{(csvFile.size / 1024).toFixed(1)} KB）
          </div>
        )}

        {chart && (
          <div style={{ marginTop: 16, display: "grid", gap: 12 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <span className="chip">图表类型：{chart.type} · 数据 {chart.table.rows.length} 行 × {chart.table.columns.length} 列</span>
              <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{chart.summary}</span>
            </div>
            <div style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
              <MiniChart data={chart} />
            </div>
            <div style={{ background: "#f8fafc", padding: 10, borderRadius: 8, fontSize: 13, fontStyle: "italic" }}>
              📝 {chart.caption}
            </div>
            <details>
              <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--ink-3)" }}>查看原始数据</summary>
              <pre style={{ fontSize: 11, maxHeight: 200, overflow: "auto" }}>
{JSON.stringify(chart.table, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
