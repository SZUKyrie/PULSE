from __future__ import annotations

from openai import OpenAI, APIConnectionError, APITimeoutError

from .config import settings


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "not-needed",
    ):
        self.model = model or settings.llm_model
        self.client = OpenAI(
            base_url=base_url or settings.llm_base_url,
            api_key=api_key,
        )

    def generate(
        self, messages: list[dict], temperature: float | None = None
    ) -> str:
        temp = temperature if temperature is not None else settings.llm_temperature
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temp,
            )
            return response.choices[0].message.content or ""
        except (APIConnectionError, APITimeoutError) as e:
            raise ConnectionError(
                f"Failed to connect to LLM at {self.client.base_url}: {e}"
            ) from e

    def generate_sql(self, system_prompt: str, user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.generate(messages)
