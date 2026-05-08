"""
AI Client — Ollama HTTP wrapper.

Handles:
- Image encoding to base64
- Resizing to AI_IMAGE_SIZE before send (saves bandwidth, qwen2.5-vl native)
- HTTP request to Ollama
- Retry logic (3 attempts with backoff)
- Timeout enforcement
- Error classification
"""
import base64
import io
import os
import time
from dataclasses import dataclass, field
from typing import Optional
import cv2
import numpy as np
from PIL import Image
import requests

from amg.config import (
    OLLAMA_API_URL,
    VISION_MODEL,
    TEXT_MODEL,
    TEXT_MODEL_FALLBACK,
    AI_IMAGE_SIZE,
    AI_CALL_TIMEOUT_SEC,
    AI_CALL_RETRY_COUNT,
    AI_CALL_RETRY_DELAYS,
    AI_SCORING_SEED,
    TEXT_GEN_TIMEOUT_SEC,
    TEXT_GEN_TEMPERATURE,
)


@dataclass
class AIResponse:
    """Result of a single AI call."""
    success: bool
    raw_text: str = ""
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    duration_sec: float = 0.0
    attempts: int = 1
    extras: dict = field(default_factory=dict)


class AIClient:
    """
    Ollama vision model client.

    Designed for use with ThreadPoolExecutor (each call is independent,
    Ollama handles parallelism internally via OLLAMA_NUM_PARALLEL).
    """

    def __init__(
        self,
        api_url: str = OLLAMA_API_URL,
        model: Optional[str] = None,
        text_model: Optional[str] = None,
        text_model_fallback: Optional[str] = None,
        timeout_sec: int = AI_CALL_TIMEOUT_SEC,
        text_timeout_sec: int = TEXT_GEN_TIMEOUT_SEC,
    ):
        self.api_url = api_url
        # Vision model resolution order: explicit model= arg → AMG_VISION_MODEL_OVERRIDE env var
        # (used by scripts/bake_off.py to swap models per run without modifying
        # config) → VISION_MODEL constant from config. Default behavior unchanged
        # when env var is unset.
        self.vision_model = model or os.environ.get("AMG_VISION_MODEL_OVERRIDE") or VISION_MODEL
        # Text model resolution:
        # - explicit text_model arg (when caller wants strict routing)
        # - fallback to explicit model arg for backward compatibility
        # - AMG_TEXT_MODEL_OVERRIDE env
        # - TEXT_MODEL config default
        self.text_model = (
            text_model
            or model
            or os.environ.get("AMG_TEXT_MODEL_OVERRIDE")
            or TEXT_MODEL
        )
        self.text_model_fallback = (
            text_model_fallback
            or os.environ.get("AMG_TEXT_MODEL_FALLBACK_OVERRIDE")
            or TEXT_MODEL_FALLBACK
        )
        # Keep legacy `model` attribute for older call sites/logging.
        self.model = self.vision_model
        self.timeout_sec = timeout_sec
        self.text_timeout_sec = text_timeout_sec
        # Use a session for connection pooling (faster across many calls)
        self._session = requests.Session()

    def score_frame(
        self,
        frame_bgr: np.ndarray,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """
        Send a frame to the vision model with the given prompt.

        Returns AIResponse with raw text or error code.
        """
        start = time.time()
        encoded = self._encode_frame(frame_bgr)
        if encoded is None:
            return AIResponse(
                success=False,
                error_code="E_FRAME_ENCODE_FAIL",
                error_message="Could not encode frame as JPEG",
                duration_sec=time.time() - start,
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": prompt,
            "images": [encoded],
        })

        payload = {
            "model": self.vision_model,
            "messages": messages,
            "stream": False,
            "options": {
                # v11.1.2: temperature=0 + fixed seed = deterministic scoring.
                # Same frame + same prompt → same score on every run, so prompt
                # changes can be A/B tested without Ollama randomness as a confound.
                # Title generation (generate_text) deliberately keeps higher temp
                # because the operator picks from multiple title suggestions.
                "temperature": 0.0,
                "seed": AI_SCORING_SEED,
                "num_predict": 200,  # Cap response length
            },
        }

        # Retry loop
        last_error = None
        for attempt in range(1, AI_CALL_RETRY_COUNT + 1):
            try:
                response = self._session.post(
                    self.api_url,
                    json=payload,
                    timeout=self.timeout_sec,
                )

                if response.status_code == 200:
                    data = response.json()
                    raw_text = data.get("message", {}).get("content", "")
                    return AIResponse(
                        success=True,
                        raw_text=raw_text,
                        duration_sec=time.time() - start,
                        attempts=attempt,
                    )

                # Non-200 response
                last_error = (
                    "E_AI_PARSE_FAIL",
                    f"HTTP {response.status_code}: {response.text[:200]}",
                )

            except requests.exceptions.ConnectionError as e:
                last_error = ("E_AI_UNAVAILABLE", f"Connection refused: {e}")
            except requests.exceptions.Timeout:
                last_error = ("E_AI_TIMEOUT", f"Request timed out after {self.timeout_sec}s")
            except requests.exceptions.RequestException as e:
                last_error = ("E_AI_UNAVAILABLE", f"Request error: {e}")
            except Exception as e:
                last_error = ("E_AI_PARSE_FAIL", f"Unexpected error: {e}")

            # Wait before retry (if more attempts left)
            if attempt < AI_CALL_RETRY_COUNT:
                delay = AI_CALL_RETRY_DELAYS[min(attempt - 1, len(AI_CALL_RETRY_DELAYS) - 1)]
                time.sleep(delay)

        # All retries exhausted
        return AIResponse(
            success=False,
            error_code=last_error[0] if last_error else "E_AI_UNAVAILABLE",
            error_message=last_error[1] if last_error else "Unknown error",
            duration_sec=time.time() - start,
            attempts=AI_CALL_RETRY_COUNT,
        )

    def _encode_frame(self, frame_bgr: np.ndarray) -> Optional[str]:
        """
        Resize frame to AI_IMAGE_SIZE and encode as base64 JPEG.

        Returns base64 string or None on failure.
        """
        if frame_bgr is None:
            return None
        try:
            # Resize (qwen2.5-vl native size for fastest processing)
            target_w, target_h = AI_IMAGE_SIZE
            h, w = frame_bgr.shape[:2]
            # Maintain aspect ratio - fit within target box
            scale = min(target_w / w, target_h / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(frame_bgr, (new_w, new_h))

            # Convert BGR -> RGB for PIL
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)

            # Encode as JPEG
            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=85, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return None

    def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_sec: Optional[int] = None,
    ) -> AIResponse:
        """
        Generate text without an image (for title generation, etc.).

        v11.1 addition. Lighter than score_frame since no image to encode.
        """
        start = time.time()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        result = self._generate_text_with_model(
            model_name=self.text_model,
            messages=messages,
            timeout=timeout_sec or self.text_timeout_sec,
        )
        if result.success:
            result.duration_sec = time.time() - start
            return result

        fallback = (self.text_model_fallback or "").strip()
        should_retry_fallback = (
            bool(fallback)
            and fallback != self.text_model
            and result.error_code in {"E_AI_PARSE_FAIL", "E_AI_TIMEOUT", "E_AI_UNAVAILABLE"}
        )
        if should_retry_fallback:
            second = self._generate_text_with_model(
                model_name=fallback,
                messages=messages,
                timeout=timeout_sec or self.text_timeout_sec,
            )
            if second.success:
                second.duration_sec = time.time() - start
                second.extras = {
                    **(second.extras or {}),
                    "fallback_model_used": fallback,
                    "primary_text_model_failed": self.text_model,
                }
                return second
            result.extras = {
                **(result.extras or {}),
                "fallback_model_attempted": fallback,
                "fallback_error_code": second.error_code,
            }

        result.duration_sec = time.time() - start
        return result

    def _generate_text_with_model(
        self,
        *,
        model_name: str,
        messages: list,
        timeout: int,
    ) -> AIResponse:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": TEXT_GEN_TEMPERATURE,
                "num_predict": 400,
            },
        }
        try:
            response = self._session.post(
                self.api_url,
                json=payload,
                timeout=timeout,
            )
            if response.status_code == 200:
                data = response.json()
                raw_text = data.get("message", {}).get("content", "")
                return AIResponse(
                    success=True,
                    raw_text=raw_text,
                    extras={"model_used": model_name},
                )
            return AIResponse(
                success=False,
                error_code="E_AI_PARSE_FAIL",
                error_message=f"HTTP {response.status_code}",
                extras={"model_used": model_name, "status_code": response.status_code},
            )
        except requests.exceptions.Timeout:
            return AIResponse(
                success=False,
                error_code="E_AI_TIMEOUT",
                error_message=f"Timeout after {timeout}s",
                extras={"model_used": model_name},
            )
        except Exception as e:
            return AIResponse(
                success=False,
                error_code="E_AI_UNAVAILABLE",
                error_message=str(e),
                extras={"model_used": model_name},
            )

    def is_alive(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            base_url = self.api_url.rsplit("/api/", 1)[0]
            response = self._session.get(f"{base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def is_model_loaded(self) -> bool:
        """Check if the vision model is available."""
        try:
            base_url = self.api_url.rsplit("/api/", 1)[0]
            response = self._session.get(f"{base_url}/api/tags", timeout=5)
            if response.status_code != 200:
                return False
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            # Match exact name or with :latest suffix
            return any(self.vision_model in m for m in models)
        except Exception:
            return False
