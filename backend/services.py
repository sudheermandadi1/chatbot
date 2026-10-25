
import json
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy.orm import Session

from backend.tool_registry import get_available_tools, execute_tools

load_dotenv()

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5-nano")

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is not configured.")

model = OpenAI(api_key=api_key)

DATASET_TOOL_NAMES = {
    "query_dataset",
    "preview_dataset_mutation",
    "confirm_dataset_mutation",
    "cancel_dataset_mutation",
}


def generate_response(
    history: list[dict],
    active_dataset_id: str | None = None,
    active_dataset_table: str | None = None,
    db: Session | None = None,
) -> dict:
    tools, registry = get_available_tools()

    dataset_available = (
        bool(active_dataset_id)
        and bool(active_dataset_table)
        and db is not None
    )

    if not dataset_available:
        tools = [
            tool for tool in tools
            if tool["name"] not in DATASET_TOOL_NAMES
        ]

    # Confirmation is never performed by the automatic model tool loop.
    tools = [
        tool for tool in tools
        if tool["name"] != "confirm_dataset_mutation"
    ]

    input_items = [
        {
            "role": message["role"],
            "content": message["content"],
        }
        for message in history
        if message.get("role") in {
            "system",
            "user",
            "assistant",
        }
    ]

    instructions = """
You are a helpful AI assistant. Answer clearly and accurately using Markdown.

General tool rules:
- Use convert_currency for exchange-rate requests.
- Explain that reference rates may differ from bank, card, or cash rates.
- Use weather tools for current weather questions.
- Never invent live values or claim a tool succeeded if it failed.
- If a service is unavailable, explain that honestly.

Dataset rules:
- Use dataset tools for questions about the active uploaded CSV.
- If no dataset is available, ask the user to upload a CSV.
- Use only the exact active dataset table specified below.
- Never invent table names, columns, metadata, or query results.
- Use SELECT for filtering, sorting, grouping, aggregation, and analysis.
- Follow SQL validation rules and row limits.
- UPDATE and DELETE must be previewed before any changes.
- Never claim that a previewed mutation has been applied.
- Never confirm a mutation yourself. The application handles confirmation.
- Never claim a mutation succeeded without a successful result.
- Present query results as tables or concise summaries when useful.
- Do not expose database credentials or connection strings.
"""

    if dataset_available:
        instructions += f"""

ACTIVE DATASET
Exact allowed table name: {active_dataset_table}

Use this exact name in every SQL query.
Example: SELECT * FROM {active_dataset_table} LIMIT 5;
Never use the dataset UUID as a table name.
"""

    pending_mutation = None

    for round_number in range(MAX_TOOL_ROUNDS + 1):
        response = model.responses.create(
            model=MODEL_NAME,
            instructions=instructions,
            input=input_items,
            tools=tools,
            tool_choice="auto",
        )

        calls = [
            item
            for item in response.output
            if item.type == "function_call"
        ]

        if not calls:
            return {
                "response": (
                    response.output_text
                    or "I couldn't generate a response. Please try again."
                ),
                "pending_mutation": pending_mutation,
            }

        if round_number == MAX_TOOL_ROUNDS:
            logger.warning("Maximum tool-call rounds exceeded.")
            return {
                "response": (
                    "I couldn't complete the request within the allowed "
                    "number of tool-call rounds. Please try a simpler request."
                ),
                "pending_mutation": pending_mutation,
            }

        # Preserve the model response, including its function-call items.
        input_items.extend(response.output)

        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")

                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object.")

                if call.name == "confirm_dataset_mutation":
                    result = {
                        "success": False,
                        "error": (
                            "Confirmation must be performed through "
                            "the application's explicit confirmation action."
                        ),
                    }
                else:
                    result = execute_tools(
                        name=call.name,
                        arguments=arguments,
                        registry=registry,
                        active_dataset_id=active_dataset_id,
                        active_dataset_table=active_dataset_table,
                        allow_confirm=False,
                        db=db,
                    )

                if (
                    call.name == "preview_dataset_mutation"
                    and isinstance(result, dict)
                    and result.get("success")
                    and result.get("requires_confirmation")
                    and result.get("pending_id")
                ):
                    pending_mutation = {
                        "pending_id": result["pending_id"],
                        "dataset_id": active_dataset_id,
                        "operation": result["operation"],
                        "matching_rows": result["matching_rows"],
                        "sample_rows": result.get("sample_rows", []),
                    }

                output = json.dumps(result, default=str)

            except Exception:
                logger.exception(
                    "Tool execution failed: %s",
                    call.name,
                )
                output = json.dumps({
                    "success": False,
                    "error": (
                        "Tool execution failed. No successful result "
                        "is available. Explain the failure honestly."
                    ),
                })

            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": output,
            })

    return {
        "response": "I couldn't complete the request. Please try again.",
        "pending_mutation": pending_mutation,
    }
