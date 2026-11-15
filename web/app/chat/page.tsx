"use client";
import { apiBase } from "../../lib/apiBase";
import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorState, SkeletonCard } from "../../components/State";

type Paper = { id: string; title: string; year: number | null };
type Chunk = { id: string; page: number; kind: string; section: string; text: string; bbox?: number[] };

// 单篇论文的原文对照面板（可独立收起、独立翻页、独立双语）
type FocusReq = { paperId: string; bbox: number[] | null; page: number; nonce: number } | null;

function PaperPanel({ paper, focus }: {
  paper: Paper; focus: FocusReq;
}) {
  const [open, setOpen] = useState(true);
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [chunksErr, setChunksErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [bbox, setBbox] = useState<number[] | null>(null);
  const [hit, setHit] = useState<string | null>(null);
  const [lang, setLang] = useState<"zh" | "en">("zh");
  const [trans, setTrans] = useState<Record<string, string>>({});
  const bodyRef = useRef<HTMLDivElement>(null);
  const pid = paper.id;

  // 载入分块
  useEffect(() => {
    setChunks(null); setChunksErr(null); setHit(null); setBbox(null); setTrans({}); setPage(1);
    fetch(`${apiBase()}/api/papers/${pid}/chunks`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(j => setChunks(j.chunks || []))
      .catch(e => setChunksErr(e instanceof Error ? e.message : String(e)));
  }, [pid]);

  // 收到外部定位请求（点击证据溯源）
  useEffect(() => {
    if (!focus || focus.paperId !== pid) return;
    setBbox(focus.bbox);
    setPage(focus.page);
    setHit(null);
  }, [focus?.nonce, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  // bbox 定位高亮
  useEffect(() => {
    if (!bbox || !chunks) return;
    if (bbox.length !== 4 || bbox.some(n => !Number.isFinite(n))) return;
    const h = chunks.find(ch =>
      ch.bbox?.length === 4 &&
      Math.abs(ch.bbox[0] - bbox[0]) < 3 &&
      Math.abs(ch.bbox[1] - bbox[1]) < 3 &&
      Math.abs(ch.bbox[2] - bbox[2]) < 4 &&
      Math.abs(ch.bbox[3] - bbox[3]) < 4);
    if (h) {
      setPage(h.page);
      setHit(h.id);
      let raf1 = 0, raf2 = 0, t = 0;
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          t = window.setTimeout(() => {
            const body = bodyRef.current;
            const el = document.getElementById(`pp-${pid}-${h.id}`);
            if (body && el) {
              const bodyRect = body.getBoundingClientRect();
              const elRect = el.getBoundingClientRect();
              const offsetTop = elRect.top - bodyRect.top + body.scrollTop;
              const target = Math.max(0, offsetTop - body.clientHeight / 2 + el.offsetHeight / 2);
              body.scrollTo({ top: target, behavior: "smooth" });
            }
          }, 120);
        });
      });
      return () => { cancelAnimationFrame(raf1); cancelAnimationFrame(raf2); window.clearTimeout(t); };
    }
  }, [chunks, bbox, pid]);

  // 双语翻译（按当前页段落）：带重试，失败保留已有译文
  useEffect(() => {
    if (!chunks) return;
    if (lang !== "zh") { setTrans({}); return; }
    const ids = chunks
      .filter(c => c.page === page && ["paragraph", "ocr", "caption"].includes(c.kind))
      .map(c => c.id.split(":").pop() as string);
    if (!ids.length) return;
    let alive = true;

    const tryFetch = (attempt: number) => {
      fetch(`${apiBase()}/api/translate`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "paper", paper_id: pid, lang: "zh", chunk_ids: ids, limit: 300 }),
      }).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }).then(j => {
        if (!alive) return;
        const map: Record<string, string> = {};
        let cnt = 0;
        for (const item of j.items || []) {
          if (item.translation) { map[item.chunk_id] = item.translation; cnt++; }
        }
        if (cnt === 0 && attempt < 3) {
          setTimeout(() => tryFetch(attempt + 1), 1200 * attempt);
          return;
        }
        setTrans(prev => ({ ...prev, ...map }));
      }).catch(() => {
        if (!alive) return;
        if (attempt < 3) setTimeout(() => tryFetch(attempt + 1), 800 * attempt);
      });
    };
    tryFetch(1);
    return () => { alive = false; };
  }, [lang, pid, chunks, page]);

  const pages = useMemo(() => {
    if (!chunks) return [];
    return Array.from(new Set(chunks.map(c => c.page))).sort((a, b) => a - b);
  }, [chunks]);
  const pageChunks = useMemo(
    () => (chunks || []).filter(c => c.page === page && c.kind !== "reference"),
    [chunks, page]);

  function jumpToPage(p: number) {
    if (!pages.length) return;
    const clamped = Math.min(Math.max(p, pages[0]), pages[pages.length - 1]);
    setPage(clamped);
    if (hit && chunks && !chunks.find(c => c.id === hit && c.page === clamped)) {
      setHit(null); setBbox(null);
    }
    bodyRef.current?.scrollTo({ top: 0 });
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
        onClick={() => setOpen(o => !o)}>
        <h3 style={{ margin: 0, fontSize: 14, maxWidth: "70%", overflow: "hidden",
          textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {open ? "▼" : "▶"} 📖 {paper.title?.slice(0, 36) || paper.id}
        </h3>
        <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12 }}
          onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}>
          {open ? "收起" : "展开"}
        </button>
      </div>

      {open && (
        <>
          <div className="row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
            <button className={`btn ghost ${lang === "zh" ? "chip" : ""}`}
              style={{ padding: "2px 8px", fontSize: 12 }}
              onClick={() => setLang(lang === "zh" ? "en" : "zh")}>
              {lang === "zh" ? "🔄 隐藏译文" : "🔄 显示译文"}
            </button>
            <a className="chip ghost" style={{ fontSize: 12, padding: "2px 8px" }}
              href={`/reader?id=${pid}&page=${page}`} target="_blank">完整阅读 ↗</a>
            {hit && (
              <span className="chip" style={{ background: "#fef3c7", borderColor: "#f59e0b", fontSize: 12 }}>
                🎯 已定位 · 第 {page} 页
              </span>
            )}
          </div>

          <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
            <button className="btn ghost" style={{ padding: "2px 10px" }}
              disabled={!pages.length || page <= pages[0]}
              onClick={() => jumpToPage(pages[pages.indexOf(page) - 1] ?? page)}>‹ 上一页</button>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>第 {page} 页 / 共 {pages.length} 页</span>
            <button className="btn ghost" style={{ padding: "2px 10px" }}
              disabled={!pages.length || page >= pages[pages.length - 1]}
              onClick={() => jumpToPage(pages[pages.indexOf(page) + 1] ?? page)}>下一页 ›</button>
          </div>

          <div ref={bodyRef} style={{
            marginTop: 8, overflowY: "auto", maxHeight: 380, minHeight: 120,
            borderTop: "1px solid var(--line)", paddingTop: 10,
          }}>
            {chunksErr ? (
              <ErrorState title="原文载入失败" message={chunksErr} />
            ) : chunks === null ? (
              <div className="skeleton">
                <div className="sk-line" style={{ width: "90%" }} />
                <div className="sk-line" style={{ width: "75%" }} />
              </div>
            ) : pageChunks.length === 0 ? (
              <Empty compact icon="📄" title="本页无正文" desc="试试翻页。" />
            ) : (
              pageChunks.map(c => (
                <div key={c.id} id={`pp-${pid}-${c.id}`}
                  style={{
                    marginBottom: 10, padding: "6px 8px", borderRadius: 6,
                    background: hit === c.id ? "#fde68a" : undefined,
                    outline: hit === c.id ? "2px solid #f59e0b" : undefined,
                    outlineOffset: hit === c.id ? 2 : undefined,
                  }}>
                  {c.kind === "heading" ? (
                    <div style={{ fontWeight: 600, fontSize: 13.5, marginTop: 4 }}>{c.text}</div>
                  ) : (
                    <div style={{ fontSize: 12.5, lineHeight: 1.65, color: "var(--ink-2)",
                      whiteSpace: "pre-wrap" }}>{c.text}</div>
                  )}
                  {lang === "zh" && trans[c.id] && c.kind !== "heading" && c.kind !== "caption" && (
                    <div style={{ marginTop: 4, color: "var(--ink-3)", fontSize: 12 }}>
                      🇨🇳 {trans[c.id]}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}


export default function ChatPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [papersLoading, setPapersLoading] = useState(true);
  const [papersErr, setPapersErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("self-attention 在 Transformer 中如何工作？与 RNN 相比优势是什么？");
  const [aspect, setAspect] = useState("核心方法、参数规模、实验结论、局限性");
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<any>(null);
  const [answerErr, setAnswerErr] = useState<string | null>(null);
  const [mode, setMode] = useState<"chat" | "compare">("chat");
  const [hierarchical, setHierarchical] = useState(false);

  // 原文对照面板（整体开关 + 证据定位请求）
  const [panelOpen, setPanelOpen] = useState(true);
  const [focus, setFocus] = useState<FocusReq>(null);

  async function loadPapers() {
    setPapersLoading(true); setPapersErr(null);
    try {
      const r = await fetch(`${apiBase()}/api/papers`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setPapers(j.papers || []);
    } catch (e) {
      setPapersErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPapersLoading(false);
    }
  }

  useEffect(() => { loadPapers(); }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setSelected([id]);
  }, []);

  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!loading) { setElapsed(0); return; }
    const t = setInterval(() => setElapsed(v => v + 1), 1000);
    return () => clearInterval(t);
  }, [loading]);

  const selPapers = useMemo(() => papers.filter(p => selected.includes(p.id)), [papers, selected]);

  async function submit() {
    if (mode === "chat" && selected.length === 0) { alert("请先点选至少一篇论文"); return; }
    if (mode === "chat" && selected.length >= 2 && !hierarchical) {
      alert("单篇问答一次只选 1 篇；多论文对比请到下方 ⚖ 标签。" +
        "\n\n需要同时检索多篇？请勾选页面顶部的「长文档分层检索」开关后提交。");
      return;
    }
    if (mode === "compare" && selected.length < 2) { alert("至少选 2 篇论文对比"); return; }
    setLoading(true); setAnswer(null); setAnswerErr(null);
    try {
      const url = mode === "chat" ? `${apiBase()}/api/chat` : `${apiBase()}/api/compare`;
      const body = mode === "chat"
        ? { query, paper_ids: selected.length ? selected : null, top_k: 8, hierarchical }
        : { paper_ids: selected, aspect };
      const r = await fetch(url, { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAnswer(await r.json());
    } catch (e) {
      setAnswerErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const showPanel = panelOpen && selected.length > 0;

  return (
    <div className={showPanel ? "chat-layout" : ""}>
      <div className="grid chat-main" style={showPanel ? { flex: 1, minWidth: 0 } : {}}>
      <section className="card">
        <div className="row">
          <button className={`btn ${mode === "chat" ? "" : "ghost"}`} onClick={() => setMode("chat")}>💬 单篇问答</button>
          <button className={`btn ${mode === "compare" ? "" : "ghost"}`} onClick={() => setMode("compare")}>⚖ 多论文对比</button>
          <span style={{ flex: 1 }} />
          {selected.length > 0 && (
            <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 13 }}
              onClick={() => setPanelOpen(v => !v)}>
              {panelOpen ? "收起原文 ▸" : "◂ 原文对照"}
            </button>
          )}
          {mode === "chat" && (
            <label style={{ fontSize: 12, color: "var(--ink-3)", marginLeft: 8, cursor: "pointer" }}>
              <input type="checkbox" checked={hierarchical} onChange={e => setHierarchical(e.target.checked)}
                style={{ marginRight: 4 }} />
              长文档分层检索
            </label>
          )}
        </div>

        <div className="row" style={{ marginTop: 12, alignItems: "stretch" }}>
          {mode === "chat" ? (
            <textarea rows={3} value={query} onChange={e => setQuery(e.target.value)}
              placeholder={hierarchical
                ? "（分层检索模式）可以问长文后半部分的细节、特定章节、作者后续工作…"
                : "输入问题，不填则检索全库…"}
              style={{ flex: 1, resize: "vertical", padding: 10, borderRadius: 8,
                border: "1px solid var(--line)", fontFamily: "inherit" }} />
          ) : (
            <textarea rows={3} value={aspect} onChange={e => setAspect(e.target.value)}
              placeholder="多论文对比维度（中文逗号分隔）"
              style={{ flex: 1, resize: "vertical", padding: 10, borderRadius: 8,
                border: "1px solid var(--line)", fontFamily: "inherit" }} />
          )}
          <button className="btn" style={{ marginLeft: 10 }} onClick={submit} disabled={loading}>
            {mode === "chat" ? "检索并回答" : "对比并回答"}
          </button>
        </div>
      </section>

      <section className="card">
        <div className="row" style={{ alignItems: "center", justifyContent: "space-between" }}>
          <strong>选择论文（可多选）</strong>
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
            已选 {selected.length} 篇
            {mode === "chat" && selected.length > 1 && !hierarchical &&
              <span style={{ color: "var(--warn)", marginLeft: 6 }}>
                ⚠ 单篇问答一次只选 1 篇，或勾选「长文档分层检索」
              </span>}
          </span>
        </div>
        {papersLoading && <div className="skeleton">
          <div className="sk-line" style={{ width: "70%" }} />
          <div className="sk-line" style={{ width: "55%" }} />
        </div>}
        {papersErr && <ErrorState title="论文列表载入失败" message={papersErr} onRetry={loadPapers} />}
        {!papersLoading && !papersErr && papers.length === 0 &&
          <Empty icon="📚" title="论文库为空" desc="请先到首页上传 PDF。" />}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          {papers.map(p => {
            const on = selected.includes(p.id);
            return (
              <button key={p.id}
                onClick={() => setSelected(arr => on
                  ? arr.filter(x => x !== p.id)
                  : (mode === "chat" && !hierarchical ? [p.id] : [...arr, p.id]))}
                className={`chip ghost ${on ? "chip" : ""}`}
                style={{
                  border: "1px solid var(--line)",
                  background: on ? "var(--brand)" : "#fff",
                  color: on ? "#fff" : "var(--ink-2)",
                  cursor: "pointer", padding: "4px 10px", fontSize: 12,
                }}>
                {p.title?.slice(0, 34) || p.id}
              </button>
            );
          })}
        </div>
        {mode === "chat" && (
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-3)" }}>
            点选论文可只在这几篇里检索；不选则检索全库（{papers.length} 篇）。
          </div>
        )}
      </section>

      {loading && (
        <section className="card">
          <strong>正在{mode === "chat" ? "检索证据并生成答案" : "对比论文…"}…</strong>
          <div style={{ marginTop: 6, color: "var(--ink-3)", fontSize: 12 }}>
            {hierarchical ? "长文档分层检索优先命中章节级证据，再下钻到具体段落。" :
              "正在检索本地文献、按证据归纳结论…"}
          </div>
          <div className="skeleton" style={{ marginTop: 14 }}>
            <div className="sk-line" style={{ width: "92%" }} />
            <div className="sk-line" style={{ width: "80%" }} />
            <div className="sk-line" style={{ width: "70%" }} />
          </div>
        </section>
      )}

      {answerErr && (
        <section className="card">
          <ErrorState title="请求失败" message={answerErr} />
        </section>
      )}

      {answer && !loading && (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>答案</strong>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              模式：{answer.mode_label || answer.mode} · 置信度 {Number(answer.confidence || 0).toFixed(2)} · {Number(answer.elapsed || 0).toFixed(2)}s
            </span>
          </div>
          <div style={{ marginTop: 10, lineHeight: 1.75, whiteSpace: "pre-wrap" }}>
            {answer.answer}
          </div>
          {(answer.sources?.length || 0) > 0 && (
            <div className="sources">
              <strong>📎 证据溯源（点击右侧对照定位）</strong>
              {answer.sources.map((s: any) => (
                <a key={s.sid} className="source"
                  href={`/reader?id=${s.paper_id}&page=${s.page}${s.bbox?.length ?
                    `#bbox=${s.bbox.join(",")}` : ""}`}
                  onClick={(e) => {
                    // 面板打开时，点击证据在对应论文的面板内定位高亮
                    e.preventDefault();
                    setFocus({ paperId: s.paper_id, bbox: s.bbox?.length === 4 ? s.bbox : null,
                      page: s.page, nonce: Date.now() });
                    if (!panelOpen) setPanelOpen(true);
                  }}>
                  <span className="sid">[{s.sid}]</span>
                  <strong>{s.paper_title}</strong> · 第 {s.page} 页 · {s.section || "-"}
                  <div style={{ marginTop: 4, fontSize: 12, color: "var(--ink-3)" }}>
                    {s.text.slice(0, 220)}
                  </div>
                </a>
              ))}
            </div>
          )}
          {(answer.dropped_citations?.length || 0) > 0 && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--warn)" }}>
              ⚠ 以下引用编号已被模型生成但证据中不存在，已自动剔除：
              {answer.dropped_citations.join(", ")}
            </div>
          )}
        </section>
      )}
      </div>

      {showPanel && (
        <aside className="card chat-aside" style={{ display: "flex", flexDirection: "column" }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>📖 原文对照（{selPapers.length} 篇）</h3>
            {selPapers.length > 1 && (
              <span style={{ fontSize: 12, color: "var(--ink-3)" }}>每篇可独立收起 / 翻页</span>
            )}
          </div>
          <div style={{ marginTop: 10, overflowY: "auto", flex: 1 }}>
            {selPapers.map(p => (
              <PaperPanel key={p.id} paper={p} focus={focus} />
            ))}
          </div>
        </aside>
      )}
    </div>
  );
}
