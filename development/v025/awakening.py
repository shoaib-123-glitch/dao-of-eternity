"""Character identity and readable Discord profile rendering for Dao of Eternity."""

ROOTS = {
    "Fire": "Alchemy / offensive arts",
    "Water": "Flow / recovery arts",
    "Wood": "Herbs / healing arts",
    "Earth": "Defensive formations",
    "Metal": "Sword / forging arts",
    "Lightning": "Rapid combat arts",
    "Wind": "Swift movement and evasive arts",
    "Ice": "Freezing and battlefield control",
}

RARE_ROOTS = {"Light":"Purification", "Shadow":"Stealth", "Poison":"Toxic arts",
              "Blood":"Vitality arts", "Soul":"Mind arts", "Void":"Spatial arts",
              "Time":"Temporal arts", "Chaos":"Unstable primordial arts"}

BACKGROUNDS = {
    "Wandering Mortal": "Self-taught and independent",
    "Village Disciple": "An ordinary beginning",
    "Scholar": "Seeks ancient scriptures",
    "Exiled Heir": "A forgotten family legacy",
}


def validate_origin(root, background):
    if root not in ROOTS:
        raise ValueError("Unknown spiritual root")
    if background not in BACKGROUNDS:
        raise ValueError("Unknown origin")
    return True


def profile_lines(state, origin):
    """Render the LIVE character, using the actual v0.9 player state schema.

    The engine stores advanced_to_foundation, NOT a `realm` field.
    The trial sandbox is a separate record and is not queried here.
    """
    player = state["player"]
    foundation = bool(player.get("advanced_to_foundation", False))
    stage = player.get("stage", 1)
    qi = player.get("qi", 0)

    if state.get('golden_core'):
        from golden_core_v022 import capacity
        golden_stage=state.get('golden_stage',1)
        heading=f'Golden Core · Stage {golden_stage}'
        qi=state.get('golden_qi',0);cap=capacity(golden_stage)
    elif foundation:
        from rapid import foundation_capacity
        foundation_stage = state.get('foundation_stage', 1)
        heading = f"Foundation Establishment · Stage {foundation_stage}"
        qi = state.get('foundation_qi', 0)
        cap = foundation_capacity(foundation_stage)
    else:
        heading = f"Qi Refining · Stage {stage}"
        cap = 600 if stage == 9 else 100 + 20 * (stage - 1) + 5 * (stage - 1) ** 2

    filled = max(0, min(10, round(10 * qi / max(1, cap))))
    # Basic ASCII avoids unsupported Unicode squares in some Discord clients/fonts.
    progress = "[" + "#" * filled + "-" * (10 - filled) + f"] {qi}/{cap} Qi"

    if origin:
        identity = f"{origin['root']} Root · {origin['background']}"
    else:
        identity = "Unawakened — use /awaken"

    condition = (
        f"Stability {player.get('stability', 0)} · "
        f"Foundation {player.get('foundation_quality', 0)} · "
        f"Meridians {player.get('meridian_integrity', 0)} · "
        f"Impurity {player.get('impurity', 0)}"
    )
    return {"heading": heading, "progress": progress,
            "origin": identity, "condition": condition}
