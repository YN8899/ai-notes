"""Single-attempt, bounded, private Bailian compatible embedding adapter."""
import json
import math
import time
import httpx
from app.shared.config import settings
from app.shared.errors import AppError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def create_client():
    return httpx.Client(timeout=httpx.Timeout(30, connect=5), trust_env=False, follow_redirects=False)


def embed(texts, config):
    if not 1 <= len(texts) <= 10 or any(not isinstance(t, str) or not 1 <= len(t) <= 800 for t in texts):
        raise AppError(422, "embedding_input_invalid", "向量输入超出单次限制")
    cfg = settings()
    usage = {}
    try:
        deadline = time.monotonic() + 40
        with create_client() as client:
            with client.stream("POST", f"https://{config['workspace']}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings",
                               headers={"Authorization": "Bearer " + cfg.embedding_api_key.get_secret_value(), "Accept-Encoding": "identity"},
                               json={"model": config["model"], "input": texts, "dimensions": config["dimensions"], "encoding_format": "float"}) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("status")
                body = bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body) > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                        raise ValueError("size")
        result = json.loads(body)
        raw_usage = result.get("usage", {})
        if isinstance(raw_usage, dict):
            usage = {k: v for k, v in raw_usage.items() if k in {"prompt_tokens", "total_tokens"}
                     and type(v) is int and 0 <= v <= 10**9}
        data = result["data"]
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("count")
        vectors = [None] * len(texts)
        for item in data:
            index, vector = item["index"], item["embedding"]
            if (type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None
                    or not isinstance(vector, list) or len(vector) != config["dimensions"]
                    or any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 1e20 for v in vector)
                    or not any(v != 0 for v in vector)):
                raise ValueError("vector")
            # Normalize in double precision before float32 storage, including tiny
            # finite provider values that would otherwise underflow to an all-zero vector.
            scale = max(abs(v) for v in vector)
            scaled = [v / scale for v in vector]
            norm = math.sqrt(sum(v * v for v in scaled))
            vectors[index] = [v / norm for v in scaled]
        return vectors, usage
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        error = AppError(503, "embedding_response_invalid", "向量服务调用失败或响应无效，请手动发起新请求")
        error.usage = usage
        raise error from None
