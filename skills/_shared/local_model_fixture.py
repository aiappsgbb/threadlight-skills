"""Deterministic upstream HTTP responses; the MAF/OpenAI clients remain native."""
import json


def tool_responses(name, args):
    from agent_framework import ChatResponse, Content, Message
    return [
        ChatResponse(messages=[Message("assistant", contents=[
            Content.from_function_call(call_id="call-1", name=name, arguments=json.dumps(args)),
        ])], finish_reason="tool_calls"),
        ChatResponse(messages=[Message("assistant", ["finished"])], finish_reason="stop"),
    ]


def native_model_client(responses):
    import httpx
    from openai import AsyncOpenAI
    from agent_framework.openai import OpenAIChatClient
    requests = []

    def transport(request):
        body = json.loads(request.content)
        requests.append((body["input"], body))
        response = responses.pop(0)
        output = []
        for message in response.messages:
            for content in message.contents:
                if content.type == "function_call":
                    output.append({"id": "fc_" + content.call_id, "type": "function_call",
                                   "call_id": content.call_id, "name": content.name,
                                   "arguments": content.arguments, "status": "completed"})
                elif content.type == "text":
                    output.append({"id": "msg_" + str(len(output)), "type": "message",
                                   "role": "assistant", "status": "completed", "content": [{
                                       "type": "output_text", "text": content.text, "annotations": []}]})
        return httpx.Response(200, json={"id": "resp_local", "object": "response", "created_at": 0,
                                        "status": "completed", "model": "local", "output": output})

    http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    sdk = AsyncOpenAI(api_key="local-test-only", http_client=http, max_retries=0)
    client = OpenAIChatClient(model="local", async_client=sdk)
    client.requests, client.responses = requests, responses
    return client
