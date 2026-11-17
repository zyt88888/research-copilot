"use client";
import { apiBase } from "../../lib/apiBase";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Empty, ErrorState, Skeleton, SkeletonCard } from "../../components/State";

export const dynamic = "force-dynamic";

type Chunk = { id: string; page: number; bbox: number[]; section: string; kind: string; text: string; seq: number; level?: number };
const FIG_CAP_RE = /^\s*(?:figure|fig\.?)\s*(\d+)/i;

type Meta = { id: string; title: string; authors: string[]; year: number | null; num_pages: number; abstract?: string;
  venue?: string; affiliations?: string[]; page_flags?: Record<string, string>;
  toc?: { title: string; level: number; page: number }[] };

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
  const [transStatus, setTransStatus] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [transErr, setTransErr] = useState<string>("");
  const [transReloadKey, setTransReloadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState<string | null>(null);
  const [pick, setPick] = useState<{ text: string; x: number; y: number; w: number } | null>(null);
  const [pickResult, setPickResult] = useState<{ text: string; mode: string } | null>(null);
  const [pickLoading, setPickLoading] = useState(false);
  const [evidence, setEvidence] = useState(false);
  const [hitExact, setHitExact] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [repro, setRepro] = useState<any>(null);
  const [reco, setReco] = useState<any[] | null>(null);
  const [insightState, setInsightState] = useState<"loading" | "ok" | "fail">("loading");
  // 参考文献含金量 + 批量下钻
  const [refsTotal, setRefsTotal] = useState<number | null>(null);
  const [refQ, setRefQ] = useState<any>(null);
  const [refQBusy, setRefQBusy] = useState(false);
  const [refQErr, setRefQErr] = useState<string | null>(null);
  const [showAllRefs, setShowAllRefs] = useState(false);
  const [drillJob, setDrillJob] = useState<any>(null);
  const [drillMsg, setDrillMsg] = useState<string | null>(null);
  const [drillBusy, setDrillBusy] = useState(false);
  // 双向引用跳转 / 左右对照 / 悬停翻译
  const [refsList, setRefsList] = useState<any[]>([]);
  const [jumpRef, setJumpRef] = useState<number | null>(null);
  const [bilingual, setBilingual] = useState(false);
  const [hoverOn, setHoverOn] = useState(false);
  const [hoverTip, setHoverTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const rightScrollerRef = useRef<HTMLDivElement>(null);
  const syncLock = useRef(false);
  const hoverReq = useRef<Set<string>>(new Set());

  // 连续滚动阅读：容器与各页锚点
  const scrollerRef = useRef<HTMLDivElement>(null);
  const pageEls = useRef<Map<number, HTMLDivElement>>(new Map());
  // 已拿到译文的 chunk（按 chunk id），避免滚动时重复请求
  const translatedRef = useRef<Set<string>>(new Set());
  const pendingRef = useRef<Set<string>>(new Set());

  // 学术洞察：复现性 + 相关推荐（不阻塞正文渲染；两个接口都失败时给出提示）
  useEffect(() => {
    if (!pid) return;
    let alive = true;
    setInsightState("loading");
    setRepro(null); setReco(null);
    const reproP = fetch(`${apiBase()}/api/insights/reproducibility?paper_id=${pid}`)
      .then(r => (r.ok ? r.json() : null)).then(j => { if (alive) setRepro(j); return !!j; })
      .catch(() => false);
    const recoP = fetch(`${apiBase()}/api/insights/recommend?paper_id=${pid}&top_k=5`)
      .then(r => (r.ok ? r.json() : null)).then(j => { if (alive) setReco(j?.items || []); return !!j; })
      .catch(() => false);
    Promise.all([reproP, recoP]).then(([a, b]) => {
      if (alive) setInsightState(a || b ? "ok" : "fail");
    });
    return () => { alive = false; };
  }, [pid]);

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
      const r = await fetch(`${apiBase()}/api/translate`, {
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
    Promise.all([
      fetch(`${apiBase()}/api/papers/${pid}`).then(r => {
        if (!r.ok) throw new Error(`论文信息载入失败（HTTP ${r.status}）`);
        return r.json();
      }),
      fetch(`${apiBase()}/api/papers/${pid}/chunks`).then(r => {
        if (!r.ok) throw new Error(`论文内容载入失败（HTTP ${r.status}）`);
        return r.json();
      }),
      fetch(`${apiBase()}/api/papers/${pid}/refs`)
        .then(r => (r.ok ? r.json() : { refs: [] })).catch(() => ({ refs: [] })),
    ]).then(([m, c, rf]) => {
      setMeta(m); setChunks(c.chunks || []);
      setRefsTotal((rf.refs || []).length);
      setRefsList(rf.refs || []);
      const p = Math.min(Math.max(wantPage || 1, 1), m.num_pages || 1);
      setPage(p - 1);
    }).catch(e => setErr(e instanceof Error ? e.message : String(e)));
  }, [pid, reloadKey]);

  // 证据深链定位：分块就绪后按 bbox 精确定位；匹配不到则回退到该页首个段落，
  // 保证「点击证据 → 打开对应页并高亮」不会静默失效
  useEffect(() => {
    if (!pid || !meta || !chunks.length) return;
    const raw = params.get("bbox")
      || (window.location.hash.startsWith("#bbox=") ? window.location.hash.slice(6) : "");
    const nums = raw ? raw.split(",").map(Number) : [];
    if (nums.length !== 4 || !nums.every(n => Number.isFinite(n))) return;
    const p = Math.min(Math.max(wantPage || 1, 1), meta.num_pages || 1);
    const target = chunks.find(ch =>
      ch.page === p && ch.bbox?.length === 4 &&
      Math.abs(ch.bbox[0] - nums[0]) < 3 && Math.abs(ch.bbox[1] - nums[1]) < 3 &&
      Math.abs(ch.bbox[2] - nums[2]) < 4 && Math.abs(ch.bbox[3] - nums[3]) < 4);
    if (target) {
      setHit(target.id); setHitExact(true); setEvidence(true); setPage(p - 1);
      return;
    }
    const fallback = chunks.find(ch => ch.page === p &&
      ["paragraph", "ocr", "caption"].includes(ch.kind));
    if (fallback) {
      setHit(fallback.id); setHitExact(false); setEvidence(true); setPage(p - 1);
    }
  }, [pid, meta, chunks, wantPage]);

  // 命中证据块 / 搜索结果后滚动到可视区域居中
  useEffect(() => {
    if (!hit) return;
    const t = setTimeout(() => {
      document.getElementById(`c-${hit}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 180);
    return () => clearTimeout(t);
  }, [hit, evidence]);

  // 深链 ?page=N：内容渲染后把滚动容器定位到该页
  useEffect(() => {
    if (!meta || !chunks.length) return;
    const p = Math.min(Math.max(wantPage || 1, 1), meta.num_pages || 1);
    if (p <= 1) return;
    const t = setTimeout(() => {
      const el = pageEls.current.get(p);
      const sc = scrollerRef.current;
      if (el && sc) sc.scrollTo({ top: Math.max(0, el.offsetTop - 8) });
    }, 260);
    return () => clearTimeout(t);
  }, [chunks, meta, wantPage]);

  // 译文按「当前可视页 + 邻近页」分批加载；滚动到新页时自动补齐。
  // 带重试（最多 3 次），失败时保留已有译文并给出可见的重试按钮，
  // 避免网络抖动 / NMT 预热未完成时译文静默消失。
  useEffect(() => {
    if (!pid) return;
    setTrans({}); setTransStatus("idle"); setTransErr("");
    translatedRef.current = new Set();
    pendingRef.current = new Set();
  }, [pid]);

  useEffect(() => {
    if (!pid || !meta) return;
    if (lang !== "zh") { setTransStatus("idle"); return; }
    const cur = page + 1;
    const windowPages = [cur - 1, cur, cur + 1, cur + 2]
      .filter(p => p >= 1 && p <= (meta.num_pages || 1));
    const ids = chunks
      .filter(c => windowPages.includes(c.page)
        && ["paragraph", "ocr", "caption"].includes(c.kind)
        && !translatedRef.current.has(c.id)
        && !pendingRef.current.has(c.id))
      .map(c => c.id.split(":").pop() as string);
    if (!ids.length) return;
    let alive = true;
    ids.forEach(id => pendingRef.current.add(id));
    const release = () => ids.forEach(id => pendingRef.current.delete(id));
    setTransStatus("loading");

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
        let translatedCount = 0;
        for (const item of j.items || []) {
          if (item.translation) {
            map[item.chunk_id] = item.translation;
            translatedRef.current.add(item.chunk_id);
            translatedCount++;
          }
        }
        // 译文为空通常是 NMT 仍在预热，当作可重试错误
        if (translatedCount === 0 && attempt < 3) {
          setTimeout(() => tryFetch(attempt + 1), 1200 * attempt);
          return;
        }
        release();
        // 合并到已有译文，不覆盖其它页已加载的内容
        setTrans(prev => ({ ...prev, ...map }));
        setTransStatus(translatedCount > 0 ? "ok" : "error");
        if (translatedCount === 0) setTransErr("本页暂无可用译文（翻译服务可能仍在预热，请重试）");
      }).catch(err => {
        if (!alive) return;
        if (attempt < 3) {
          setTimeout(() => tryFetch(attempt + 1), 800 * attempt);
        } else {
          release();
          // 保留已有译文，只标记错误状态
          setTransStatus("error");
          setTransErr(err instanceof Error ? err.message : String(err));
        }
      });
    };
    tryFetch(1);
    return () => { alive = false; };
  }, [lang, pid, meta, page, chunks, transReloadKey]);

  /** 参考文献含金量分析（正文提及 + 语义线索 + 章节位置）。 */
  async function analyzeRefs() {
    if (!pid) return;
    setRefQBusy(true); setRefQErr(null);
    try {
      const r = await fetch(`${apiBase()}/api/papers/${pid}/refs/quality`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `分析失败（HTTP ${r.status}）`);
      setRefQ(j);
    } catch (e) {
      setRefQErr(e instanceof Error ? e.message : String(e));
    } finally { setRefQBusy(false); }
  }

  /** 下钻下载：refIds 为空时按含金量批量挑高价值文献。 */
  async function runDrill(refIds?: number[]) {
    if (!pid) return;
    setDrillBusy(true); setDrillMsg(null); setDrillJob(null);
    try {
      const fd = new FormData();
      fd.append("limit", String(refIds?.length || 3));
      fd.append("min_score", "30");
      fd.append("quality_filter", refIds ? "false" : "true");
      if (refIds) fd.append("ref_ids", refIds.join(","));
      const r = await fetch(`${apiBase()}/api/papers/${pid}/drill`,
        { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `下钻失败（HTTP ${r.status}）`);
      if (!j.job_id) { setDrillMsg(j.message || "没有符合条件的参考文献"); return; }
      let last: any = null;
      for (let i = 0; i < 600; i++) {
        await new Promise(res => setTimeout(res, 1000));
        const jr = await fetch(`${apiBase()}/api/papers/drill-jobs/${j.job_id}`);
        if (!jr.ok) throw new Error(`进度查询失败（HTTP ${jr.status}）`);
        last = await jr.json();
        setDrillJob(last);
        if (last.status !== "running") break;
      }
      if (last?.status === "done") {
        const ok = (last.items || []).filter((x: any) => x.status === "ok").length;
        setDrillMsg(`${last.stage}${ok ? `（新入库 ${ok} 篇）` : ""}`);
      } else if (last) {
        setDrillMsg("下钻仍在后台进行，稍后回到文库刷新即可");
      }
    } catch (e) {
      setDrillMsg(e instanceof Error ? e.message : String(e));
    } finally { setDrillBusy(false); }
  }

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

  // 连续滚动阅读：按 PDF 页码分组（仅保留有正文的分组），保留原始阅读顺序
  const READ_KINDS = ["paragraph", "table", "ocr", "caption", "formula", "heading", "figure"];
  const docChunks = chunks.filter(c => READ_KINDS.includes(c.kind));
  const pageGroups: { page: number; items: Chunk[] }[] = [];
  for (const c of docChunks) {
    const last = pageGroups[pageGroups.length - 1];
    if (last && last.page === c.page) last.items.push(c);
    else pageGroups.push({ page: c.page, items: [c] });
  }
  // 图号：优先取图注中的 Figure N，否则按出现顺序编号（与后端裁剪定位一致）
  const figureNo = new Map<string, number>();
  let figSeq = 0;
  for (const c of docChunks) {
    if (c.kind !== "figure" && c.kind !== "caption") continue;
    const m = (c.text || "").match(FIG_CAP_RE);
    if (!m && c.kind === "caption") continue;
    figureNo.set(c.id, m ? parseInt(m[1], 10) : ++figSeq);
  }

  /** 跳转到指定页：把滚动容器滚到该页锚点。 */
  function gotoPage(p: number) {
    const el = pageEls.current.get(p);
    const sc = scrollerRef.current;
    if (!el || !sc) return;
    sc.scrollTo({ top: Math.max(0, el.offsetTop - 8), behavior: "smooth" });
    setPage(p - 1);
  }

  /** 滚动时把顶部的页号同步到状态，用于译文按可视页加载。 */
  function onDocScroll() {
    const sc = scrollerRef.current;
    if (!sc) return;
    const probe = sc.scrollTop + 48;
    let cur = pageGroups[0]?.page ?? 1;
    for (const g of pageGroups) {
      const node = pageEls.current.get(g.page);
      if (node && node.offsetTop <= probe) cur = g.page;
      else break;
    }
    if (cur - 1 !== page) setPage(cur - 1);
  }

  /** 正文 [n] → 参考文献条目（高亮 2 秒） */
  function jumpToRef(n: number) {
    setJumpRef(n);
    document.getElementById(`ref-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => setJumpRef(null), 2200);
  }

  /** 参考文献 [n] → 正文首次引用处 */
  function jumpToCitation(n: number) {
    const re = new RegExp(`\\[\\s*${n}\\s*(?:[,，\\]–-])`);
    const ch = docChunks.find(c => re.test(c.text));
    if (!ch) return;
    setPage((ch.page || 1) - 1);
    setHit(ch.id);
    setTimeout(() => document.getElementById(`c-${ch.id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
  }

  /** 把正文中的 [11] / [11,12] / [11–13] 渲染为可点击角标 */
  function renderCitations(text: string) {
    const parts = text.split(/(\[\s*\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*\s*\])/g);
    if (parts.length === 1) return text;
    return parts.map((part, i) => {
      const m = part.match(/^\[\s*(\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*)\s*\]$/);
      if (!m) return <span key={i}>{part}</span>;
      const nums: number[] = [];
      for (const seg of m[1].split(",")) {
        const range = seg.trim().split(/[–-]/).map(x => parseInt(x, 10));
        if (range.length === 2 && range.every(Number.isFinite) && range[1] - range[0] < 20) {
          for (let n = range[0]; n <= range[1]; n++) nums.push(n);
        } else {
          const n = parseInt(seg.trim(), 10);
          if (Number.isFinite(n)) nums.push(n);
        }
      }
      const valid = nums.filter(n => n >= 1 && n <= refsList.length);
      if (!valid.length) return <span key={i}>{part}</span>;
      return (
        <span key={i}>
          {valid.map((n, k) => (
            <button key={k} onClick={(e) => { e.stopPropagation(); jumpToRef(n); }}
              title={`跳转到参考文献 [${n}]`}
              style={{ border: "none", background: "#eef2ff", color: "#4338ca",
                borderRadius: 3, padding: "0 2px", margin: "0 1px", fontSize: "0.78em",
                cursor: "pointer", verticalAlign: "super", lineHeight: 1 }}>
              [{n}]
            </button>
          ))}
        </span>
      );
    });
  }

  /** 左右两栏按滚动比例同步（避免互相触发死循环） */
  function syncScroll(from: "left" | "right") {
    if (!bilingual || syncLock.current) return;
    const a = scrollerRef.current;
    const b = rightScrollerRef.current;
    if (!a || !b) return;
    syncLock.current = true;
    const src = from === "left" ? a : b;
    const dst = from === "left" ? b : a;
    const ratio = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight);
    dst.scrollTop = ratio * Math.max(1, dst.scrollHeight - dst.clientHeight);
    setTimeout(() => { syncLock.current = false; }, 80);
  }

  /** 悬停翻译：开启后鼠标停留在段落上即取译文（有缓存则不重复请求） */
  async function hoverTranslate(c: Chunk, e: React.MouseEvent) {
    if (!hoverOn || c.kind === "heading") return;
    const x = Math.max(12, Math.min(e.clientX + 14, window.innerWidth - 372));
    const y = Math.max(12, Math.min(e.clientY + 14, window.innerHeight - 220));
    if (trans[c.id]) { setHoverTip({ x, y, text: trans[c.id] }); return; }
    setHoverTip({ x, y, text: "翻译中…" });
    if (hoverReq.current.has(c.id)) return;
    hoverReq.current.add(c.id);
    try {
      const r = await fetch(`${apiBase()}/api/translate`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "text", text: c.text, lang: "zh" }),
      }).then(res => res.json());
      const text = r.text || r.translation || r.notice || "暂无译文";
      setTrans(prev => ({ ...prev, [c.id]: text }));
      setHoverTip(prev => prev ? { ...prev, text } : null);
    } catch {
      hoverReq.current.delete(c.id);
      setHoverTip(prev => prev ? { ...prev, text: "翻译失败：后端不可用" } : null);
    }
  }

  return (
    <div className="grid">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>{meta.title}</h2>
        <div style={{ color: "var(--ink-3)", fontSize: 13 }}>
          {(meta.authors || []).join(", ")} · {meta.year || "n.d."} · {meta.venue || ""} · {meta.num_pages} 页
        </div>
        {(meta.affiliations || []).length > 0 && (
          <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 3 }}>
            🏛 {meta.affiliations.join("；")}
          </div>
        )}
        {meta.abstract && <p style={{ marginTop: 10, lineHeight: 1.7 }}>{meta.abstract}</p>}
        <div className="row grow" style={{ marginTop: 12 }}>
          <button className={`btn ghost ${lang === "zh" ? "chip" : ""}`}
            onClick={() => setLang(lang === "zh" ? "en" : "zh")}>
            {lang === "zh" ? "🔄 隐藏中文译文（仅原文）" : "🔄 显示中文译文（双语对照）"}
          </button>
          <button className={`btn ghost ${bilingual ? "chip" : ""}`}
            onClick={() => { setBilingual(v => !v); if (!bilingual) setLang("zh"); }}
            title="左右两栏对照，拖动任一侧自动同步滚动">
            {bilingual ? "📖 退出左右对照" : "📖 左右对照 + 同步滚动"}
          </button>
          <button className={`btn ghost ${hoverOn ? "chip" : ""}`}
            onClick={() => { setHoverOn(v => !v); setHoverTip(null); }}
            title="开启后鼠标停留在段落上即可查看中文译文">
            💬 悬停翻译
          </button>
          {lang === "zh" && transStatus === "loading" && (
            <span className="chip" style={{ fontSize: 12 }}>⏳ 正在加载译文…</span>
          )}
          {lang === "zh" && transStatus === "ok" && (
            <span className="chip" style={{ fontSize: 12, background: "#dcfce7", borderColor: "#22c55e" }}>
              ✅ 译文已就绪
            </span>
          )}
          {lang === "zh" && transStatus === "error" && (
            <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12, color: "#dc2626", borderColor: "#fecaca" }}
              onClick={() => { setTransErr(""); setTransStatus("loading"); setTransReloadKey(k => k + 1); }}
              title={transErr}>
              ⚠ 译文加载失败，点击重试
            </button>
          )}
          <input type="text" placeholder="全文搜索…"
            value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn ghost" onClick={() => {
            const q = query.trim().toLowerCase();
            if (!q) return;
            const found = docChunks.filter(c => c.text.toLowerCase().includes(q));
            if (!found.length) { setHit(null); return; }
            const idx = hit ? found.findIndex(c => c.id === hit) : -1;
            setHit(found[(idx + 1) % found.length].id);
          }}>查找</button>

          {meta.toc && meta.toc.length > 0 && (
            <details style={{ position: "relative" }}>
              <summary className="btn ghost" style={{ cursor: "pointer", listStyle: "none" }}>
                📑 标题树（{meta.toc.length}）
              </summary>
              <div style={{ position: "absolute", left: 0, top: "calc(100% + 6px)", zIndex: 25,
                width: 320, maxHeight: 300, overflowY: "auto", background: "#fff",
                border: "1px solid var(--line)", borderRadius: 10, padding: 8,
                boxShadow: "0 12px 32px rgba(15,23,42,.16)" }}>
                {meta.toc.map((t, i) => (
                  <div key={i} onClick={() => gotoPage(Math.max(1, t.page || 1))}
                    style={{ padding: "5px 6px", paddingLeft: 6 + (Math.max(1, t.level || 1) - 1) * 16,
                      fontSize: 12.5, cursor: "pointer", borderRadius: 6 }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "#f1f5f9"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = ""; }}>
                    {t.title}
                    <span style={{ color: "var(--ink-3)", marginLeft: 6, fontSize: 11 }}>
                      p{t.page}
                    </span>
                  </div>
                ))}
              </div>
            </details>
          )}
          <a className="btn ghost" href={`${apiBase()}/api/papers/${pid}/export?format=md&lang=both`}>
            ⬇ Markdown
          </a>
          <a className="btn ghost" href={`${apiBase()}/api/papers/${pid}/export?format=html&lang=both`}>
            ⬇ HTML
          </a>
          <a className="btn ghost" href={`${apiBase()}/api/papers/${pid}/pdf`}>
            📄 原版 PDF
          </a>
        </div>
      </section>

      {insightState === "loading" && (
        <section className="card" aria-busy="true">
          <h3 style={{ margin: 0 }}>🔬 学术洞察</h3>
          <div style={{ marginTop: 10, color: "var(--ink-3)", fontSize: 13 }}>
            正在评估复现性与相关推荐…
          </div>
        </section>
      )}

      {insightState === "fail" && (
        <section className="card">
          <h3 style={{ margin: 0 }}>🔬 学术洞察</h3>
          <div style={{ marginTop: 10, color: "var(--ink-3)", fontSize: 13 }}>
            洞察服务暂不可用（不影响正文阅读），可稍后刷新重试。
          </div>
        </section>
      )}

      {insightState === "ok" && (repro || (reco && reco.length > 0)) && (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>🔬 学术洞察</h3>
            {repro && (
              <span className="chip" style={{
                background: repro.score >= 70 ? "#dcfce7" : repro.score >= 40 ? "#fef3c7" : "#fee2e2",
                borderColor: repro.score >= 70 ? "#22c55e" : repro.score >= 40 ? "#f59e0b" : "#ef4444",
              }}>复现性 {repro.score}/100</span>
            )}
          </div>

          {repro && (
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <div style={{ color: "var(--ink-2)" }}>🧪 {repro.verdict}</div>
              {(repro.repos || []).filter((r: any) => r.found).map((r: any) => (
                <div key={r.repo} style={{ marginTop: 6, padding: "6px 10px",
                  border: "1px solid var(--line)", borderRadius: 8, fontSize: 12.5 }}>
                  <a href={r.url} target="_blank" rel="noreferrer">{r.repo}</a>
                  <span style={{ color: "var(--ink-3)", marginLeft: 8 }}>
                    ⭐ {r.stars ?? "-"} · {r.language || "-"} · {r.license || "无 License"} · 更新于 {r.pushed_at || "-"}
                  </span>
                </div>
              ))}
            </div>
          )}

          {reco && reco.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>📚 相关推荐（按标题+摘要相似度）</div>
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 5 }}>
                {reco.slice(0, 5).map((x: any) => (
                  <div key={x.paper_id} style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", gap: 10, padding: "6px 10px",
                    border: "1px solid var(--line)", borderRadius: 8 }}>
                    <span style={{ fontSize: 13 }}>{x.title || x.paper_id}</span>
                    <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12,
                      whiteSpace: "nowrap" }}
                      onClick={() => router.push(`/reader?id=${x.paper_id}`)}>阅读 →</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0 }}>📚 参考文献含金量</h3>
          <div className="row" style={{ gap: 8 }}>
            {refsTotal != null && <span className="chip">{refsTotal} 条参考文献</span>}
            <button className="btn ghost" disabled={refQBusy || refsTotal === 0}
              onClick={analyzeRefs}>
              {refQBusy ? "分析中…" : refQ ? "重新分析" : "分析含金量"}
            </button>
          </div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 8 }}>
          按「正文提及次数 + 上下文语义线索 + 引用章节位置」打分，自动识别只在参考文献列表出现、
          正文零提及的应付式引用；下钻时默认剔除这类文献。
        </div>

        {refQErr && (
          <div style={{ marginTop: 10, padding: 10, borderRadius: 8, background: "#fef2f2",
            border: "1px solid #fecaca", fontSize: 12.5, color: "#991b1b" }}>{refQErr}</div>
        )}

        {refQ && (
          <>
            <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <span className="chip ok">高价值 {refQ.summary.high}</span>
              <span className="chip">一般引用 {refQ.summary.medium}</span>
              <span className="chip warn">弱引用 {refQ.summary.low}</span>
              <span className="chip err">应付式 {refQ.summary.perfunctory}</span>
              <button className="btn" style={{ marginLeft: "auto", padding: "6px 12px", fontSize: 12.5 }}
                disabled={drillBusy} onClick={() => runDrill()}>
                {drillBusy ? "下钻中…" : "⬇ 批量下钻高价值文献（最多 3 篇）"}
              </button>
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 6 }}>
              判定方法：{refQ.summary.method}
            </div>

            {drillMsg && (
              <div style={{ marginTop: 10, padding: 10, borderRadius: 8, background: "#eef2ff",
                border: "1px solid #c7d2fe", fontSize: 12.5, color: "#3730a3" }}>{drillMsg}</div>
            )}
            {drillJob && (
              <div style={{ marginTop: 10, padding: 10, border: "1px solid var(--line)",
                borderRadius: 8 }}>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
                  {drillJob.status === "running"
                    ? `${drillJob.stage} · ${drillJob.done}/${drillJob.total}`
                    : drillJob.stage}
                </div>
                {(drillJob.items || []).map((it: any) => (
                  <div key={it.ref_id ?? it.index} style={{ fontSize: 12, marginTop: 4 }}>
                    <span className="chip" style={{ marginRight: 6 }}
                      title={it.message}>{it.status === "ok" ? "已入库" :
                        it.status === "exists" ? "库中已有" :
                        it.status === "duplicate" ? "内容重复" :
                        it.status === "not_found" ? "未检索到" : "失败"}</span>
                    {(it.title || "").slice(0, 70)}
                  </div>
                ))}
              </div>
            )}

            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
              {(showAllRefs ? refQ.refs : refQ.refs.slice(0, 8)).map((r: any) => (
                <div key={r.ref_id ?? r.index} style={{ padding: "8px 10px",
                  border: "1px solid var(--line)", borderRadius: 8 }}>
                  <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
                    <div style={{ fontSize: 13, minWidth: 0 }}>
                      {r.index}. {r.title || (r.raw || "").slice(0, 90) || "（标题未能解析）"}
                    </div>
                    <div className="row" style={{ gap: 6, flexShrink: 0 }}>
                      <span className={`chip ${r.label === "高价值" ? "ok" :
                        r.label === "应付式" ? "err" :
                        r.label === "弱引用" ? "warn" : ""}`}>
                        {r.label} · {r.score}
                      </span>
                      <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 11.5 }}
                        disabled={drillBusy || r.ref_id == null}
                        onClick={() => runDrill([r.ref_id])}>下钻</button>
                    </div>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 4 }}>
                    正文提及 {r.mentions} 处
                    {r.sections?.length ? ` · ${r.sections.join("、")}` : ""}
                    {r.cues?.length ? ` · 线索：${r.cues.join("、")}` : ""}
                    {r.arxiv_id ? ` · arXiv:${r.arxiv_id}` : ""}
                  </div>
                  {r.evidence?.length > 0 && (
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ fontSize: 11.5, cursor: "pointer", color: "var(--brand)" }}>
                        查看正文证据
                      </summary>
                      {r.evidence.map((e: any, i: number) => (
                        <div key={i} style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 4 }}>
                          第 {e.page} 页 · {e.section || "正文"}：{e.snippet}
                        </div>
                      ))}
                    </details>
                  )}
                </div>
              ))}
            </div>
            {refQ.refs.length > 8 && (
              <button className="btn ghost" style={{ marginTop: 8 }}
                onClick={() => setShowAllRefs(v => !v)}>
                {showAllRefs ? "收起" : `查看全部 ${refQ.refs.length} 条`}
              </button>
            )}
          </>
        )}
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="row grow">
            <button className="btn ghost" disabled={page <= 0} onClick={() => gotoPage(page)}>← 上一页</button>
            <div style={{ textAlign: "center", width: 120 }}>
              第 <strong>{page + 1}</strong> / {meta.num_pages} 页
            </div>
            <button className="btn ghost" disabled={page >= meta.num_pages - 1}
              onClick={() => gotoPage(page + 2)}>下一页 →</button>
            {meta.toc?.length > 0 && (
              <select onChange={(e) => {
                const t = meta.toc![Number(e.target.value)];
                if (t) gotoPage(Math.min(Math.max(t.page || 1, 1), meta.num_pages));
              }} style={{ padding: 6, borderRadius: 6, border: "1px solid var(--line)", marginLeft: 10 }}>
                <option value="">📑 跳转到章节…</option>
                {meta.toc.map((t, i) => (
                  <option key={i} value={i}>{t.level}. {t.title}（p{t.page}）</option>
                ))}
              </select>
            )}
            <span className="chip" style={{ fontSize: 12, marginLeft: 10 }}>↕ 上下滚动连续阅读</span>
          </div>
        </div>

        {evidence && hit && (
          <div className="row" style={{ marginTop: 12, alignItems: "center" }}>
            <span className="chip" style={{ background: "#fef3c7", borderColor: "#f59e0b" }}>
              {hitExact ? "🎯 已定位到引用证据" : "🎯 已定位到引用所在页（该页段落已重排，无法精确到行）"} · 第 {page + 1} 页
            </span>
            <button className="btn ghost" style={{ marginLeft: 10 }}
              onClick={() => { setEvidence(false); setHit(null); }}>清除高亮</button>
          </div>
        )}
        {!evidence && hit && (
          <div className="row" style={{ marginTop: 12, alignItems: "center" }}>
            <span className="chip" style={{ background: "#fef3c7", borderColor: "#f59e0b" }}>
              🔍 已定位到匹配段落 · 第 {page + 1} 页
            </span>
            <button className="btn ghost" style={{ marginLeft: 10 }}
              onClick={() => setHit(null)}>清除高亮</button>
          </div>
        )}

        <div style={{ display: bilingual ? "grid" : "block",
          gridTemplateColumns: bilingual ? "minmax(0,1fr) minmax(0,1fr)" : undefined,
          gap: 12, marginTop: 16 }}>
        <div ref={scrollerRef} onScroll={() => { onDocScroll(); syncScroll("left"); }}
          onMouseUp={handleSelect}
          className="reader-scroll"
          style={{ position: "relative", maxHeight: "calc(100vh - 220px)", minHeight: 420,
            overflowY: "auto", background: "#fff",
            border: "1px solid var(--line)", borderRadius: 10, padding: "8px 16px",
            lineHeight: 1.7, scrollBehavior: "smooth" }}>
          {pageGroups.map(({ page: pg, items }) => (
            <div key={pg} id={`page-${pg}`}
              ref={(node) => {
                if (node) pageEls.current.set(pg, node);
                else pageEls.current.delete(pg);
              }}>
              <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#fff",
                padding: "6px 0 2px", marginBottom: 6, fontSize: 12,
                color: "var(--ink-3)", borderBottom: "1px solid var(--line)" }}>
                第 {pg} 页
              </div>
              {items.map(c => (
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
                  {c.kind === "table" && <TableBlock text={c.text} />}
                  {(c.kind === "figure" || (c.kind === "caption" && FIG_CAP_RE.test(c.text))) && (
                    <figure style={{ margin: "10px 0" }}>
                      <img
                        src={`${apiBase()}/api/papers/${pid}/figures/${figureNo.get(c.id)}/image`}
                        alt={c.text || "figure"}
                        loading="lazy"
                        style={{ maxWidth: "100%", border: "1px solid var(--line)", borderRadius: 6 }}
                        onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = "0.25"; }}
                      />
                      {c.text && c.text !== "[Figure]" && (
                        <figcaption style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4 }}>
                          图 {figureNo.get(c.id)}：{c.text}
                        </figcaption>
                      )}
                    </figure>
                  )}
                  {c.kind === "formula" && <div style={{ fontFamily: "ui-monospace, monospace",
                    fontSize: 13, background: "#f8fafc", padding: 6, borderRadius: 6 }}>{c.text}</div>}
                  {c.kind === "caption" && !FIG_CAP_RE.test(c.text) && (
                    <div style={{ fontStyle: "italic", fontSize: 13, color: "var(--ink-3)" }}>
                      {c.text}</div>
                  )}
                  {(c.kind === "paragraph" || c.kind === "ocr") && (
                    <p style={{ margin: 0 }}
                      onMouseEnter={(e) => hoverTranslate(c, e)}
                      onMouseLeave={() => setHoverTip(null)}>
                      {renderCitations(c.text)}
                    </p>
                  )}
                  {lang === "zh" && trans[c.id] && c.kind !== "heading" && c.kind !== "caption" && (
                    <div style={{ marginTop: 4, color: "var(--ink-3)", fontSize: 13 }}>
                      🇨🇳 {trans[c.id]}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
          {docChunks.length === 0 && (
            <Empty compact icon="🔍" title="本文没有可解析文本"
              desc="可能是扫描件或纯图片 PDF；可尝试重新上传，系统会自动启用 OCR。"
              />
          )}
        </div>

          {bilingual && (
            <div ref={rightScrollerRef} onScroll={() => syncScroll("right")}
              style={{ maxHeight: "calc(100vh - 220px)", minHeight: 420, overflowY: "auto",
                background: "#fcfcff", border: "1px solid var(--line)", borderRadius: 10,
                padding: "8px 16px", lineHeight: 1.8 }}>
              {pageGroups.map(({ page: pg, items }) => (
                <div key={pg}>
                  <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#fcfcff",
                    padding: "6px 0 2px", marginBottom: 6, fontSize: 12,
                    color: "var(--ink-3)", borderBottom: "1px solid var(--line)" }}>
                    第 {pg} 页 · 中文译文
                  </div>
                  {items.map(c => (
                    <div key={c.id} style={{ marginBottom: 10, paddingBottom: 10,
                      borderBottom: "1px dashed var(--line)",
                      background: hit === c.id ? "#fef3c7" : undefined, borderRadius: 6 }}>
                      {c.kind === "heading" ? (
                        <h3 style={{ fontSize: 15, margin: "6px 0 4px" }}>{c.text}</h3>
                      ) : (c.kind === "table" || c.kind === "formula") ? (
                        <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
                          （表格 / 公式保留原文）
                        </div>
                      ) : (
                        <p style={{ margin: 0, fontSize: 14 }}>
                          {trans[c.id] || (lang === "zh" ? "译文加载中…" : "已关闭中文译文")}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {refsList.length > 0 && (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>📚 参考文献（{refsList.length} 条）</h3>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              点击正文 [n] 跳到此处；点「↩ 正文」回到首次引用位置
            </span>
          </div>
          <div style={{ marginTop: 10, maxHeight: 360, overflowY: "auto",
            display: "flex", flexDirection: "column", gap: 6 }}>
            {refsList.map((r, i) => (
              <div key={r.id ?? i} id={`ref-${i + 1}`}
                style={{ padding: "7px 10px", border: "1px solid var(--line)",
                  borderRadius: 8, fontSize: 12.5, lineHeight: 1.6,
                  background: jumpRef === i + 1 ? "#fef3c7" : "#fff" }}>
                <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
                  <span style={{ flex: "0 0 auto", color: "var(--brand)", fontWeight: 600 }}>
                    [{i + 1}]
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    {r.raw || r.title || "（原始条目缺失）"}
                  </span>
                  <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 11 }}
                    onClick={() => jumpToCitation(i + 1)}>↩ 正文</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {hoverTip && (
        <div style={{ position: "fixed", left: hoverTip.x, top: hoverTip.y, zIndex: 70,
          width: 350, maxWidth: "calc(100vw - 24px)", background: "#fff",
          border: "1px solid var(--line)", borderRadius: 10, padding: 10,
          boxShadow: "0 12px 32px rgba(15,23,42,.18)",
          fontSize: 12.5, lineHeight: 1.6, pointerEvents: "none" }}>
          <div style={{ color: "var(--ink-3)", fontSize: 11, marginBottom: 4 }}>悬停翻译</div>
          {hoverTip.text}
        </div>
      )}

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
              {pickResult.mode !== "llm" && (
                <div style={{ marginTop: 6, fontSize: 11, color: "var(--ink-3)" }}>
                  当前按原文展示，切换在线模型可获得译文
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 把解析出的 Markdown 管表格渲染为原生 HTML 表格；非表格文本退回等宽显示。 */
function TableBlock({ text }: { text: string }) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  const rows = lines
    .filter(l => !/^\|?[\s:\-|]+\|?$/.test(l))
    .map(l => l.replace(/^\||\|$/g, "").split("|").map(x => x.trim()));
  if (rows.length < 2 || rows.some(r => r.length < 2)) {
    return (
      <div style={{ background: "#f8fafc", padding: 8, borderRadius: 6,
        fontFamily: "ui-monospace, monospace", fontSize: 12, whiteSpace: "pre-wrap" }}>
        {text}
      </div>
    );
  }
  const [head, ...body] = rows;
  return (
    <div style={{ overflowX: "auto", margin: "6px 0" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
        <thead>
          <tr>{head.map((h, i) => (
            <th key={i} style={{ border: "1px solid var(--line)", padding: "5px 7px",
              background: "#f1f5f9", textAlign: "left" }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>{r.map((cell, ci) => (
              <td key={ci} style={{ border: "1px solid var(--line)", padding: "5px 7px" }}>
                {cell}
              </td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
