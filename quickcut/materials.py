"""Load a material's laser cutting recipe from the shared profile file.

One source of truth for every vibes tool: ``material_profiles/laser_materials.yaml``
sits next to the tool directories, and each entry's ``laser`` block is the recipe
(feed / power / passes) an emitter needs.
"""

from __future__ import annotations

from pathlib import Path

SHARED_MATERIALS = (
    Path(__file__).resolve().parent.parent
    / "material_profiles"
    / "laser_materials.yaml"
)


def load_material(material_id: str, profile_path: Path = SHARED_MATERIALS) -> dict:
    """Return the ``laser`` recipe dict for `material_id` from the profile file."""
    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "material profiles need pyyaml (pip install pyyaml), or pass the "
            "feed/power/passes explicitly instead."
        )
    if not profile_path.exists():
        raise SystemExit(f"profile file not found: {profile_path}")
    materials = yaml.safe_load(profile_path.read_text())
    for material in materials:
        if material.get("id") == material_id:
            if "laser" not in material:
                raise SystemExit(f"material '{material_id}' has no laser recipe")
            return material["laser"]
    known = ", ".join(sorted(str(m.get("id", "?")) for m in materials))
    raise SystemExit(f"unknown material: {material_id}. Available: {known}")
