"""Claude API sarmalayıcı: model katmanları, prompt cache, JSON çıktı, maliyet takibi."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

# İki katman: akıllı (yaratıcı/araştırma işleri) ve hızlı (denetim/analiz).
MODEL_SMART = os.getenv("MODEL_SMART", "claude-sonnet-5-5")
MODEL_FAST = os.getenv("MODEL_FAST", "claude-haiku-5-5")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}


def _extract_json(text: str):
    """Model çıktısındaki ilk JSON nesnesini/dizisini ayıklar."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"JSON bulunamadı:\n{text[:500]}")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return json.loads(text[start : end + 1])


def image_block(path: str | Path, max_side: int = 768) -> dict:
    """Görseli küçültüp vision bloğuna çevirir (token tasarrufu)."""
    from io import BytesIO

    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg",
                   "data": base64.b64encode(buf.getvalue()).decode()},
    }


class LLM:
    def __init__(self, brand_text: str, store=None):
        import anthropic

        self.client = anthropic.Anthropic()
        self.brand_text = brand_text
        self.store = store

    def _system(self, role_prompt: str) -> list:
        # Marka rehberi her ajanda aynı → cache'lenir, tekrar okumak ~%90 ucuz.
        return [
            {"type": "text", "text": "MARKA REHBERİ (YAML):\n" + self.brand_text,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": role_prompt},
        ]

    def ask_json(self, agent: str, role_prompt: str, content, *, model: str = MODEL_SMART,
                 tools: list | None = None, max_tokens: int = 4000):
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        messages = [{"role": "user", "content": content}]
        kwargs = dict(model=model, max_tokens=max_tokens, system=self._system(role_prompt),
                      messages=messages)
        if tools:
            kwargs["tools"] = tools

        texts = []
        for _ in range(6):  # server tool'lar 'pause_turn' ile bölünebilir
            resp = self.client.messages.create(**kwargs)
            self._log(agent, model, resp)
            texts += [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            if resp.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": resp.content})
        return _extract_json("\n".join(texts))

    def _log(self, agent: str, model: str, resp) -> None:
        if not self.store:
            return
        u = resp.usage
        stu = getattr(u, "server_tool_use", None)
        self.store.log_usage(
            agent=agent, model=model,
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
            searches=(getattr(stu, "web_search_requests", 0) or 0) if stu else 0,
        )
