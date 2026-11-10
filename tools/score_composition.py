"""Deterministic fabric scoring tool for the sustainable fashion crew.

What it does
- Reads the fibre list in fabrics.csv (name, aliases, tier, draft_score).
- Scores the OUTER fabric only: each fibre's draft_score weighted by its percentage.
- Reports a small lining_signal and trim_signal, which never change the score.
- Flags (never guesses): unknown fibres, unscored fibres, percentages that do not
  add up, an unclear outer part, and leather (handled separately).

Where it looks for fabrics.csv, in order:
  1. the FABRICS_CSV_PATH environment variable
  2. the current working directory (the project root when you use crewai run)
  3. the folder above this file's folder, then this file's own folder
"""

import csv
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# ---- Settings you may want to change ---------------------------------------

# Labels are often rounded, so allow a part to be this far from 100.
SUM_TOLERANCE = 1.0

# Lining and trim signal wording, based on that part's own score.
SIGNAL_NATURAL_MIN = 80  # at or above: "mostly natural"
SIGNAL_MIXED_MIN = 40    # at or above: "mixed", below: "mostly synthetic"

# Words used to work out what kind of part a label heading refers to.
# Lining is checked first, then trim, then filling, then outer.
TRIM_WORDS = ("trim", "contrast", "rib", "cuff", "binding", "pocket", "applique",
              "embroider", "decorat", "detail")
FILLING_WORDS = ("fill", "padding", "wadding", "insulation", "down", "stuffing")
OUTER_WORDS = ("outer", "shell", "main", "body", "self", "exterior", "fabric")
UNNAMED_PARTS = ("", "composition", "all", "item", "product", "garment")

# ---- Input shapes the agent fills in ---------------------------------------


class FibreInput(BaseModel):
    fibre: str = Field(..., description="Fibre name exactly as on the label, e.g. 'Cotton', 'Polyamide'.")
    percent: float = Field(..., description="Percentage of this fibre in the part, e.g. 60 for 60%.")


class PartInput(BaseModel):
    part: str = Field(
        "outer",
        description="Which part of the garment, e.g. 'outer', 'shell', 'lining', 'trim', 'filling'.",
    )
    fibres: List[FibreInput] = Field(..., description="The fibres and percentages for this part.")


class ScoreCompositionInput(BaseModel):
    parts: List[PartInput] = Field(
        ...,
        description="Every part of the composition listed on the product page, for ONE garment.",
    )


# ---- Helpers ----------------------------------------------------------------


def _get(obj, key, default=None):
    """Read a field from either a dict or an object (CrewAI may pass either)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _norm(text: str) -> str:
    text = str(text or "").lower().replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()


def _loose(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", _norm(text))).strip()


def _to_float(value) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _find_csv() -> Optional[Path]:
    candidates = []
    env_path = os.environ.get("FABRICS_CSV_PATH")
    if env_path:
        candidates.append(Path(env_path))
    here = Path(__file__).resolve().parent
    candidates += [Path.cwd() / "fabrics.csv", here.parent / "fabrics.csv", here / "fabrics.csv"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _load_fabrics(path: Path):
    exact, loose = {}, {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            score_text = (row.get("draft_score") or "").strip()
            entry = {
                "fibre": row["fibre"].strip(),
                "tier": (row.get("tier") or "").strip(),
                "subtype": (row.get("subtype") or "").strip(),
                "draft_score": float(score_text) if score_text else None,
            }
            names = [row["fibre"]] + (row.get("aliases") or "").split("|")
            for name in names:
                if name.strip():
                    exact.setdefault(_norm(name), entry)
                    loose.setdefault(_loose(name), entry)
    return exact, loose


def _match(name: str, lookup):
    exact, loose = lookup
    return exact.get(_norm(name)) or loose.get(_loose(name))


def _role(part_name: str) -> str:
    name = _norm(part_name)
    if "lining" in name:
        return "lining"
    if any(word in name for word in TRIM_WORDS):
        return "trim"
    if any(word in name for word in FILLING_WORDS):
        return "filling"
    if name in UNNAMED_PARTS or any(word in name for word in OUTER_WORDS):
        return "outer"
    return "other"


def _score_part(part_name: str, fibres, lookup) -> dict:
    entries, unknown, unscored = [], [], []
    leather = False
    total = 0.0
    for fibre in fibres:
        raw = str(_get(fibre, "fibre") or "").strip()
        percent = _to_float(_get(fibre, "percent"))
        total += percent
        row = _match(raw, lookup)
        if row is None:
            unknown.append(raw)
            entries.append({"input": raw, "percent": percent, "matched": None,
                            "tier": None, "draft_score": None})
            continue
        if row["subtype"] == "animal_hide":
            leather = True
        elif row["draft_score"] is None:
            unscored.append(row["fibre"])
        entries.append({"input": raw, "percent": percent, "matched": row["fibre"],
                        "tier": row["tier"], "draft_score": row["draft_score"]})
    score = None
    if entries and total > 0 and not unknown and not unscored and not leather:
        score = sum(e["draft_score"] * e["percent"] for e in entries) / total
    return {
        "part": part_name,
        "role": _role(part_name),
        "sum_percent": round(total, 2),
        "sums_ok": abs(total - 100.0) <= SUM_TOLERANCE,
        "score": None if score is None else round(score, 1),
        "fibres": entries,
        "unknown": unknown,
        "unscored": unscored,
        "leather": leather,
    }


def _signal(part: Optional[dict]) -> Optional[str]:
    """A short, plain-language note for a lining or trim part."""
    if part is None:
        return None
    if part["score"] is None:
        if part["leather"]:
            return "leather (not scored)"
        return "unclear (unknown or unscored fibre)"
    if part["score"] >= SIGNAL_NATURAL_MIN:
        label = "mostly natural"
    elif part["score"] >= SIGNAL_MIXED_MIN:
        label = "mixed"
    else:
        label = "mostly synthetic"
    makeup = ", ".join(f"{e['percent']:g}% {e['matched']}" for e in part["fibres"])
    return f"{label} (score {part['score']:g}): {makeup}"


def score_composition(parts, lookup) -> dict:
    scored_parts = []
    for part in parts or []:
        fibres = _get(part, "fibres") or []
        scored_parts.append(_score_part(str(_get(part, "part") or "outer"), fibres, lookup))

    if not scored_parts or not any(p["fibres"] for p in scored_parts):
        return {"status": "missing_composition", "flag_reason": "no fibres were supplied",
                "outer_fabric_score": None, "lining_signal": None, "trim_signal": None, "parts": []}

    outer_parts = [p for p in scored_parts if p["role"] == "outer"]
    assumed_outer = False
    if not outer_parts and len(scored_parts) == 1:
        outer_parts = scored_parts
        assumed_outer = True

    result = {"status": None, "flag_reason": None, "outer_fabric_score": None,
              "lining_signal": None, "trim_signal": None}

    if len(outer_parts) != 1:
        reason = ("more than one part looks like the outer fabric" if outer_parts
                  else "could not tell which part is the outer fabric")
        result.update(status="flagged_unclear_outer", flag_reason=reason)
    else:
        outer = outer_parts[0]
        if assumed_outer:
            outer["role"] = "outer"
        reasons, status = [], None
        if outer["leather"]:
            reasons.append("outer material is leather; handled separately")
            status = "leather_separate"
        if outer["unknown"]:
            reasons.append("unknown fibre(s): " + ", ".join(outer["unknown"]))
            status = status or "flagged_unknown_fibre"
        if outer["unscored"]:
            reasons.append("fibre(s) with no score in fabrics.csv: " + ", ".join(outer["unscored"]))
            status = status or "flagged_unscored_fibre"
        if not outer["fibres"]:
            reasons.append("outer part has no fibres")
            status = status or "flagged_unclear_outer"
        if outer["fibres"] and not outer["sums_ok"]:
            reasons.append(f"outer fibres add up to {outer['sum_percent']:g}%, not 100%")
            status = status or "flagged_percentages"
        if status is None:
            result.update(status="scored", outer_fabric_score=outer["score"])
        else:
            result.update(status=status, flag_reason="; ".join(reasons))

    result["lining_signal"] = _signal(next((p for p in scored_parts if p["role"] == "lining"), None))
    result["trim_signal"] = _signal(next((p for p in scored_parts if p["role"] == "trim"), None))
    result["parts"] = scored_parts
    return result


# ---- The CrewAI tool --------------------------------------------------------


class ScoreCompositionTool(BaseTool):
    name: str = "score_composition"
    description: str = (
        "Scores the fabric composition of ONE garment using the fibre list in fabrics.csv. "
        "Pass every part of the composition as shown on the product page (outer, lining, trim, "
        "filling), each with its fibres and percentages. Returns JSON with status, flag_reason, "
        "outer_fabric_score (outer fabric only), lining_signal, trim_signal and a parts breakdown. "
        "Call it once per item and report its result exactly as returned."
    )
    args_schema: Type[BaseModel] = ScoreCompositionInput

    def _run(self, parts: list) -> str:
        path = _find_csv()
        if path is None:
            return json.dumps({
                "status": "tool_error",
                "flag_reason": "fabrics.csv was not found. Stop and report this; do not guess scores.",
            })
        result = score_composition(parts, _load_fabrics(path))
        return json.dumps(result, ensure_ascii=False, indent=2)
