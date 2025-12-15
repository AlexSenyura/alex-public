import json
from typing import List

from fastapi import HTTPException
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.topics import TopicIdea, TopicRequest


TOPIC_SCHEMA = {
    "name": "topics",
    "schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "title_variants": {"type": "array", "items": {"type": "string"}},
                        "hook": {"type": "string"},
                        "cold_open_15s": {"type": "string"},
                        "outline": {"type": "array", "items": {"type": "string"}},
                        "thumbnail_prompt": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "cta": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "title_variants",
                        "hook",
                        "cold_open_15s",
                        "outline",
                        "thumbnail_prompt",
                        "tags",
                        "cta",
                    ],
                },
                "minItems": 1,
            }
        },
        "required": ["topics"],
    },
}


RANK_SCHEMA = {
    "name": "ranked_topics",
    "schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "title_variants": {"type": "array", "items": {"type": "string"}},
                        "hook": {"type": "string"},
                        "cold_open_15s": {"type": "string"},
                        "outline": {"type": "array", "items": {"type": "string"}},
                        "thumbnail_prompt": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "cta": {"type": "string"},
                        "score": {"type": "integer"},
                        "why_it_works": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "title_variants",
                        "hook",
                        "cold_open_15s",
                        "outline",
                        "thumbnail_prompt",
                        "tags",
                        "cta",
                        "score",
                        "why_it_works",
                    ],
                },
            }
        },
        "required": ["topics"],
    },
}


async def _call_ai(client: AsyncOpenAI, model: str, prompt: str, schema: dict) -> str:
    response = await client.responses.create(
        model=model,
        input=prompt,
        response_format={"type": "json_schema", "json_schema": schema},
    )
    if getattr(response, "output", None):
        first = response.output[0]
        content = first.content[0].text  # type: ignore[index]
        return content if isinstance(content, str) else getattr(content, "value", "")
    return getattr(response, "output_text", "")


def _repair_json(raw: str) -> str:
    # naive repair to close brackets
    trimmed = raw.strip()
    if trimmed.endswith(("]", "}")):
        return trimmed
    if "[" in trimmed and not trimmed.rstrip().endswith("]"):
        trimmed += "]" if "[" in trimmed and "]" not in trimmed else ""
    if "{" in trimmed and not trimmed.rstrip().endswith("}"):
        trimmed += "}"
    return trimmed


def _parse_topics(raw: str, schema_model):
    last_error = None
    text = raw
    for _ in range(3):
        try:
            data = json.loads(text)
            return schema_model(**data)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            text = _repair_json(text)
    raise last_error  # type: ignore[misc]


async def generate_topics(req: TopicRequest) -> List[TopicIdea]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY відсутній")

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    draft_prompt = (
        "You are an expert YouTube strategist. Generate {n} diverse video concepts "
        "for the keyword '{kw}'. Each response must be in strict JSON. "
        "Use concise, high-converting Ukrainian-friendly hooks but keep content in English. "
        "Seed: {seed}."
    ).format(n=req.bulk_n, kw=req.keyword, seed=req.seed or "none")

    attempts = 0
    bulk_n = req.bulk_n
    drafts = None
    while attempts < 3:
        try:
            raw = await _call_ai(client, "gpt-5-mini", draft_prompt, TOPIC_SCHEMA)
            parsed = _parse_topics(raw, lambda **d: d)
            drafts = parsed.get("topics", [])  # type: ignore[assignment]
            if isinstance(drafts, dict):
                drafts = drafts.get("topics", [])
            if not isinstance(drafts, list):
                raise ValueError("Невірний формат чернеток")
            break
        except Exception:
            attempts += 1
            if attempts == 2 and bulk_n > req.final_n:
                bulk_n = max(req.final_n + 2, int(bulk_n * 0.8))
                draft_prompt = draft_prompt.replace(str(req.bulk_n), str(bulk_n))
            if attempts >= 3:
                raise HTTPException(status_code=500, detail="Не вдалося згенерувати теми")

    if drafts is None:
        raise HTTPException(status_code=500, detail="Чернетки не отримано")

    trimmed_prompt = (
        "From the draft topics, choose the best {final_n} that will perform on YouTube. "
        "Return JSON with reasons and score 0-100."
    ).format(final_n=req.final_n)

    try:
        raw_ranked = await _call_ai(client, "gpt-5.2", trimmed_prompt + json.dumps({"topics": drafts}), RANK_SCHEMA)
        ranked = _parse_topics(raw_ranked, lambda **d: d).get("topics", [])  # type: ignore[attr-defined]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Помилка ранжування: {exc}")

    ideas: List[TopicIdea] = []
    for item in ranked:
        try:
            ideas.append(TopicIdea.model_validate(item))
        except ValidationError:
            continue
    return ideas
