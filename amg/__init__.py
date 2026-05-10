"""
AMG OS — Adult VOD Scene Processor v1

A modular, research-validated scene processing system for adult VOD distribution.
Built for Apple Silicon (M4 Pro+), uses Qwen2.5-VL via Ollama for scoring.

See docs/ARCHITECTURE.md for the system design.
"""
from amg.__version__ import __version__, __version_info__, __release_date__

__all__ = ["__version__", "__version_info__", "__release_date__"]
