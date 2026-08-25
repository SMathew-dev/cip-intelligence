from __future__ import annotations


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = unit.strip().lower().replace(" ", "")
    aliases = {
        "c": "C", "°c": "C", "degc": "C", "celsius": "C",
        "f": "F", "°f": "F", "degf": "F", "fahrenheit": "F",
        "l/min": "L/min", "lpm": "L/min", "l/minute": "L/min",
        "gpm": "gpm", "gal/min": "gpm",
        "ms/cm": "mS/cm", "mscm": "mS/cm",
        "us/cm": "uS/cm", "µs/cm": "uS/cm", "μs/cm": "uS/cm",
        "bar": "bar", "barg": "bar", "psi": "psi", "kpa": "kPa",
        "kw": "kW", "kg/min": "kg/min", "kgmin": "kg/min",
    }
    return aliases.get(u, unit.strip())


def convert_value(value: float, source_unit: str | None, target_unit: str | None) -> float:
    source = normalize_unit(source_unit)
    target = normalize_unit(target_unit)
    if target is None or source is None or source == target:
        return value

    if source == "F" and target == "C":
        return (value - 32.0) * 5.0 / 9.0
    if source == "C" and target == "F":
        return value * 9.0 / 5.0 + 32.0
    if source == "gpm" and target == "L/min":
        return value * 3.785411784
    if source == "L/min" and target == "gpm":
        return value / 3.785411784
    if source == "uS/cm" and target == "mS/cm":
        return value / 1000.0
    if source == "mS/cm" and target == "uS/cm":
        return value * 1000.0
    if source == "psi" and target == "bar":
        return value * 0.0689475729
    if source == "bar" and target == "psi":
        return value / 0.0689475729
    if source == "kPa" and target == "bar":
        return value / 100.0
    if source == "bar" and target == "kPa":
        return value * 100.0

    raise ValueError(f"Unsupported unit conversion: {source!r} -> {target!r}")
