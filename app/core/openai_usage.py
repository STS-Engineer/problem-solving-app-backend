from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai.types.responses import Response

logger = logging.getLogger(__name__)


def log_openai_usage(response: ChatCompletion | Response, *, feature: str) -> None:
    usage = response.usage
    if usage is None:
        return

    if hasattr(usage, "prompt_tokens"):
        input_tokens = usage.prompt_tokens
        output_tokens = usage.completion_tokens
    else:
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens

    logger.info(
        "openai_usage %s",
        json.dumps(
            {
                "feature": feature,
                "model": response.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": usage.total_tokens,
            }
        ),
    )
