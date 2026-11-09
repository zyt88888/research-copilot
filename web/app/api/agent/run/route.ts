// 直连后端的 SSE 流式转发。
// 说明：next.config.js 的 rewrites 会把 text/event-stream 整体缓冲，
// 浏览器端只能等流结束后一次性拿到，导致 Agent 页长时间停在「正在规划」。
// 这里用 Route Handler 透传 upstream.body，保证逐事件到达前端。
export const dynamic = "force-dynamic";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND || "http://localhost:8000";

export async function POST(req: Request) {
  const body = await req.text();
  const upstream = await fetch(`${BACKEND}/api/agent/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    cache: "no-store",
  });

  if (!upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
