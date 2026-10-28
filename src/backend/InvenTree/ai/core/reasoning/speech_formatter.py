"""One tool-free formatting call over an already validated diagnostic answer."""

import asyncio
import inspect
from types import SimpleNamespace


async def format_voice_body(
    request, *, timeout, cancel_event, wait_with_cancel, client_factory=None
):
    """Use the existing configured fast chat plane; never diagnose or read tools."""
    from ai.core.config import get_settings
    from ai.core.integrations.azure_openai_client import build_openai_client, client_auth_mode

    settings = get_settings()
    deployment = settings.azure_openai_fast_deployment
    if settings.ai_provider != "azure" or not deployment:
        raise ValueError("Azure speech formatter is unavailable")
    client = client_factory() if client_factory else build_openai_client(settings=settings)
    owned = client_factory is None
    client = client.with_options(timeout=max(timeout, 0.001), max_retries=0)
    options = {
        "model": deployment,
        "messages": [
            {"role": "system", "content": request["instructions"]},
            *request["input"],
        ],
        "max_tokens": request["max_output_tokens"],
        "response_format": {
            "type": "json_schema",
            "json_schema": request["text"]["format"],
        },
    }
    options["response_format"]["json_schema"] = {
        key: value
        for key, value in options["response_format"]["json_schema"].items()
        if key != "type"
    }

    async def invoke():
        create = client.chat.completions.create
        if inspect.iscoroutinefunction(create):
            return await create(**options)

        def invoke_sync():
            try:
                return create(**options)
            finally:
                if owned:
                    client.close()

        return await asyncio.to_thread(invoke_sync)

    response = await wait_with_cancel(invoke(), timeout=timeout, cancel_event=cancel_event)
    choice = response.choices[0]
    raw_usage = response.usage
    counts = (
        {
            "input_tokens": raw_usage.prompt_tokens,
            "output_tokens": raw_usage.completion_tokens,
            "total_tokens": raw_usage.total_tokens,
        }
        if raw_usage
        else {}
    )
    return SimpleNamespace(
        id=response.id,
        model=response.model,
        formatter_deployment=deployment,
        formatter_auth_mode=client_auth_mode(settings),
        output_text=choice.message.content or "",
        output=(),
        status="completed" if choice.finish_reason == "stop" else "incomplete",
        usage=SimpleNamespace(**counts, model_dump=lambda: counts),
    )
