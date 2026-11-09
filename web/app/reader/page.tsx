"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Empty, ErrorState, Skeleton, SkeletonCard } from "../../components/State";

export const dynamic = "force-dynamic";

type Chunk = { id: string; page: number; bbox: number[]; section: string; kind: string; text: string; seq: number; level?: number };
type Meta = { id: string; title: string; authors: string[]; year: number | null; num_pages: number; abstract?: string;
  venue?: string; page_flags?: Record<string, string>; toc?: { title: string; level: number; page: number }[] };

export default function ReaderPage() {
  return (
    <Suspense fallback={<SkeletonCard rows={6} label="加载中…" />}>
      <Inner />
    </Suspense>
  );
}

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pid = params.get("id");
  const wantPage = Number(params.get("page") || "0");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [page, setPage] = useState(0);
  const [lang, setLang] = useState<"zh" | "en">("zh");
  const [trans, setTrans] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState<string | null>(null);
  const [pick, setPick] = useState<{ text: string; x: number; y: number; w: number } | null>(null);
  const [pickResult, setPickResult] = useState<{ text: string; mode: string } | null>(null);
  const [pickLoading, setPickLoading] = useState(false);
  const [evidence, setEvidence] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  function closePick() {
    setPick(null); setPickResult(null); setPickLoading(false);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closePick(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 划词：鼠标抬起时取选中文本，在光标附近弹出翻译浮层
  function handleSelect(e: React.MouseEvent) {
    const raw = (window.getSelection()?.toString() || "").replace(/\s+/g, " ").trim();
    if (raw.length < 8 || raw.length > 1500) return;
    openAt(raw, e.clientX, e.clientY);
  }

  function openAt(text: string, clientX: number, clientY: number) {
    // 浮层宽度跟随视口（移动端 ≤ 屏宽 - 24）
    const w = Math.min(400, window.innerWidth - 24);
    const x = Math.max(12, Math.min(window.innerWidth - w - 12, clientX - 30));
    const y = Math.max(12, Math.min(window.innerHeight - 260, clientY + 14));
    setPick({ text, x, y, w });
    setPickResult(null);
    setPickLoading(false);
  }

  async function translatePick() {
    if (!pick) return;
    setPickLoading(true);
    try {
      const r = await fetch(`/api/backend/api/translate`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "text", text: pick.text, lang: "zh" }),
      }).then(res => res.json());
      setPickResult({ text: r.text || r.translation || "（无返回）", mode: r.mode || "?" });
    } catch (err) {
      setPickResult({ text: "翻译失败：后端不可用", mode: "error" });
    } finally { setPickLoading(false); }
  }

  useEffect(() => {
    if (!pid) return;
    setErr(null);
    // 支持 /reader?id=&page=#bbox=x0,y0,x1,y1 的引用深链
    const raw = params.get("bbox")
      || (window.location.hash.startsWith("#bbox=") ? window.location.hash.slice(6) : "");
    const nums = raw ? raw.split(",").map(Number) : [];
    Promise.all([
      fetch(`/api/backend/api/papers/${pid}`).then(r => {
        if (!r.ok) throw new Error(`论文信息载入失败（HTTP ${r.status}）`);
        return r.json();
      }),
      fetch(`/api/backend/api/papers/${pid}/chunks`).then(r => {
        if (!r.ok) throw new Error(`论文内容载入失败（HTTP ${r.status}）`);
        return r.json();
      }),
    ]).then(([m, c]) => {
      setMeta(m); setChunks(c.chunks || []);
      const p = Math.min(Math.max(wantPage || 1, 1), m.num_pages || 1);
      setPage(p - 1);
      if (nums.length === 4 && nums.every(n => Number.isFinite(n))) {
        const target = (c.chunks as Chunk[]).find(ch =>
          ch.page === p && ch.bbox?.length === 4 &&
          Math.abs(ch.bbox[0] - nums[0]) < 3 && Math.abs(ch.bbox[1] - nums[1]) < 3 &&
          Math.abs(ch.bbox[2] - nums[2]) < 4 && Math.abs(ch.bbox[3] - nums[3]) < 4);
        if (target) { setHit(target.id); setEvidence(true); }
      }
    }).catch(e => setErr(e instanceof Error ? e.message : String(e)));
  }, [pid, reloadKey]);

  // 命中证据块后滚动到可视区域居中
  useEffect(() => {
    if (!evidence || !hit) return;
    const t = setTimeout(() => {
      document.getElementById(`c-${hit}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 180);
    return () => clearTimeout(t);
  }, [evidence, hit, page]);

  useEffect(() => {
    if (!pid || !meta) return;
    if (lang === "zh") {
      fetch(`/api/backend/api/translate`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "paper", paper_id: pid, lang: "zh", limit: 80 }),
      }).then(r => r.json()).then(j => {
        const map: Record<string, string> = {};
        for (const item of j.items || []) map[item.chunk_id] = item.translation;
        setTrans(map);
      }).catch(() => setTrans({}));
    } else { setTrans({}); }
  }, [lang, pid, meta]);

  if (!pid) return (
    <div className="card">
      <Empty icon="📖" title="缺少论文 id 参数"
        desc="请从文库或问答的证据链接进入阅读器。"
        action={<a className="btn ghost" href="/">前往文库</a>} />
    </div>
  );
  if (err) return (
    <div className="card">
      <ErrorState title="阅读器载入失败" message={err}
        onRetry={() => setReloadKey(k => k + 1)} />
    </div>
  );
  if (!meta) return <SkeletonCard rows={7} label="正在载入论文…" />;

  const pageChunks = chunks.filter(c => c.page === page + 1 &&
    ["paragraph", "table", "ocr", "caption", "formula", "heading"].includes(c.kind));

  return (
    <div className="grid">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>{meta.title}</h2>
        <div style={{ color: "var(--ink-3)", fontSize: 13 }}>
          {(meta.authors || []).join(", ")} · {meta.year || "n.d."} · {meta.venue || ""} · {meta.num_pages} 页
        </div>
        {meta.abstract && <p style={{ marginTop: 10, lineHeight: 1.7 }}>{meta.abstract}</p>}
        <div className="row grow" style={{ marginTop: 12 }}>
          <button className={`btn ghost ${lang === "zh" ? "chip" : ""}`}
            onClick={() => setLang(lang === "zh" ? "en" : "zh")}>
            {lang === "zh" ? "🔄 切换：显示中文译文" : "🔄 切换：隐藏译文（原文）"}
          </button>
          <input type="text" placeholder="页内搜索…"
            value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn ghost" onClick={() => {
            const h = hit ? pageChunks.find(c => c.text.toLowerCase().includes(query.toLowerCase())
              && c.id > hit) : pageChunks.find(c => c.text.toLowerCase().includes(query.toLowerCase()));
            setHit(h?.id || null);
          }}>查找</button>
        </div>
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="row grow">
            <button className="btn ghost" disabled={page <= 0} onClick={() => setPage(page - 1)}>← 上一页</button>
            <div style={{ textAlign: "center", width: 120 }}>
              第 <strong>{page + 1}</strong> / {meta.num_pages} 页
            </div>
            <button className="btn ghost" disabled={page >= meta.num_pages - 1} onClick={() => setPage(page + 1)}>下一页 →</button>
            {meta.toc?.length > 0 && (
              <select onChange={(e) => {
                const t = meta.toc![Number(e.target.value)]; setPage((t?.page || 1) - 1);
              }} style={{ padding: 6, borderRadius: 6, border: "1px solid var(--line)", marginLeft: 10 }}>
                <option value="">📑 跳转到章节…</option>
                {meta.toc.map((t, i) => (
                  <option key={i} value={i}>{t.level}. {t.title}（p{t.page}）</option>
                ))}
              </select>
            )}
          </div>
        </div>

        {evidence && hit && (
          <div className="row" style={{ marginTop: 12, alignItems: "center" }}>
            <span className="chip" style={{ background: "#fef3c7", borderColor: "#f59e0b" }}>
              🎯 已定位到引用证据 · 第 {page + 1} 页
            </span>
            <button className="btn ghost" style={{ marginLeft: 10 }}
              onClick={() => { setEvidence(false); setHit(null); }}>清除高亮</button>
          </div>
        )}

        <div className="grid reader-grid" style={{ gap: 16, marginTop: 16 }}>
          <div onMouseUp={handleSelect}
            style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 10, padding: 16,
            minHeight: 400, lineHeight: 1.7 }}>
            {pageChunks.map(c => (
              <div key={c.id} id={`c-${c.id}`}
                style={{ position: "relative",
                  marginBottom: 10, borderBottom: "1px dashed var(--line)",
                  paddingBottom: 10,
                  background: hit === c.id ? "#fde68a" : undefined,
                  outline: evidence && hit === c.id ? "2px solid #f59e0b" : undefined,
                  outlineOffset: evidence && hit === c.id ? 2 : undefined,
                  borderRadius: 6 }}>
                {(c.kind === "paragraph" || c.kind === "ocr") && (
                  <button title="翻译本段" onClick={(ev) => {
                    ev.stopPropagation();
                    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
                    openAt(c.text, r.left + 4, r.bottom);
                  }}
                    style={{ position: "absolute", right: 0, top: 0, fontSize: 10,
                      padding: "1px 5px", border: "1px solid var(--line)", borderRadius: 4,
                      background: "#fff", color: "var(--ink-3)", cursor: "pointer", opacity: 0.6 }}>
                    译
                  </button>
                )}
                {c.kind === "heading" && <h3 style={{ fontSize: 16, margin: "8px 0 4px" }}>
                  {c.level >= 1 ? "▶ " : ""}{c.text}</h3>}
                {c.kind === "table" && <div style={{ background: "#f8fafc", padding: 8, borderRadius: 6,
                  fontFamily: "ui-monospace, monospace", fontSize: 12,
                  whiteSpace: "pre-wrap" }}>{c.text}</div>}
                {c.kind === "formula" && <div style={{ fontFamily: "ui-monospace, monospace",
                  fontSize: 13, background: "#f8fafc", padding: 6, borderRadius: 6 }}>{c.text}</div>}
                {c.kind === "caption" && <div style={{ fontStyle: "italic", fontSize: 13, color: "var(--ink-3)" }}>
                  {c.text}</div>}
                {c.kind === "paragraph" || c.kind === "ocr" ? <p style={{ margin: 0 }}>{c.text}</p> : null}
                {lang === "zh" && trans[c.id] && c.kind !== "heading" && c.kind !== "caption" && (
                  <div style={{ marginTop: 4, color: "var(--ink-3)", fontSize: 13 }}>
                    🇨🇳 {trans[c.id]}
                  </div>
                )}
              </div>
            ))}
            {pageChunks.length === 0 && (
              <Empty compact icon="🔍" title="本页没有可解析文本"
                desc={meta.page_flags?.[page + 1] === "needs_ocr"
                  ? "该页为扫描件，OCR 未产出可用文本，已标记为待识别。"
                  : "可能是扫描页、空白页或纯图片页。"} />
            )}
          </div>
        </div>
      </section>

      {pick && (
        <div style={{ position: "fixed", left: pick.x, top: pick.y, zIndex: 60, width: pick.w,
          maxWidth: "calc(100vw - 24px)",
          background: "#fff", border: "1px solid var(--line)", borderRadius: 10,
          boxShadow: "0 12px 32px rgba(15,23,42,.18)", padding: 12 }}
          onMouseUp={(e) => e.stopPropagation()}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <strong style={{ fontSize: 13 }}>划词翻译</strong>
            <span style={{ cursor: "pointer", color: "var(--ink-3)", fontSize: 16, lineHeight: 1 }}
              onClick={closePick}>×</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6, maxHeight: 54, overflowY: "auto" }}>
            {pick.text.slice(0, 180)}{pick.text.length > 180 ? "…" : ""}
          </div>
          {!pickResult && (
            <button className="btn" style={{ marginTop: 10, width: "100%" }}
              onClick={translatePick} disabled={pickLoading}>
              {pickLoading ? "翻译中…" : "🇨🇳 翻译成中文"}
            </button>
          )}
          {pickResult && (
            <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.7 }}>
              {pickResult.text}
              <div style={{ marginTop: 6, fontSize: 11, color: "var(--ink-3)" }}>
                mode={pickResult.mode}
                {pickResult.mode === "offline" && "（离线模式：未配置模型，不伪造译文）"}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
