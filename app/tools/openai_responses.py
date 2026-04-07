from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OpenAIResponsesError(Exception):
    """Raised when a Responses API request fails."""


class OpenAIResponsesClient:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout_seconds: int = 45,
        base_url: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 1400,
    ) -> str:
        response_json = self._create_response(
            messages=[
                self._message("system", system_prompt),
                self._message("user", user_prompt),
            ],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return self._extract_output_text(response_json)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_output_tokens: int = 1400,
    ) -> dict[str, Any]:
        instruction = (
            "Return only a valid JSON object. Do not include markdown fences or extra text."
        )
        response_text = self.generate_text(
            system_prompt=system_prompt,
            user_prompt=f"{user_prompt}\n\n{instruction}",
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            extracted = self._extract_json_object(response_text)
            return json.loads(extracted)

    def _create_response(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise OpenAIResponsesError("OpenAI Responses client is disabled (missing API key/model).")

        payload: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/responses"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise OpenAIResponsesError(f"Network error calling Responses API: {exc}") from exc

        if response.status_code >= 400:
            raise OpenAIResponsesError(
                f"Responses API error {response.status_code}: {response.text[:500]}"
            )

        return response.json()

    def _message(self, role: str, text: str) -> dict[str, Any]:
        return {
            "role": role,
            "content": [
                {
                    "type": "input_text",
                    "text": text,
                }
            ],
        }

    def _extract_output_text(self, response_json: dict[str, Any]) -> str:
        output_text = response_json.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output_items = response_json.get("output") or []
        for item in output_items:
            if item.get("type") != "message":
                continue
            for content_item in item.get("content", []):
                if content_item.get("type") == "output_text":
                    text = content_item.get("text", "")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

        raise OpenAIResponsesError("Responses API did not return output text.")

    def _extract_json_object(self, text: str) -> str:
        start = text.find("{")
        if start == -1:
            raise OpenAIResponsesError("Could not find JSON object in model output.")

        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]

        raise OpenAIResponsesError("Unbalanced JSON object in model output.")
