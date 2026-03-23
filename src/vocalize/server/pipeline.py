from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

from openai import AsyncOpenAI

from vocalize.config import CONFIG_PATH, LLMConfig, PipelineConfig, PipelineStepConfig
from vocalize.models import StepDetail

logger = logging.getLogger(__name__)


class PipelineStep(ABC):
    step_type: str = "unknown"

    @abstractmethod
    async def process(self, text: str) -> str: ...

    def get_detail(self) -> dict:
        return {}



class LLMRewrite(PipelineStep):
    step_type = "llm_rewrite"

    def __init__(
        self,
        llm_client: AsyncOpenAI,
        model: str,
        prompt: str,
        temperature: float = 0,
        max_tokens: int = 512,
    ):
        self.client = llm_client
        self.model = model
        self.prompt = prompt
        self.temperature = temperature
        self.max_tokens = max_tokens

    def get_detail(self) -> dict:
        return {"model": self.model, "system_prompt": self.prompt}

    async def process(self, text: str) -> str:
        if not text.strip():
            return text
        logger.info(
            "[LLMRewrite] model=%s temperature=%s max_tokens=%s",
            self.model, self.temperature, self.max_tokens,
        )
        logger.debug("[LLMRewrite] system: %r", self.prompt)
        logger.info("[LLMRewrite] IN:  %r", text)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": text},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            result = response.choices[0].message.content
            output = result.strip() if result else text
            logger.info("[LLMRewrite] OUT: %r", output)
            return output
        except Exception:
            logger.exception("LLM rewrite failed, returning original text")
            return text


class Pipeline:
    def __init__(self, config: PipelineConfig, llm_config: LLMConfig):
        logger.info(
            "Pipeline LLM: provider=%s model=%s base_url=%s",
            llm_config.provider, llm_config.model, llm_config.base_url,
        )
        self.llm_client = AsyncOpenAI(
            base_url=llm_config.base_url,
            api_key=llm_config.api_key,
            timeout=30.0,
        )
        self.steps = self._build_steps(config.steps, llm_config)

    def _build_steps(
        self, step_configs: list[PipelineStepConfig], llm_config: LLMConfig
    ) -> list[PipelineStep]:
        steps: list[PipelineStep] = []
        for sc in step_configs:
            if not sc.enabled:
                continue
            if sc.type == "llm_rewrite":
                prompt_path = (CONFIG_PATH.parent / sc.params["prompt_file"]).resolve()
                if not prompt_path.exists():
                    raise FileNotFoundError(f"prompt_file not found: {prompt_path}")
                prompt = prompt_path.read_text(encoding="utf-8")
                logger.info("[Pipeline] Loaded prompt from file: %s", prompt_path)
                temperature = sc.params.get("temperature", 0)
                max_tokens = sc.params.get("max_tokens", 512)
                steps.append(
                    LLMRewrite(
                        self.llm_client,
                        llm_config.model,
                        prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                )
            else:
                logger.warning("Unknown pipeline step type: %s", sc.type)
        return steps

    async def run(self, text: str) -> tuple[str, list[StepDetail]]:
        details: list[StepDetail] = []
        for step in self.steps:
            input_text = text
            start = time.perf_counter()
            text = await step.process(text)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            details.append(
                StepDetail(
                    step_type=step.step_type,
                    input_text=input_text,
                    output_text=text,
                    time_ms=elapsed_ms,
                    **step.get_detail(),
                )
            )
        return text, details
