"""
Vegetable-garden planting calendar.

Curated sow / transplant / harvest windows for the crops grown at El Durazno,
tuned to a temperate **southern-hemisphere** climate with winter frost (Córdoba
sierras): hot summers (Dec–Feb), frosty winters (Jun–Aug). Months are 1–12.

The windows are guideline ranges, not hard dates - the frost card on the same
dashboard is the live check for the cold-sensitive crops (cucumber, corn).

For a northern-hemisphere site the whole calendar is shifted by six months
(`_shift`), so the same data serves both hemispheres; the endpoint picks the
shift from the site latitude.
"""
from __future__ import annotations

# Each crop: stable key, emoji, localized names, and month lists for direct
# sowing, transplanting (raised then planted out) and harvest. `frost_sensitive`
# flags crops that must wait until after the last frost.
CROPS: list[dict] = [
    {"key": "carrot", "emoji": "🥕", "de": "Möhren", "es": "Zanahoria", "en": "Carrot",
     "sow": [2, 3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 7, 11, 12, 1],
     "days": 75, "frost_sensitive": False,
     "note_de": "Direktsaat, gleichmäßig feucht halten; verträgt leichten Frost."},
    {"key": "arugula", "emoji": "🌿", "de": "Rucola", "es": "Rúcula", "en": "Arugula",
     "sow": [3, 4, 5, 8, 9, 10], "transplant": [], "harvest": [4, 5, 6, 9, 10, 11],
     "days": 35, "frost_sensitive": False,
     "note_de": "Schnellwüchsig; schießt bei Sommerhitze – Herbst/Frühjahr säen."},
    {"key": "lettuce", "emoji": "🥬", "de": "Salat", "es": "Lechuga", "en": "Lettuce",
     "sow": [2, 3, 4, 5, 8, 9, 10], "transplant": [3, 4, 5, 9, 10], "harvest": [4, 5, 6, 7, 10, 11, 12],
     "days": 60, "frost_sensitive": False,
     "note_de": "Im Hochsommer nur mit Schatten; schießt bei Hitze."},
    {"key": "celery", "emoji": "🥬", "de": "Sellerie (Apio)", "es": "Apio", "en": "Celery",
     "sow": [1, 2, 7, 8, 9], "transplant": [3, 9, 10, 11], "harvest": [5, 6, 12, 1, 2],
     "days": 140, "frost_sensitive": False,
     "note_de": "Lange Kultur; vorziehen und pikieren, gleichmäßig wässern."},
    {"key": "dill", "emoji": "🌱", "de": "Dill", "es": "Eneldo", "en": "Dill",
     "sow": [3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 11, 12],
     "days": 60, "frost_sensitive": False,
     "note_de": "Direktsaat am endgültigen Standort (verträgt Umpflanzen schlecht)."},
    {"key": "coriander", "emoji": "🌱", "de": "Koriander", "es": "Cilantro", "en": "Coriander",
     "sow": [3, 4, 5, 8, 9], "transplant": [], "harvest": [5, 6, 10, 11],
     "days": 50, "frost_sensitive": False,
     "note_de": "Schießt bei Hitze schnell; Herbst/Spätwinter säen."},
    {"key": "chard", "emoji": "🥬", "de": "Mangold (Acelga)", "es": "Acelga", "en": "Chard",
     "sow": [2, 3, 4, 5, 8, 9, 10], "transplant": [3, 4, 9, 10], "harvest": [5, 6, 7, 8, 11, 12, 1],
     "days": 60, "frost_sensitive": False,
     "note_de": "Sehr robust und frosthart; lange beerntbar (Blatt für Blatt)."},
    {"key": "leek", "emoji": "🧅", "de": "Porree", "es": "Puerro", "en": "Leek",
     "sow": [6, 7, 8, 9], "transplant": [9, 10, 11], "harvest": [2, 3, 4, 5, 6, 7],
     "days": 150, "frost_sensitive": False,
     "note_de": "Lange Kultur, sehr frosthart; vorziehen und tief pflanzen."},
    {"key": "spinach", "emoji": "🥬", "de": "Spinat", "es": "Espinaca", "en": "Spinach",
     "sow": [3, 4, 5, 6, 8], "transplant": [], "harvest": [5, 6, 7, 8, 9],
     "days": 50, "frost_sensitive": False,
     "note_de": "Kühle-Kultur, frosthart; schießt bei Hitze und langen Tagen."},
    {"key": "beet", "emoji": "🟣", "de": "Rote Beete", "es": "Remolacha", "en": "Beetroot",
     "sow": [2, 3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 11, 12, 1],
     "days": 70, "frost_sensitive": False,
     "note_de": "Direktsaat; verträgt leichten Frost, gut lagerfähig."},
    {"key": "cucumber", "emoji": "🥒", "de": "Gurken", "es": "Pepino", "en": "Cucumber",
     "sow": [9, 10, 11, 12], "transplant": [10, 11], "harvest": [12, 1, 2, 3],
     "days": 60, "frost_sensitive": True,
     "note_de": "Wärmebedürftig und frostempfindlich – erst nach dem letzten Frost."},
    {"key": "corn", "emoji": "🌽", "de": "Mais", "es": "Maíz", "en": "Corn",
     "sow": [9, 10, 11, 12], "transplant": [], "harvest": [1, 2, 3, 4],
     "days": 90, "frost_sensitive": True,
     "note_de": "Frostempfindlich; im Block pflanzen (Windbestäubung)."},
]


def _shift(months: list[int], by: int) -> list[int]:
    return sorted(((m - 1 + by) % 12) + 1 for m in months)


def calendar(hemisphere: str = "south") -> list[dict]:
    """Return the crop calendar, shifted by 6 months for a northern site."""
    by = 6 if hemisphere == "north" else 0
    out = []
    for c in CROPS:
        out.append({
            **c,
            "sow": _shift(c["sow"], by),
            "transplant": _shift(c["transplant"], by),
            "harvest": _shift(c["harvest"], by),
        })
    return out


def hemisphere_for(latitude: float | None) -> str:
    return "north" if (latitude is not None and latitude > 0) else "south"
