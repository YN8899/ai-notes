"""Single-attempt, bounded, private Bailian compatible embedding adapter."""
import json
import math
import time
from app.shared import outbound
from app.shared.config import settings
from app.shared.errors import AppError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def embed(texts, config, *, before_send=None):
    if not 1 <= len(texts) <= 10 or any(not isinstance(t, str) or not 1 <= len(t) <= 800 for t in texts):
        raise AppError(422, "embedding_input_invalid", "向量输入超出单次限制")
    cfg = settings()
    usage = {}
    try:
        errors = []
        def gate():
            try:
                return before_send() if before_send is not None else True
            except AppError as error:
                errors.append(error)
                return False
        status, body = outbound.post_json(
            f"https://{config['workspace']}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings",
            data=json.dumps({"model": config["model"], "input": texts, "dimensions": config["dimensions"],
                "encoding_format": "float"}, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg.embedding_api_key.get_secret_value(),
                "Content-Type": "application/json", "Accept": "application/json"}, before_send=gate)
        if status != 200:
            raise ValueError("status")
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
    except outbound.SendCancelled:
        if errors:
            raise errors[0]
        raise AppError(409, "semantic_interrupted", "语义调用已取消，不会自动重试") from None
    except (outbound.EndpointUnavailable, outbound.UnsafeDestination, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        error = AppError(503, "embedding_response_invalid", "向量服务调用失败或响应无效，请手动发起新请求")
        error.usage = usage
        raise error from None
