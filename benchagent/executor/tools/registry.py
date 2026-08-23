"""Tool registry.

Two families of tools, mirroring the paper (Sec. A.3):

1. LLM-based tools      -- content synthesis driven by the LLM
   (context construction, question generation, distractor generation, ...)
2. Pure (non-LLM) tools -- deterministic, parameterized operators:
   - synthesis tools:      image resizing, noise injection, audio mixing, TTS
   - programmatic tools:   structured field patching, metadata editing,
                           content decomposition, file conversion

Each tool has a name, a JSON-schema-ish description of its parameters (used by the
planner agents when constructing transformation plans), and a callable that maps
(current sample fields, params) -> new fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ... import prompts
from ...llm import LLMClient
from ...schemas import Subtask

ToolFn = Callable[[dict[str, Any], dict[str, Any], LLMClient, Subtask], dict[str, Any]]


@dataclass
class Tool:
    name: str
    description: str
    params_schema: dict[str, Any]
    is_llm: bool
    fn: ToolFn


# ---------------------------------------------------------------------------
# LLM-based tools
# ---------------------------------------------------------------------------
def _llm_context_construction(fields, params, llm, subtask):
    out = llm.chat_json(
        prompts.LLM_TOOL_CONTEXT_CONSTRUCTION,
        prompts.llm_tool_user(subtask.model_dump(), fields),
    )
    return {"context": out.get("context", "")}


def _llm_question_generation(fields, params, llm, subtask):
    out = llm.chat_json(
        prompts.LLM_TOOL_QUESTION_GENERATION,
        prompts.llm_tool_user(subtask.model_dump(), fields),
    )
    return {"question": out.get("question", ""), "answer": out.get("answer", "")}


def _llm_distractor_generation(fields, params, llm, subtask):
    out = llm.chat_json(
        prompts.LLM_TOOL_DISTRACTOR_GENERATION,
        prompts.llm_tool_user(subtask.model_dump(), fields),
    )
    return {"distractors": out.get("distractors", [])}


def _llm_dialogue_synthesis(fields, params, llm, subtask):
    """Synthesize a multi-speaker dialogue from source material (for dialogue tasks)."""
    sys = (
        "You are a data transformation tool. Synthesize a realistic multi-speaker "
        "dialogue (2-4 speakers) that faithfully reflects the source facts. "
        "Output STRICT JSON: {\"dialogue\": [{\"speaker\": \"...\", \"utterance\": \"...\"}]}"
    )
    out = llm.chat_json(sys, prompts.llm_tool_user(subtask.model_dump(), fields))
    return {"dialogue": out.get("dialogue", [])}


def _llm_reasoning_transform(fields, params, llm, subtask):
    """Rewrite source material into a step-by-step reasoning problem
    (e.g. faulty-step localization in a proof)."""
    sys = (
        "You are a data transformation tool. Transform the source material into a "
        "step-by-step reasoning artifact (e.g. a numbered proof or derivation) that "
        "contains exactly one subtle faulty step. Keep every step faithful to the source "
        "facts except the single planted flaw. "
        "Output STRICT JSON: {\"reasoning_steps\": [\"Step 1 ...\", ...], "
        "\"faulty_step_index\": 0, \"explanation\": \"...\"}"
    )
    out = llm.chat_json(sys, prompts.llm_tool_user(subtask.model_dump(), fields))
    return {
        "reasoning_steps": out.get("reasoning_steps", []),
        "faulty_step_index": out.get("faulty_step_index", 0),
        "flaw_explanation": out.get("explanation", ""),
    }


# ---------------------------------------------------------------------------
# Pure tools (deterministic)
# ---------------------------------------------------------------------------
def _pure_field_patch(fields, params, llm, subtask):
    """Structured field patching: set/rename/move/delete fields."""
    new = dict(fields)
    for k, v in (params.get("set") or {}).items():
        new[k] = v
    for old_k, new_k in (params.get("rename") or {}).items():
        if old_k in new:
            new[new_k] = new.pop(old_k)
    for k in params.get("delete") or []:
        new.pop(k, None)
    return new


def _pure_metadata_edit(fields, params, llm, subtask):
    """Metadata editing: add/update structured attributes like modality tags."""
    new = dict(fields)
    meta = dict(new.get("_meta", {}))
    meta.update(params.get("set") or {})
    new["_meta"] = meta
    return new


def _pure_content_decompose(fields, params, llm, subtask):
    """Content decomposition: split a field into smaller units (list -> items, or split by delimiter)."""
    new = dict(fields)
    src = params.get("source_field")
    if src and src in new:
        value = new[src]
        if isinstance(value, list):
            units = value
        elif isinstance(value, str):
            units = [u.strip() for u in value.split(params.get("delimiter", "\n")) if u.strip()]
        else:
            units = [value]
        new[params.get("target_field", src + "_units")] = units
    return new


def _pure_image_resize(fields, params, llm, subtask):
    """Image resizing via Pillow; mutates the media file in place (or writes new file)."""
    from PIL import Image

    new = dict(fields)
    media_field = params.get("media_field")
    path = new.get(media_field or "image_path")
    if not path:
        raise ValueError("image_resize: no image path found in sample fields")
    width = params.get("width")
    height = params.get("height")
    img = Image.open(path)
    if width and height:
        img = img.resize((int(width), int(height)))
    elif width:
        ratio = int(width) / img.width
        img = img.resize((int(width), int(img.height * ratio)))
    elif height:
        ratio = int(height) / img.height
        img = img.resize((int(img.width * ratio), int(height)))
    else:
        raise ValueError("image_resize: specify width and/or height")
    out_path = params.get("out_path") or path
    img.save(out_path)
    new[media_field or "image_path"] = out_path
    return new


def _pure_noise_injection(fields, params, llm, subtask):
    """Add Gaussian noise to an image (deterministic, seeded)."""
    import numpy as np
    from PIL import Image

    new = dict(fields)
    media_field = params.get("media_field")
    path = new.get(media_field or "image_path")
    if not path:
        raise ValueError("noise_injection: no image path found")
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    rng = np.random.default_rng(params.get("seed", 0))
    noise = rng.normal(0, params.get("intensity", 0.02) * 255, img.shape)
    img = np.clip(img + noise, 0, 255).astype(np.uint8)
    out_path = params.get("out_path") or path
    Image.fromarray(img).save(out_path)
    new[media_field or "image_path"] = out_path
    return new


def _pure_file_convert(fields, params, llm, subtask):
    """File conversion (format-level, content-preserving) — currently JSON <-> JSONL."""
    new = dict(fields)
    # For MVP this tool is a no-op on the sample level; format conversion happens at
    # dataset export time. Keep it registered so plans can reference it.
    return new


def _pure_tts(fields, params, llm, subtask):
    """Text-to-speech synthesis tool (placeholder).

    A real implementation can plug in any TTS engine (e.g. Coqui XTTS v2 as in the
    paper). Requires the `audio` extra (pydub / soundfile).
    """
    raise NotImplementedError(
        "TTS tool requires a TTS backend. Install `benchagent[audio]` and implement "
        "_pure_tts in tools/registry.py, or use the LLM-only pipeline."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def build_registry() -> dict[str, Tool]:
    tools: list[Tool] = [
        Tool("context_construction",
             "Assemble raw fields into a coherent self-contained context (LLM)",
             {"input_fields": "list[str]"}, True, _llm_context_construction),
        Tool("question_generation",
             "Generate a question + correct answer from the context (LLM)",
             {"answer_type": "str"}, True, _llm_question_generation),
        Tool("distractor_generation",
             "Generate plausible incorrect options for a multiple-choice item (LLM)",
             {"num_distractors": "int"}, True, _llm_distractor_generation),
        Tool("dialogue_synthesis",
             "Synthesize a multi-speaker dialogue faithful to source facts (LLM)",
             {"num_speakers": "int"}, True, _llm_dialogue_synthesis),
        Tool("reasoning_transform",
             "Rewrite source material into a stepwise reasoning artifact with one planted flaw (LLM)",
             {"num_steps": "int"}, True, _llm_reasoning_transform),
        Tool("field_patch",
             "Structured field patching: set/rename/move/delete fields (pure)",
             {"set": "dict", "rename": "dict", "delete": "list"}, False, _pure_field_patch),
        Tool("metadata_edit",
             "Add/update structured metadata attributes (pure)",
             {"set": "dict"}, False, _pure_metadata_edit),
        Tool("content_decompose",
             "Split a field into smaller units (pure)",
             {"source_field": "str", "target_field": "str", "delimiter": "str"}, False, _pure_content_decompose),
        Tool("image_resize",
             "Resize an image to target dimensions (pure, Pillow)",
             {"media_field": "str", "width": "int", "height": "int", "out_path": "str"}, False, _pure_image_resize),
        Tool("noise_injection",
             "Add Gaussian noise to an image (pure, seeded)",
             {"media_field": "str", "intensity": "float", "seed": "int", "out_path": "str"}, False, _pure_noise_injection),
        Tool("file_convert",
             "Convert file formats preserving content (pure)",
             {"from": "str", "to": "str"}, False, _pure_file_convert),
        Tool("tts",
             "Text-to-speech synthesis (pure, backend-dependent)",
             {"voice": "str", "style": "str", "out_path": "str"}, False, _pure_tts),
    ]
    return {t.name: t for t in tools}


def tool_descriptions(registry: dict[str, Tool]) -> str:
    lines = []
    for t in registry.values():
        lines.append(f"- {t.name} ({'LLM' if t.is_llm else 'pure'}): {t.description}")
    return "\n".join(lines)
