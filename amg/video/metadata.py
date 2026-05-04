"""
Video metadata extraction via ffprobe.

Returns: duration_sec, fps, width, height, codec, size_bytes
"""
import json
import subprocess
from pathlib import Path
from typing import Optional


def get_metadata(video_path: Path) -> Optional[dict]:
    """
    Extract video metadata using ffprobe.

    Returns dict or None if ffprobe fails:
        {
            'duration_sec': float,
            'fps': float,
            'width': int,
            'height': int,
            'codec': str,
            'size_bytes': int,
            'size_gb': float,
            'bitrate': int (bits/sec),
            'has_audio': bool,
        }
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return None

    # Find video stream
    video_stream = None
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio":
            has_audio = True

    if video_stream is None:
        return None

    # Parse fps (avg_frame_rate is "30000/1001" format)
    fps = _parse_fraction(video_stream.get("avg_frame_rate", "0/1"))
    if fps <= 0:
        fps = _parse_fraction(video_stream.get("r_frame_rate", "0/1"))

    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0))
    size_bytes = int(fmt.get("size", 0))
    bitrate = int(fmt.get("bit_rate", 0))

    return {
        "duration_sec": duration,
        "fps": fps,
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "codec": video_stream.get("codec_name", "unknown"),
        "size_bytes": size_bytes,
        "size_gb": size_bytes / (1024 ** 3),
        "bitrate": bitrate,
        "has_audio": has_audio,
    }


def _parse_fraction(s: str) -> float:
    """Parse a fraction string like '30000/1001' to float."""
    if "/" in s:
        try:
            num, denom = s.split("/")
            num = float(num)
            denom = float(denom)
            return num / denom if denom else 0
        except (ValueError, ZeroDivisionError):
            return 0
    try:
        return float(s)
    except ValueError:
        return 0
