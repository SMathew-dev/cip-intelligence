from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SemanticConcept:
    concept: str
    canonical_unit: str | None
    aliases: tuple[str, ...]
    plausible_range: tuple[float, float] | None = None


REGISTRY: tuple[SemanticConcept, ...] = (
    SemanticConcept(
        "cip.return.temperature", "C",
        ("return temperature", "return temp", "ret temp", "cip return temp", "tt return", "tt_ret", "ttret"),
        (-5.0, 100.0),
    ),
    SemanticConcept(
        "cip.supply.temperature", "C",
        ("supply temperature", "supply temp", "sup temp", "cip supply temp", "tt supply", "tt_sup"),
        (-5.0, 100.0),
    ),
    SemanticConcept(
        "cip.return.flow", "L/min",
        ("return flow", "ret flow", "cip return flow", "flow return", "fit return", "fit_ret", "fitret"),
        (0.0, 10000.0),
    ),
    SemanticConcept(
        "cip.supply.flow", "L/min",
        ("supply flow", "sup flow", "cip supply flow", "flow supply", "fit supply", "fit_sup"),
        (0.0, 10000.0),
    ),
    SemanticConcept(
        "cip.return.conductivity", "mS/cm",
        ("return conductivity", "ret conductivity", "return cond", "ret cond", "conductivity return", "ait conductivity", "cond ret", "cond_return"),
        (0.0, 250.0),
    ),
    SemanticConcept(
        "cip.return.pressure", "bar",
        ("return pressure", "ret pressure", "cip return pressure", "pressure return", "pt return", "pt_ret"),
        (-1.0, 50.0),
    ),
    SemanticConcept(
        "cip.supply.pressure", "bar",
        ("supply pressure", "sup pressure", "cip supply pressure", "pressure supply", "pt supply", "pt_sup"),
        (-1.0, 50.0),
    ),
    SemanticConcept(
        "cip.return.ph", None,
        ("return ph", "ret ph", "ph return", "ph_ret"),
        (0.0, 14.0),
    ),
    SemanticConcept(
        "cip.sequence.phase", None,
        ("cip phase", "phase", "cip step", "sequence step", "cip sequence", "cipseq step", "step name"),
        None,
    ),
    SemanticConcept(
        "cip.supply_pump.state", None,
        ("cip pump", "supply pump state", "pump run", "pump status", "cip pump run"),
        None,
    ),
    SemanticConcept(
        "cip.return_valve.state", None,
        ("return valve", "return valve state", "ret valve state", "valve return"),
        None,
    ),
    SemanticConcept(
        "cip.utility.fresh_water.flow", "L/min",
        ("fresh water flow", "makeup water flow", "make up water flow", "cip water flow", "water supply flow"),
        (0.0, 50000.0),
    ),
    SemanticConcept(
        "cip.utility.wastewater.flow", "L/min",
        ("wastewater flow", "effluent flow", "drain flow", "cip drain flow"),
        (0.0, 50000.0),
    ),
    SemanticConcept(
        "cip.utility.electric.power", "kW",
        ("cip electric power", "cip electrical power", "electric power", "pump power"),
        (0.0, 10000.0),
    ),
    SemanticConcept(
        "cip.utility.thermal.power", "kW",
        ("cip thermal power", "thermal power", "heating power", "steam thermal power"),
        (0.0, 100000.0),
    ),
    SemanticConcept(
        "cip.chemical.caustic.mass_flow", "kg/min",
        ("caustic dose", "caustic mass flow", "naoh dose", "alkali dose"),
        (0.0, 1000.0),
    ),
    SemanticConcept(
        "cip.chemical.acid.mass_flow", "kg/min",
        ("acid dose", "acid mass flow", "nitric acid dose"),
        (0.0, 1000.0),
    ),
    SemanticConcept(
        "cip.chemical.sanitizer.mass_flow", "kg/min",
        ("sanitizer dose", "sanitiser dose", "sanitizer mass flow"),
        (0.0, 1000.0),
    ),
    SemanticConcept(
        "cip.asset", None,
        ("asset", "equipment", "equipment id", "circuit", "route", "cip route", "unit"),
        None,
    ),
)

TIMESTAMP_ALIASES = (
    "timestamp", "date time", "datetime", "date_time", "time stamp", "event time", "sample time", "recorded at", "ts"
)

UNIT_ALIASES = {
    "°c": "C", "c": "C", "degc": "C", "celsius": "C",
    "°f": "F", "f": "F", "degf": "F", "fahrenheit": "F",
    "l/min": "L/min", "lpm": "L/min", "l min": "L/min", "liter/min": "L/min", "liters/min": "L/min",
    "gpm": "gpm", "gal/min": "gpm", "us gpm": "gpm",
    "ms/cm": "mS/cm", "mscm": "mS/cm", "millisiemens/cm": "mS/cm",
    "us/cm": "uS/cm", "µs/cm": "uS/cm", "μs/cm": "uS/cm",
    "bar": "bar", "barg": "bar", "psi": "psi", "kpa": "kPa",
    "kw": "kW", "kg/min": "kg/min", "kgmin": "kg/min",
}


def normalize_text(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"[_\-./]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _tokens(value: str) -> set[str]:
    return set(normalize_text(value).split())


def infer_unit(header: str) -> str | None:
    """Infer engineering units only from explicit unit-like syntax.

    We intentionally avoid substring guesses such as treating the letter "c" in a
    tag name as Celsius. Unit inference is advisory; approved mappings still
    require the source unit to be explicit.
    """
    raw = header.strip().lower()
    candidates: list[str] = []
    # Bracketed or parenthesized units are the strongest signal.
    candidates.extend(re.findall(r"[\[(]\s*([^\])]+?)\s*[\])]", raw))
    # Also consider a short final token/suffix, e.g. return_flow_gpm.
    suffix_norm = normalize_text(raw)
    if suffix_norm:
        candidates.append(suffix_norm.split()[-1])
    for candidate in candidates:
        key = candidate.strip().replace(" ", "")
        for alias, canonical in UNIT_ALIASES.items():
            if key == alias.replace(" ", ""):
                return canonical
    return None


def infer_timestamp_column(headers: Iterable[str]) -> tuple[str | None, float]:
    best: tuple[str | None, float] = (None, 0.0)
    for header in headers:
        norm = normalize_text(header)
        for alias in TIMESTAMP_ALIASES:
            alias_norm = normalize_text(alias)
            if norm == alias_norm:
                return header, 1.0
            if alias_norm in norm or norm in alias_norm:
                if best[1] < 0.82:
                    best = (header, 0.82)
    return best


def infer_concept(header: str) -> list[dict]:
    """Return conservative, ranked semantic candidates.

    We require a measurement/type cue (temperature, flow, pressure, etc.) and,
    for directional process signals, a return/supply cue. Opaque instrument IDs
    such as ``FIT_214`` remain ambiguous until engineering approves a mapping.
    """
    norm = normalize_text(header)
    tokens = _tokens(header)

    cue_map: dict[str, tuple[set[str], str | None]] = {
        "cip.return.temperature": ({"temperature", "temp", "tt"}, "return"),
        "cip.supply.temperature": ({"temperature", "temp", "tt"}, "supply"),
        "cip.return.flow": ({"flow", "fit"}, "return"),
        "cip.supply.flow": ({"flow", "fit"}, "supply"),
        "cip.return.conductivity": ({"conductivity", "cond"}, "return"),
        "cip.return.pressure": ({"pressure", "pt"}, "return"),
        "cip.supply.pressure": ({"pressure", "pt"}, "supply"),
        "cip.return.ph": ({"ph"}, "return"),
        "cip.sequence.phase": ({"phase", "step", "sequence", "cipseq"}, None),
        "cip.supply_pump.state": ({"pump"}, None),
        "cip.return_valve.state": ({"valve"}, "return"),
        "cip.utility.fresh_water.flow": ({"water", "flow"}, None),
        "cip.utility.wastewater.flow": ({"wastewater", "effluent", "drain"}, None),
        "cip.utility.electric.power": ({"electric", "electrical", "power"}, None),
        "cip.utility.thermal.power": ({"thermal", "heating", "power"}, None),
        "cip.chemical.caustic.mass_flow": ({"caustic", "naoh", "alkali"}, None),
        "cip.chemical.acid.mass_flow": ({"acid", "nitric"}, None),
        "cip.chemical.sanitizer.mass_flow": ({"sanitizer", "sanitiser"}, None),
        "cip.asset": ({"asset", "equipment", "circuit", "route", "unit"}, None),
    }
    return_tokens = {"return", "ret"}
    supply_tokens = {"supply", "sup"}

    results: list[dict] = []
    resource_concepts = {
        "cip.utility.fresh_water.flow", "cip.utility.wastewater.flow",
        "cip.utility.electric.power", "cip.utility.thermal.power",
        "cip.chemical.caustic.mass_flow", "cip.chemical.acid.mass_flow",
        "cip.chemical.sanitizer.mass_flow",
    }
    for item in REGISTRY:
        cues, direction = cue_map[item.concept]
        matched_cues = tokens & cues
        if not matched_cues:
            continue
        if item.concept in resource_concepts:
            # Resource accounting is too consequential for a loose token match.
            # Require a recognizable full alias phrase; opaque utility tags remain manual.
            if not any(normalize_text(alias) in norm for alias in item.aliases):
                continue

        if direction == "return":
            if tokens & supply_tokens:
                continue
            if not (tokens & return_tokens):
                # Instrument type is known, destination is not. Do not suggest a
                # directional mapping from an opaque tag alone.
                continue
            confidence = 0.94
        elif direction == "supply":
            if tokens & return_tokens:
                continue
            if not (tokens & supply_tokens):
                continue
            confidence = 0.94
        else:
            confidence = 0.92

        # Exact/common human-readable aliases get a small confidence bump.
        alias_match = None
        for alias in item.aliases:
            alias_norm = normalize_text(alias)
            if norm == alias_norm or alias_norm in norm:
                alias_match = alias
                confidence = min(0.99, confidence + 0.04)
                break

        results.append({
            "concept": item.concept,
            "canonical_unit": item.canonical_unit,
            "source_unit_guess": infer_unit(header),
            "confidence": round(confidence, 3),
            "reason": (
                f"measurement cue(s) {sorted(matched_cues)} and {direction or 'type'} context"
                + (f"; resembles '{alias_match}'" if alias_match else "")
            ),
        })

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:3]


def get_concept(concept: str) -> SemanticConcept | None:
    return next((x for x in REGISTRY if x.concept == concept), None)
