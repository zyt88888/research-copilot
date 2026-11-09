"""OpenAI 兼容 LLM 客户端：支持流式、JSON 输出、失败重试与离线降级判断。"""
from __future__ import annotations

import json
import re
import time
from typing import Iterable, Iterator

import requests

from ..settings import (LLM_API_KEY, LLM_BASE_URL, LLM_MAX_TOKENS, LLM_MODEL,
                        LLM_TEMPERATURE, LLM_TIMEOUT)


class LLMError(RuntimeError):
    pass


def is_available() -> bool:
    return bool(LLM_API_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def chat(messages: list[dict], temperature: float | None = None,
         max_tokens: int | None = None, json_mode: bool = False,
         retries: int = 2) -> str:
    if not is_available():
        raise LLMError("未配置 LLM_API_KEY，当前为离线演示模式")
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": LLM_MAX_TOKENS if max_tokens is None else max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=_headers(), json=payload, timeout=LLM_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
            last_err = LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as exc:  # 网络抖动重试
            last_err = exc
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise LLMError(str(last_err))


def stream_chat(messages: list[dict], temperature: float | None = None,
                max_tokens: int | None = None) -> Iterator[str]:
    """逐 token 输出，供 SSE 使用。"""
    if not is_available():
        raise LLMError("未配置 LLM_API_KEY")
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": LLM_MAX_TOKENS if max_tokens is None else max_tokens,
        "stream": True,
    }
    with requests.post(f"{LLM_BASE_URL}/chat/completions", headers=_headers(),
                       json=payload, timeout=LLM_TIMEOUT, stream=True) as resp:
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


def parse_json(text: str) -> dict:
    """容错解析：去掉 ```json 包裹，提取第一个平衡的 JSON 对象。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < -1:
            raise
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        raise


def json_chat(system: str, user: str, schema_hint: str = "") -> dict:
    """要求模型输出 JSON；失败时抛错由上层降级，不做静默伪造。"""
    prompt = user + (f"\n\n严格输出 JSON，结构要求：{schema_hint}" if schema_hint else "")
    raw = chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": prompt}],
        json_mode=True,
        max_tokens=LLM_MAX_TOKENS,
    )
    return parse_json(raw)


def format_evidence(blocks: Iterable[dict]) -> str:
    lines = []
    for b in blocks:
        lines.append(
            f"[{b['sid']}] (paper={b['paper_title']} | page={b['page']} | "
            f"section={b.get('section') or '-'})\n{b['text']}"
        )
    return "\n\n".join(lines)
