"""
Studio Profiles.

Each studio (YasminaBrady, BlondeHexe, MaximoGarcia, etc.) has a JSON profile
that informs scoring, language detection, calibration history.

Profile schema (see v11_final_specification.md § XVIII.A):
{
    "name": "YasminaBrady",
    "display_name": "Yasmina Brady Productions",
    "primary_language": "en",
    "secondary_languages": ["es"],
    "primary_market": "US",
    "secondary_markets": ["EU", "LATAM"],
    "performers": {
        "regular": [...],
        "frequent_costars": [...],
        "occasional": [...]
    },
    "default_genres": [...],
    "common_scene_types": [...],
    "filename_patterns": {...},
    "platform_targets": [...],
    "banned_terms": [...],
    "calibration_history": {...},
    "notes": "..."
}
"""
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from amg.config import STUDIO_PROFILES_DIR

# Known studio name patterns (case-insensitive substrings to match in path)
KNOWN_STUDIOS = {
    "YasminaBrady": ["yasminabrady", "yasmina_brady", "yasmina brady"],
    "BlondeHexe": ["blondehexe", "blonde_hexe", "blonde hexe"],
    "MaximoGarcia": ["maximogarcia", "maximo_garcia", "maximo garcia"],
    "RaulsBud": ["raulsbud", "rauls_bud", "raul's bud"],
    "YeriBlue": ["yeriblue", "yeri_blue", "yeri blue"],
    "Shinaryen": ["shinaryen"],
    "HussiePass": ["hussiepass", "hussie_pass", "hussie pass"],
    "NaughtyAmerica": ["naughtyamerica", "naughty_america", "naughty america"],
    "MILTRIP": ["miltrip"],
    "FreakOff": ["freakoff", "freak_off", "freakoff films"],
    "Amazing": ["amazing entertainment", "amazingentertainment"],
    "BradyBud": ["bradybud", "brady_bud", "brady bud"],
    "VinceKarter": ["vincekarter", "vince_karter", "vince karter"],
    "NellyKent": ["nellykent", "nelly_kent", "nelly kent"],
    "NicoGrey": ["nicogrey", "nico_grey", "nico grey"],
    "Team18": ["team18", "team_18", "team 18"],
    "XFeeds": ["xfeeds", "x_feeds", "x feeds"],
    "IntimacyShots": ["intimacyshots", "intimacy_shots", "intimacy shots"],
    "MissLexa": ["misslexa", "miss_lexa", "miss lexa"],
    "JMAC": ["jmac", "j_mac", "j mac"],
}


def detect_studio(video_path: Path) -> Optional[str]:
    """
    Detect studio name from video path.

    Looks at parent directories and filename for known studio patterns.
    Returns canonical studio name or None.
    """
    # Build candidate strings from path components
    candidates = []
    for part in video_path.parts:
        candidates.append(part.lower())
    candidates.append(video_path.stem.lower())

    full_path_lower = str(video_path).lower()

    for canonical_name, patterns in KNOWN_STUDIOS.items():
        for pattern in patterns:
            # Check word-bounded match in full path
            if pattern in full_path_lower:
                return canonical_name

    return None


def studio_profile_path(studio_name: str) -> Path:
    """Get path to studio profile JSON file."""
    safe_name = "".join(c for c in studio_name if c.isalnum() or c in "_-")
    return STUDIO_PROFILES_DIR / f"{safe_name}.json"


def load_studio_profile(studio_name: str) -> Optional[dict]:
    """Load studio profile from disk. Returns None if not found."""
    path = studio_profile_path(studio_name)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_studio_profile(profile: dict) -> None:
    """Save studio profile to disk."""
    STUDIO_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = studio_profile_path(profile["name"])
    profile["last_updated"] = datetime.utcnow().isoformat()
    with open(path, "w") as f:
        json.dump(profile, f, indent=2, default=str)


def get_or_create_profile(studio_name: str) -> dict:
    """
    Load profile, or create stub with reasonable defaults if missing.
    Always returns a usable profile dict.
    """
    profile = load_studio_profile(studio_name)
    if profile:
        return profile

    # Create stub
    profile = _create_stub_profile(studio_name)
    save_studio_profile(profile)
    return profile


def _create_stub_profile(studio_name: str) -> dict:
    """Build a default profile for a newly-discovered studio."""
    # Apply known defaults if we recognize the studio
    studio_defaults = {
        "YasminaBrady": {
            "primary_language": "en",
            "secondary_languages": ["es"],
            "primary_market": "US",
            "default_genres": ["GROUP", "GLAMOUR"],
            "common_scene_types": ["FOURSOME", "THREESOME", "GANGBANG", "COUPLE"],
            "filename_patterns": {
                "uses_performer_code": True,
                "code_position": "prefix",
                "title_format": "{number} {code} - {description}",
            },
        },
        "BlondeHexe": {
            "primary_language": "de",
            "secondary_languages": ["en"],
            "primary_market": "EU_DE",
            "default_genres": ["MILF", "MATURE", "GERMAN"],
            "common_scene_types": ["COUPLE", "SOLO", "MILF"],
        },
        "MaximoGarcia": {
            "primary_language": "es",
            "secondary_languages": ["en"],
            "primary_market": "ES",
            "secondary_markets": ["LATAM"],
            "default_genres": ["LATINA", "GROUP"],
            "common_scene_types": ["COUPLE", "THREESOME", "GROUP"],
        },
        "HussiePass": {
            "primary_language": "en",
            "primary_market": "US",
            "default_genres": ["INTERRACIAL", "POV"],
            "common_scene_types": ["COUPLE", "POV"],
        },
        "NaughtyAmerica": {
            "primary_language": "en",
            "primary_market": "US",
            "default_genres": ["MILF", "OFFICE", "POV"],
            "common_scene_types": ["COUPLE", "POV"],
        },
    }

    base = {
        "name": studio_name,
        "display_name": studio_name,
        "primary_language": "en",
        "secondary_languages": [],
        "primary_market": "US",
        "secondary_markets": [],
        "performers": {
            "regular": [],
            "frequent_costars": [],
            "occasional": [],
        },
        "default_genres": [],
        "common_scene_types": [],
        "filename_patterns": {
            "uses_performer_code": False,
            "code_position": None,
            "title_format": None,
        },
        "platform_targets": ["AEBN", "SLR", "ADE"],
        "banned_terms": [],
        "calibration_history": {
            "tier_1_floor_avg": None,
            "tier_1_floor_p25": None,
            "tier_1_floor_p75": None,
            "scenes_processed": 0,
            "last_updated": None,
        },
        "notes": "Auto-created stub profile. Edit to customize.",
        "stub": True,  # Flag indicating this is auto-created
    }

    # Apply specific defaults if known
    if studio_name in studio_defaults:
        base.update(studio_defaults[studio_name])
        base["stub"] = False  # Has known defaults

    return base


def update_calibration_history(studio_name: str, sharp_floor: float) -> None:
    """
    Update studio profile's calibration history with a new sharpness floor measurement.
    Used by learning system over time.
    """
    profile = get_or_create_profile(studio_name)
    history = profile.setdefault("calibration_history", {})

    # Track running average and percentiles (simplistic but effective)
    floors = history.setdefault("_recent_floors", [])
    floors.append(sharp_floor)
    # Keep last 50 floors
    floors = floors[-50:]
    history["_recent_floors"] = floors

    if floors:
        sorted_floors = sorted(floors)
        n = len(sorted_floors)
        history["tier_1_floor_avg"] = sum(sorted_floors) / n
        history["tier_1_floor_p25"] = sorted_floors[max(0, n // 4)]
        history["tier_1_floor_p75"] = sorted_floors[min(n - 1, 3 * n // 4)]

    history["scenes_processed"] = history.get("scenes_processed", 0) + 1
    history["last_updated"] = datetime.utcnow().isoformat()

    save_studio_profile(profile)
