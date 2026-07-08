"""Azure Functions v2 HTTP webhook receiver — Shape #5 (escape hatch).

Flex Consumption, Python v2 programming model (``function_app.py`` + decorators,
**no** legacy ``function.json``). Managed identity only. The idempotency +
agent-invoke + dead-letter logic lives in the unit-tested ``receiver_core``.
"""
import json

import azure.functions as func

import receiver_core as core

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="trigger", methods=["POST"])
async def trigger(req: func.HttpRequest) -> func.HttpResponse:
    request_id = req.headers.get("X-Request-Id")
    if not request_id:
        return func.HttpResponse("X-Request-Id header required", status_code=400)
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("body must be JSON", status_code=400)

    payload = dict(body) if isinstance(body, dict) else {"body": body}
    payload["request_id"] = request_id

    cred, store, cosmos = core.open_store()
    try:
        result = await core.handle(
            payload,
            store=store,
            invoke=core.invoke_agent,
            dead_letter=core.dead_letter_to_queue,
        )
    finally:
        await cosmos.close()
        await cred.close()

    if result["status"] == "dead_lettered":
        # Persisted to the poison queue; ask the sender to retry (dedup makes
        # the eventual success exactly-once).
        return func.HttpResponse("processing failed, retry", status_code=502)
    return func.HttpResponse(
        json.dumps({"status": result["status"]}),
        status_code=200,
        mimetype="application/json",
    )
