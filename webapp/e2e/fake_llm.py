"""Deterministic OpenAI-compatible model for the browser tests (no network, no cost).

Answers every chat completion with a call of the request's output tool: a fixed page interpretation
(one wardrobe module) or, for the refine agent, the same module with its materials filled in.

Run: ``uv run uvicorn webapp.e2e.fake_llm:app --port 8765`` (started by playwright.config.ts).
"""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, Request

app = FastAPI()

BOX = {"x_min": 0.05, "y_min": 0.05, "x_max": 0.95, "y_max": 0.95, "label": ""}
MODULE = {
    "module_id": "SZ01",
    "title": "Szafa testowa",
    "group_id": "SZ01",
    "kind": "wardrobe",
    "view_bbox": BOX,
    "width_value": 1200,
    "height_value": 2200,
    "plinth_value": 100,
    "columns": [
        {"width_value": 600, "cells": [{"col": 1, "row": 1, "front": "door_left", "shelves": 3, "label": "półki"}]},
        {"width_value": 600, "cells": [{"col": 2, "row": 1, "front": "door_right", "label": "drążek"}]},
    ],
    "uncertain": [{"field": "cell_front", "column": 2, "row": 1, "reason": "Symbol otwierania słabo widoczny."}],
    "notes": [],
}
MATERIALS = {
    "materials": [{"key": "M1", "name": "Płyta biała 18", "kind": "laminated_board"}],
    "carcass_material_key": "M1",
    "front_material_key": "M1",
}


def _arguments(parameters: dict) -> dict:
    """Page interpretation for the PageDraft tool, the refined module for the ModuleDraft tool."""
    properties = parameters.get("properties", {})
    if "modules" in properties:
        return {"units": "mm", "modules": [MODULE]}
    return {**MODULE, **MATERIALS}


@app.get("/v1/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": "fake", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    # The output tool is the one whose arguments describe a page/module, whichever position it is in.
    tools = [item["function"] for item in body["tools"]]
    tool = next((t for t in tools if "modules" in t.get("parameters", {}).get("properties", {})), tools[0])
    return {
        "id": "chatcmpl-e2e",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "fake"),
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_e2e",
                            "type": "function",
                            "function": {"name": tool["name"], "arguments": json.dumps(_arguments(tool["parameters"]))},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
