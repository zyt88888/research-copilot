"use client";
import { apiBase } from "../../lib/apiBase";
import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorState, SkeletonCard } from "../../components/State";

type Paper = { id: string; title: string; year: number | null };
type Chunk = { id: string; page: number; kind: string; section: string; text: string; bbox?: number[] };

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

  // 原文对照面板
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelPaperId, setPanelPaperId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [chunksErr, setChunksErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const panelBodyRef = useRef<HTMLDivElement>(null);

  // 面板高亮 & 双语
  const [panelBbox, setPanelBbox] = useState<number[] | null>(null);
  const [panelHit, setPanelHit] = useState<string | null>(null);
  const [panelLang, setPanelLang] = useState<"zh" | "en">("zh");
  const [panelTrans, setPanelTrans] = useState<Record<string, string>>({});

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

  // 支持 /chat?id=xxx（文库卡片「问答」入口）预选指定论文
  useEffect(() => {
    if (typeof window === "undefined") return;
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setSelected([id]);
  }, []);

  // 生成耗时计时：大模型回答通常 10–30s，给出秒数避免看起来像卡死
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!loading) { setElapsed(0); return; }
    const t = setInterval(() => setElapsed(v => v + 1), 1000);
    return () => clearInterval(t);
  }, [loading]);

  const selPapers = useMemo(() => papers.filter(p => selected.includes(p.id)), [papers, selected]);

  // 选中变化时：默认展示第一篇选中论文的原文
  useEffect(() => {
    if (selected.length === 0) { setPanelPaperId(null); return; }
    if (!panelPaperId || !selected.includes(panelPaperId)) {
      setPanelPaperId(selected[0]);
    }
  }, [selected]);

  // 载入面板论文的分块（切换面板论文时同时清掉高亮 / 翻译）
  useEffect(() => {
    if (!panelPaperId) { setChunks(null); setPanelHit(null); setPanelBbox(null); setPanelTrans({}); return; }
    setChunks(null); setChunksErr(null); setPage(1);
    setPanelHit(null); setPanelBbox(null); setPanelTrans({});
    fetch(`${apiBase()}/api/papers/${panelPaperId}/chunks`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(j => setChunks(j.chunks || []))
      .catch(e => setChunksErr(e instanceof Error ? e.message : String(e)));
  }, [panelPaperId]);

  // chunks 载入后，若有 bbox 需要定位 → 找对应 chunk 并高亮
  useEffect(() => {
    if (!panelBbox || !chunks) return;
    if (panelBbox.length !== 4 || panelBbox.some(n => !Number.isFinite(n))) return;
    // 先跳转到证据所在页，再高亮 chunk
    const hit = chunks.find(ch =>
      ch.bbox?.length === 4 &&
      Math.abs(ch.bbox[0] - panelBbox[0]) < 3 &&
      Math.abs(ch.bbox[1] - panelBbox[1]) < 3 &&
      Math.abs(ch.bbox[2] - panelBbox[2]) < 4 &&
      Math.abs(ch.bbox[3] - panelBbox[3]) < 4);
    if (hit) {
      setPage(hit.page);
      setPanelHit(hit.id);
      // 等 DOM 渲染完后，在面板容器内平滑滚动到高亮段（不滚整个窗口）
      let raf1 = 0, raf2 = 0, t = 0;
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          t = window.setTimeout(() => {
            const body = panelBodyRef.current;
            const el = document.getElementById(`p-${hit.id}`);
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
  }, [chunks, panelBbox]);

  // 双语：切换 / 面板论文变化时拉翻译
  useEffect(() => {
    if (!panelPaperId || !chunks) return;
    if (panelLang === "zh") {
      fetch(`${apiBase()}/api/translate`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "paper", paper_id: panelPaperId, lang: "zh", limit: 500 }),
      }).then(r => r.json()).then(j => {
        const map: Record<string, string> = {};
        for (const item of j.items || []) map[item.chunk_id] = item.translation;
        setPanelTrans(map);
      }).catch(() => { setPanelTrans({}); });
    } else { setPanelTrans({}); }
  }, [panelLang, panelPaperId, chunks]);

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
    // 翻页时若当前命中不在本页，清除面板高亮
    if (panelHit && chunks && !chunks.find(c => c.id === panelHit && c.page === clamped)) {
      setPanelHit(null); setPanelBbox(null);
    }
    panelBodyRef.current?.scrollTo({ top: 0 });
  }

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

  const showPanel = panelOpen && panelPaperId && selected.length > 0;
  const panelPaper = papers.find(p => p.id === panelPaperId) || null;

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
              onClick={() => {
                setPanelOpen(v => !v);
              }}>
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
          <button className="btn" style={{ marginLeft: 10 }}
            onClick={submit} disabled={loading}>
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
        {papersErr && <ErrorState title="论文列表载入失败" message={papersErr}
          onRetry={loadPapers} />}
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
              "当前为稳健模式（内置本地检索 + 抽取式归纳；接入 LLM_API_KEY 可获得更连贯的论述式回答）。"}
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
              <strong>📎 证据溯源（点击{showPanel ? "右侧对照" : "跳转原文"}）</strong>
              {answer.sources.map((s: any) => (
                <a key={s.sid} className="source"
                  href={`/reader?id=${s.paper_id}&page=${s.page}${s.bbox?.length ?
                    `#bbox=${s.bbox.join(",")}` : ""}`}
                  onClick={(e) => {
                    // 面板正在展示这篇论文时，点击证据直接在面板内定位并高亮，不打断当前页面
                    if (showPanel && s.paper_id === panelPaperId) {
                      e.preventDefault();
                      if (s.bbox?.length === 4) setPanelBbox(s.bbox);
                      setPage(s.page);
                    } else if (showPanel && s.paper_id !== panelPaperId) {
                      // 面板展示不同论文时，先切论文再触发定位
                      e.preventDefault();
                      setPanelBbox(s.bbox?.length === 4 ? s.bbox : null);
                      setPanelPaperId(s.paper_id);
                    }
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
        <aside className="card chat-aside">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>📖 原文对照</h3>
            <div className="row" style={{ gap: 6 }}>
              <button className={`btn ghost ${panelLang === "zh" ? "chip" : ""}`}
                style={{ padding: "2px 8px", fontSize: 12 }}
                onClick={() => setPanelLang(panelLang === "zh" ? "en" : "zh")}>
                {panelLang === "zh" ? "🔄 显示中文译文" : "🔄 隐藏译文"}
              </button>
              <a className="chip ghost" style={{ fontSize: 12, padding: "2px 8px" }}
                href={`/reader?id=${panelPaperId}&page=${page}${panelBbox?.length ? `#bbox=${panelBbox.join(",")}` : ""}`}
                target="_blank">
                完整阅读 ↗
              </a>
            </div>
          </div>

          {selPapers.length > 1 && (
            <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {selPapers.map(p => (
                <button key={p.id} className="chip ghost"
                  style={{ cursor: "pointer", padding: "3px 8px", fontSize: 12,
                    border: "1px solid var(--line)",
                    background: p.id === panelPaperId ? "var(--ink)" : "#fff",
                    color: p.id === panelPaperId ? "#fff" : "var(--ink-2)" }}
                  onClick={() => setPanelPaperId(p.id)}>
                  {p.title?.slice(0, 26)}
                </button>
              ))}
            </div>
          )}

          <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-3)" }}>
            {panelPaper?.title}
          </div>

          <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
            <button className="btn ghost" style={{ padding: "2px 10px" }}
              disabled={!pages.length || page <= pages[0]}
              onClick={() => jumpToPage(pages[pages.indexOf(page) - 1] ?? page)}>‹ 上一页</button>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              第 {page} 页 / 共 {pages.length} 页
            </span>
            <button className="btn ghost" style={{ padding: "2px 10px" }}
              disabled={!pages.length || page >= pages[pages.length - 1]}
              onClick={() => jumpToPage(pages[pages.indexOf(page) + 1] ?? page)}>下一页 ›</button>
          </div>

          {panelHit && (
            <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
              <span className="chip" style={{ background: "#fef3c7", borderColor: "#f59e0b" }}>
                🎯 已定位到引用证据 · 第 {page} 页
              </span>
              <button className="btn ghost" style={{ marginLeft: 10, padding: "2px 8px", fontSize: 12 }}
                onClick={() => { setPanelHit(null); setPanelBbox(null); }}>清除高亮</button>
            </div>
          )}

          <div ref={panelBodyRef} style={{
            marginTop: panelHit ? 8 : 10, overflowY: "auto", flex: 1, minHeight: 200,
            borderTop: "1px solid var(--line)", paddingTop: 10,
          }}>
            {chunksErr ? (
              <ErrorState title="原文载入失败" message={chunksErr} />
            ) : chunks === null ? (
              <div className="skeleton">
                <div className="sk-line" style={{ width: "90%" }} />
                <div className="sk-line" style={{ width: "75%" }} />
                <div className="sk-line" style={{ width: "82%" }} />
              </div>
            ) : pageChunks.length === 0 ? (
              <Empty compact icon="📄" title="本页无正文" desc="试试翻页。" />
            ) : (
              pageChunks.map(c => (
                <div key={c.id} id={`p-${c.id}`}
                  style={{
                    marginBottom: 10,
                    padding: "6px 8px",
                    background: panelHit === c.id ? "#fde68a" : undefined,
                    outline: panelHit === c.id ? "2px solid #f59e0b" : undefined,
                    outlineOffset: panelHit === c.id ? 2 : undefined,
                    borderRadius: 6,
                  }}>
                  {c.kind === "heading" ? (
                    <div style={{ fontWeight: 600, fontSize: 13.5, marginTop: 4 }}>{c.text}</div>
                  ) : (
                    <div style={{ fontSize: 12.5, lineHeight: 1.65, color: "var(--ink-2)",
                      whiteSpace: "pre-wrap" }}>{c.text}</div>
                  )}
                  {panelLang === "zh" && panelTrans[c.id] && c.kind !== "heading" && c.kind !== "caption" && (
                    <div style={{ marginTop: 4, color: "var(--ink-3)", fontSize: 12 }}>
                      🇨🇳 {panelTrans[c.id]}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
