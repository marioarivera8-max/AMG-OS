"""Scoring: AI vision model client, prompt building, response parsing, parallel orchestration."""
from amg.scoring.ai_client import AIClient, AIResponse
from amg.scoring.prompt import build_scoring_prompt
from amg.scoring.parser import parse_ai_response
from amg.scoring.orchestrator import score_frames_parallel

__all__ = [
    "AIClient",
    "AIResponse",
    "build_scoring_prompt",
    "parse_ai_response",
    "score_frames_parallel",
]
