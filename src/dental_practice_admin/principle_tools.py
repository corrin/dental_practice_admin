"""Agent tools compiled from the released Principle interface."""

from __future__ import annotations

import json
from typing import Any

import httpx
from agents import FunctionTool
from agents.tool_context import ToolContext
from jsonschema import Draft202012Validator

from dental_practice_admin.config import Settings
from dental_practice_admin.principle import (
    CATALOGUE,
    Call,
    CallError,
    PrincipleClient,
    PrincipleError,
)

MAX_RESULT_CHARACTERS = 24000


def api_tools(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> list[FunctionTool]:
    """Bind generated schemas to one executor with trusted practice configuration."""

    def tool(call: Call) -> FunctionTool:
        async def invoke(_context: ToolContext[Any], arguments: str) -> str:
            try:
                values = json.loads(arguments)
            except ValueError:
                return "Invalid tool arguments: expected JSON."
            if not Draft202012Validator(call.definition["tool_schema"]).is_valid(values):
                return "Invalid tool arguments: use the declared fields and types."
            values = {k: v for k, v in values.items() if v is not None}
            values["practiceId"] = settings.practice_id
            path = {
                p["name"]: values[p["name"]]
                for p in call.definition["parameters"]
                if p["in"] == "path"
            }
            query = {k: v for k, v in values.items() if k in call.query}
            try:
                async with PrincipleClient(settings, transport=transport) as client:
                    envelope = await client.get(call.name, path_params=path, query=query)
            except CallError:
                return "Invalid arguments for this operation."
            except PrincipleError:
                return "Principle could not supply a compatible result. Do not infer an answer."
            except httpx.HTTPError:
                return "Principle is unavailable. No result was obtained."
            complete = call.definition["complete_listing"]
            output = {
                "operation": call.name,
                "coverage": "complete" if complete else "partial",
                "note": call.definition["description"],
                "result": envelope,
            }
            result = json.dumps(output)
            if len(result) > MAX_RESULT_CHARACTERS:
                return json.dumps(
                    {
                        "operation": call.name,
                        "coverage": "partial",
                        "note": "Result exceeds the response limit; narrow the query.",
                    }
                )
            return result

        return FunctionTool(
            name=call.name,
            description=call.definition["description"],
            params_json_schema=call.definition["tool_schema"],
            on_invoke_tool=invoke,
        )

    return [tool(call) for call in CATALOGUE if call.definition["exposed"]]
