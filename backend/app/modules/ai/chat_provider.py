"""Bounded Chat Completions SSE parser shared by all model call sites."""
import json
import re

from app.shared import outbound

URL = "https://api.deepseek.com/chat/completions"


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def decode_events(chunks):
    """Tool calls are emitted ONLY after a terminal frame and [DONE]."""
    pending, data_lines = b"", []
    finished, calls, done = None, {}, False
    def frame(raw):
        nonlocal finished
        data = strict_json(raw)
        if not isinstance(data, dict) or "error" in data:
            raise ValueError()
        event = {}
        choices = data.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError()
        if choices:
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise ValueError()
            delta = choice.get("delta", {})
            if not isinstance(delta, dict) or finished and delta:
                raise ValueError()
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str) or "\x00" in content:
                    raise ValueError()
                content.encode("utf-8")
                event["delta"] = content
            fragments = delta.get("tool_calls", [])
            if not isinstance(fragments, list) or len(fragments) > 6:
                raise ValueError()
            for part in fragments:
                if not isinstance(part, dict) or set(part) - {"index", "id", "type", "function"}:
                    raise ValueError()
                index = part.get("index")
                if type(index) is not int or not 0 <= index < 6:
                    raise ValueError()
                call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if "type" in part and part["type"] != "function":
                    raise ValueError()
                if "id" in part:
                    if not isinstance(part["id"], str) or len(call["id"] + part["id"]) > 128:
                        raise ValueError()
                    call["id"] += part["id"]
                function = part.get("function", {})
                if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
                    raise ValueError()
                for key, limit in (("name", 64), ("arguments", 4096)):
                    if key in function:
                        if not isinstance(function[key], str):
                            raise ValueError()
                        call["function"][key] += function[key]
                        if len(call["function"][key].encode("utf-8")) > limit:
                            raise ValueError()
            reason = choice.get("finish_reason")
            if reason is not None:
                if finished is not None:
                    raise ValueError()
                if reason not in {"stop", "length", "tool_calls"}:
                    raise ProviderFailure("provider_incomplete")
                finished = reason
        usage = data.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError()
            event["usage"] = {key: value for key, value in usage.items() if key in {
                "prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int and 0 <= value <= 10_000_000}
            if not event["usage"]:
                event.pop("usage")
        return event
    try:
        for chunk in chunks:
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if len(line) > 65536:
                    raise ValueError()
                if not line:
                    if not data_lines:
                        yield {}
                        continue
                    raw = b"\n".join(data_lines)
                    data_lines = []
                    if raw == b"[DONE]":
                        if finished is None:
                            raise ProviderFailure("provider_incomplete")
                        done = True
                        break
                    yield frame(raw)
                elif line.startswith(b"data:"):
                    data_lines.append(line[5:].lstrip(b" "))
                    if sum(map(len, data_lines)) > 65536:
                        raise ValueError()
                else:
                    yield {}
            if done:
                break
            if len(pending) > 65536:
                raise ValueError()
        if not done or finished is None:
            raise ProviderFailure("provider_incomplete")
        if calls:
            if finished != "tool_calls" or sorted(calls) != list(range(len(calls))):
                raise ValueError()
            output = list(calls.values())
            if len({call["id"] for call in output}) != len(output):
                raise ValueError()
            for call in output:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]):
                    raise ValueError()
                if not re.fullmatch(r"[a-z_]{1,64}", call["function"]["name"]):
                    raise ValueError()
                if not isinstance(strict_json(call["function"]["arguments"]), dict):
                    raise ValueError()
            yield {"tool_calls": output, "finish": finished}
        elif finished == "tool_calls":
            raise ValueError()
        else:
            yield {"finish": finished}
    except ProviderFailure:
        raise
    except (ValueError, UnicodeError, TypeError, AttributeError, KeyError, RecursionError):
        raise ProviderFailure("provider_invalid_response") from None


def stream_chat(secret, model, messages, *, max_tokens=4096, endpoint=URL, provider="deepseek", tools=None, before_send=None):
    payload = {"model": model, "messages": messages, "stream": True,
               "stream_options": {"include_usage": True}, "max_tokens": max_tokens}
    if provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
    wire = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    gate_errors = []
    def gate():
        try:
            return before_send() if before_send is not None else True
        except Exception as error:
            gate_errors.append(getattr(error, "code", "run_cancelled"))
            return False
    stream = outbound.stream(endpoint, data=wire, headers={"Authorization": "Bearer " + secret,
        "Content-Type": "application/json", "Accept": "text/event-stream"}, before_send=gate)
    def chunks():
        for status, content in stream:
            if status != 200:
                code = {401: "credential_rejected", 403: "credential_rejected", 402: "provider_balance",
                        429: "provider_rate_limited"}.get(status, "provider_unavailable")
                raise ProviderFailure(code)
            yield content
    try:
        yield from decode_events(chunks())
    except outbound.SendCancelled:
        raise ProviderFailure(gate_errors[0] if gate_errors else "run_cancelled") from None
    except outbound.EndpointUnavailable as error:
        raise ProviderFailure({"timeout": "provider_timeout", "invalid_response": "provider_invalid_response"}.get(error.reason, "provider_unreachable")) from None
    except outbound.UnsafeDestination:
        raise ProviderFailure("provider_unreachable") from None
    finally:
        stream.close()
