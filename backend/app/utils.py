import secrets
import random

# ─── Curated color palette for cursors (high-contrast on dark bg) ───
CURSOR_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
    "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
    "#F0B27A", "#82E0AA", "#F1948A", "#AED6F1", "#D2B4DE",
    "#A3E4D7", "#FAD7A0", "#A9CCE3", "#D5F5E3", "#FADBD8",
    "#E8DAEF", "#D4EFDF", "#FCF3CF", "#D6EAF8", "#FDEDEC",
    "#F9E79F", "#A2D9CE", "#F5B7B1", "#AEB6BF", "#D7BDE2",
]

# ─── Fun monikers for anonymous collaboration ───
ADJECTIVES = [
    "swift", "cosmic", "emerald", "crystal", "velvet",
    "golden", "silver", "neon", "mystic", "crimson",
    "azure", "lunar", "solar", "stellar", "coral",
    "amber", "indigo", "scarlet", "jade", "ruby",
]

NOUNS = [
    "phoenix", "dragon", "falcon", "panther", "dolphin",
    "tiger", "eagle", "wolf", "fox", "hawk",
    "raven", "otter", "lynx", "cobra", "panda",
    "jaguar", "viper", "heron", "crane", "sparrow",
]


def generate_key(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe key."""
    return secrets.token_urlsafe(length)


def generate_moniker() -> str:
    """Generate a random adjective-noun moniker."""
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    return f"{adj}{noun}"


def generate_cursor_color() -> str:
    """Pick a random cursor color from the curated palette."""
    return random.choice(CURSOR_COLORS)
