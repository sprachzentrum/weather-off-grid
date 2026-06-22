"""
Vegetable-garden + reforestation planting calendar.

Curated sow / transplant / harvest windows for the crops and native trees grown
at El Durazno, tuned to a temperate **southern-hemisphere** climate with winter
frost (Córdoba sierras): hot summers (Dec–Feb), frosty winters (Jun–Aug).
Months are 1–12.

Sources: sowing windows for the listed vegetables are aligned, where available,
with the local government calendar "La Huerta en tu Hogar" (Gobierno de Córdoba,
Ministerio de Agricultura y Ganadería) and INTA guidance. Crops not covered
there (and all the native trees) use our own guideline values for the climate.

Two categories:
  * "vegetable" - sow / transplant / harvest as usual.
  * "tree"      - native species for reforestation. Here the "harvest" months
                  mean *seed collection*, and "transplant" means planting the
                  raised seedling out (best at the start of the spring rains).

The windows are guideline ranges, not hard dates - the frost card on the same
dashboard is the live check for the cold-sensitive crops.

For a northern-hemisphere site the whole calendar is shifted by six months
(`_shift`), so the same data serves both hemispheres; the endpoint picks the
shift from the site latitude.
"""
from __future__ import annotations

# Each entry: stable key, category, emoji, localized names, and month lists for
# direct sowing, transplanting and harvest. `frost_sensitive` flags crops that
# must wait until after the last frost. `days` is approximate days to harvest
# (None for trees).
CROPS: list[dict] = [
    # ── Vegetables ──────────────────────────────────────────────────────────
    {"key": "carrot", "category": "vegetable", "emoji": "🥕", "de": "Möhren", "es": "Zanahoria", "en": "Carrot",
     "sow": [2, 3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 7, 11, 12, 1],
     "days": 75, "frost_sensitive": False,
     "note_de": "Direktsaat, gleichmäßig feucht halten; verträgt leichten Frost."},
    {"key": "arugula", "category": "vegetable", "emoji": "🌿", "de": "Rucola", "es": "Rúcula", "en": "Arugula",
     "sow": [3, 4, 5, 8, 9, 10], "transplant": [], "harvest": [4, 5, 6, 9, 10, 11],
     "days": 35, "frost_sensitive": False,
     "note_de": "Schnellwüchsig; schießt bei Sommerhitze – Herbst/Frühjahr säen."},
    {"key": "lettuce", "category": "vegetable", "emoji": "🥬", "de": "Salat", "es": "Lechuga", "en": "Lettuce",
     "sow": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "transplant": [3, 4, 5, 9, 10], "harvest": [4, 5, 6, 7, 10, 11, 12],
     "days": 60, "frost_sensitive": False,
     "note_de": "Ganzjährig (Córdoba); im Hochsommer nur mit Schatten, schießt bei Hitze."},
    {"key": "celery", "category": "vegetable", "emoji": "🥬", "de": "Sellerie (Apio)", "es": "Apio", "en": "Celery",
     "sow": [1, 2, 7, 8, 9], "transplant": [3, 9, 10, 11], "harvest": [5, 6, 12, 1, 2],
     "days": 140, "frost_sensitive": False,
     "note_de": "Lange Kultur; vorziehen und pikieren, gleichmäßig wässern."},
    {"key": "dill", "category": "vegetable", "emoji": "🌱", "de": "Dill", "es": "Eneldo", "en": "Dill",
     "sow": [3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 11, 12],
     "days": 60, "frost_sensitive": False,
     "note_de": "Direktsaat am endgültigen Standort (verträgt Umpflanzen schlecht)."},
    {"key": "coriander", "category": "vegetable", "emoji": "🌱", "de": "Koriander", "es": "Cilantro", "en": "Coriander",
     "sow": [3, 4, 5, 8, 9], "transplant": [], "harvest": [5, 6, 10, 11],
     "days": 50, "frost_sensitive": False,
     "note_de": "Schießt bei Hitze schnell; Herbst/Spätwinter säen."},
    {"key": "chard", "category": "vegetable", "emoji": "🥬", "de": "Mangold (Acelga)", "es": "Acelga", "en": "Chard",
     "sow": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "transplant": [], "harvest": [5, 6, 7, 8, 11, 12, 1],
     "days": 60, "frost_sensitive": False,
     "note_de": "Ganzjährig (Córdoba); sehr robust und frosthart, lange beerntbar."},
    {"key": "leek", "category": "vegetable", "emoji": "🧅", "de": "Porree", "es": "Puerro", "en": "Leek",
     "sow": [6, 7, 8, 9], "transplant": [9, 10, 11], "harvest": [2, 3, 4, 5, 6, 7],
     "days": 150, "frost_sensitive": False,
     "note_de": "Lange Kultur, sehr frosthart; vorziehen und tief pflanzen."},
    {"key": "spinach", "category": "vegetable", "emoji": "🥬", "de": "Spinat", "es": "Espinaca", "en": "Spinach",
     "sow": [3, 4, 5, 6, 8], "transplant": [], "harvest": [5, 6, 7, 8, 9],
     "days": 50, "frost_sensitive": False,
     "note_de": "Kühle-Kultur, frosthart; schießt bei Hitze und langen Tagen."},
    {"key": "beet", "category": "vegetable", "emoji": "🟣", "de": "Rote Beete", "es": "Remolacha", "en": "Beetroot",
     "sow": [2, 3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 11, 12, 1],
     "days": 70, "frost_sensitive": False,
     "note_de": "Direktsaat; verträgt leichten Frost, gut lagerfähig."},
    {"key": "peas", "category": "vegetable", "emoji": "🫛", "de": "Erbsen", "es": "Arvejas", "en": "Peas",
     "sow": [4, 5, 6, 7], "transplant": [], "harvest": [8, 9, 10, 11],
     "days": 75, "frost_sensitive": False,
     "note_de": "Apr–Jul (Córdoba); frosthart, Rankhilfe geben."},
    {"key": "tomato", "category": "vegetable", "emoji": "🍅", "de": "Tomaten", "es": "Tomate", "en": "Tomato",
     "sow": [8, 9], "transplant": [10, 11], "harvest": [12, 1, 2, 3],
     "days": 100, "frost_sensitive": True,
     "note_de": "Almácigo Aug–Sep, Auspflanzen Okt–Nov (Córdoba); ausgeizen, stützen."},
    {"key": "cucumber", "category": "vegetable", "emoji": "🥒", "de": "Gurken", "es": "Pepino", "en": "Cucumber",
     "sow": [9, 10, 11, 12], "transplant": [10, 11], "harvest": [12, 1, 2, 3],
     "days": 60, "frost_sensitive": True,
     "note_de": "Wärmebedürftig und frostempfindlich – erst nach dem letzten Frost."},
    {"key": "pumpkin", "category": "vegetable", "emoji": "🎃", "de": "Kürbis (Calabaza)", "es": "Calabaza", "en": "Pumpkin",
     "sow": [9, 10, 11], "transplant": [], "harvest": [3, 4, 5],
     "days": 110, "frost_sensitive": True,
     "note_de": "Sep–Nov (Córdoba, „zapallo calabacita“); viel Platz, vor dem ersten Frost ernten."},
    {"key": "hokkaido", "category": "vegetable", "emoji": "🎃", "de": "Hokkaido-Kürbis", "es": "Zapallo Hokkaido", "en": "Hokkaido squash",
     "sow": [9, 10, 11], "transplant": [10, 11], "harvest": [3, 4, 5],
     "days": 100, "frost_sensitive": True,
     "note_de": "Speisekürbis; reif wenn die Schale hart ist, vor dem ersten Frost ernten."},
    {"key": "watermelon", "category": "vegetable", "emoji": "🍉", "de": "Wassermelone", "es": "Sandía", "en": "Watermelon",
     "sow": [9, 10, 11], "transplant": [10, 11], "harvest": [1, 2, 3],
     "days": 90, "frost_sensitive": True,
     "note_de": "Braucht viel Wärme und Sonne; warmen Boden abwarten."},
    {"key": "corn", "category": "vegetable", "emoji": "🌽", "de": "Mais", "es": "Maíz", "en": "Corn",
     "sow": [10, 11, 12], "transplant": [], "harvest": [2, 3, 4],
     "days": 90, "frost_sensitive": True,
     "note_de": "Okt–Dez (Córdoba); frostempfindlich, im Block pflanzen (Windbestäubung)."},
    {"key": "onion", "category": "vegetable", "emoji": "🧅", "de": "Zwiebeln", "es": "Cebolla", "en": "Onion",
     "sow": [2, 3], "transplant": [4, 5], "harvest": [11, 12, 1],
     "days": 150, "frost_sensitive": False,
     "note_de": "Almácigo Feb, Auspflanzen Apr (Córdoba); frosthart."},
    {"key": "garlic", "category": "vegetable", "emoji": "🧄", "de": "Knoblauch", "es": "Ajo", "en": "Garlic",
     "sow": [2, 3, 4], "transplant": [], "harvest": [11, 12],
     "days": 210, "frost_sensitive": False,
     "note_de": "Zehen Feb–Apr stecken (Córdoba); wurzelt vor dem Winter ein, frosthart."},
    {"key": "potato", "category": "vegetable", "emoji": "🥔", "de": "Kartoffeln", "es": "Papa", "en": "Potato",
     "sow": [1, 2, 8, 9], "transplant": [], "harvest": [4, 5, 12, 1],
     "days": 110, "frost_sensitive": True,
     "note_de": "Knollen nach dem Frost legen (Frühjahr + Spätsommer); das Laub ist frostempfindlich."},
    {"key": "pepper", "category": "vegetable", "emoji": "🫑", "de": "Paprika/Chili", "es": "Pimiento/Ají", "en": "Pepper",
     "sow": [7, 8], "transplant": [10], "harvest": [1, 2, 3, 4],
     "days": 120, "frost_sensitive": True,
     "note_de": "Almácigo Jul–Aug, Auspflanzen Okt (Córdoba); braucht Wärme."},
    {"key": "eggplant", "category": "vegetable", "emoji": "🍆", "de": "Aubergine", "es": "Berenjena", "en": "Eggplant",
     "sow": [7, 8], "transplant": [9, 10], "harvest": [1, 2, 3, 4],
     "days": 120, "frost_sensitive": True,
     "note_de": "Almácigo Jul–Aug, Auspflanzen Sep–Okt (Córdoba); wärmebedürftig."},
    {"key": "beans", "category": "vegetable", "emoji": "🫘", "de": "Bohnen", "es": "Poroto/Chaucha", "en": "Beans",
     "sow": [1, 10, 11, 12], "transplant": [], "harvest": [12, 1, 2, 3],
     "days": 70, "frost_sensitive": True,
     "note_de": "Jan + Okt–Dez (Córdoba); Direktsaat nach dem Frost, Busch-/Stangenbohne."},
    {"key": "zucchini", "category": "vegetable", "emoji": "🥒", "de": "Zucchini (Zapallito)", "es": "Zapallito", "en": "Zucchini",
     "sow": [10, 11, 12], "transplant": [], "harvest": [12, 1, 2, 3],
     "days": 55, "frost_sensitive": True,
     "note_de": "Okt–Dez (Córdoba); schnell und ertragreich, regelmäßig ernten."},
    {"key": "broccoli", "category": "vegetable", "emoji": "🥦", "de": "Brokkoli", "es": "Brócoli", "en": "Broccoli",
     "sow": [1, 2, 3, 12], "transplant": [2, 3, 4], "harvest": [5, 6, 7, 8],
     "days": 90, "frost_sensitive": False,
     "note_de": "Kühle-Kultur; vorziehen, im Herbst auspflanzen, frosthart."},
    {"key": "cauliflower", "category": "vegetable", "emoji": "🥬", "de": "Blumenkohl", "es": "Coliflor", "en": "Cauliflower",
     "sow": [1, 2, 3], "transplant": [2, 3, 4], "harvest": [5, 6, 7, 8],
     "days": 100, "frost_sensitive": False,
     "note_de": "Wie Brokkoli; gleichmäßig wässern, frosthart."},
    {"key": "brussels_sprouts", "category": "vegetable", "emoji": "🥬", "de": "Rosenkohl", "es": "Repollito de Bruselas", "en": "Brussels sprouts",
     "sow": [1, 2, 3], "transplant": [2, 3, 4], "harvest": [6, 7, 8, 9],
     "days": 150, "frost_sensitive": False,
     "note_de": "Lange Kultur; vorziehen, im Herbst auspflanzen. Frost verbessert den Geschmack."},
    {"key": "kale", "category": "vegetable", "emoji": "🥬", "de": "Grünkohl", "es": "Col rizada (Kale)", "en": "Kale",
     "sow": [1, 2, 3, 12], "transplant": [2, 3, 4], "harvest": [5, 6, 7, 8, 9],
     "days": 90, "frost_sensitive": False,
     "note_de": "Sehr frosthart; Frost macht ihn süßer, lange Blatt für Blatt beerntbar."},
    {"key": "radish", "category": "vegetable", "emoji": "🔴", "de": "Radieschen", "es": "Rabanito", "en": "Radish",
     "sow": [3, 4, 5, 8, 9, 10], "transplant": [], "harvest": [4, 5, 6, 9, 10, 11],
     "days": 30, "frost_sensitive": False,
     "note_de": "Sehr schnell; Herbst/Frühjahr, im Hochsommer scharf und holzig."},
    {"key": "parsley", "category": "vegetable", "emoji": "🍃", "de": "Petersilie", "es": "Perejil", "en": "Parsley",
     "sow": [2, 3, 4, 8, 9, 10], "transplant": [], "harvest": [5, 6, 7, 11, 12],
     "days": 80, "frost_sensitive": False,
     "note_de": "Keimt langsam; robust und frosthart, lange beerntbar."},
    # ── Reforestation / native trees (harvest months = seed collection) ──────
    {"key": "espinillo", "category": "tree", "emoji": "🌳", "de": "Espinillo", "es": "Espinillo (Vachellia caven)", "en": "Espinillo",
     "sow": [8, 9, 10], "transplant": [10, 11, 12], "harvest": [12, 1, 2],
     "days": None, "frost_sensitive": False,
     "note_de": "Heimischer Pionierbaum (bindet Stickstoff); Samen aus reifen Hülsen, vor der Saat anrauen/einweichen."},
    {"key": "coco", "category": "tree", "emoji": "🌳", "de": "Coco", "es": "Coco (Zanthoxylum coco)", "en": "Coco",
     "sow": [8, 9, 10], "transplant": [10, 11, 12], "harvest": [1, 2, 3],
     "days": None, "frost_sensitive": False,
     "note_de": "Heimischer Sierra-Baum; Anzucht im Frühjahr, zum Beginn der Frühregen auspflanzen."},
    {"key": "molle", "category": "tree", "emoji": "🌳", "de": "Molle", "es": "Molle (Lithraea molleoides)", "en": "Molle",
     "sow": [8, 9, 10], "transplant": [10, 11, 12], "harvest": [3, 4, 5],
     "days": None, "frost_sensitive": False,
     "note_de": "Heimisch und trockenheitsfest; Früchte im Herbst, Samen daraus gewinnen (Achtung: kann Hautreizungen auslösen)."},
    {"key": "aguaribay", "category": "tree", "emoji": "🌳", "de": "Aguaribay", "es": "Aguaribay (Schinus areira)", "en": "Aguaribay (pepper tree)",
     "sow": [8, 9, 10], "transplant": [10, 11, 12], "harvest": [3, 4, 5],
     "days": None, "frost_sensitive": False,
     "note_de": "Heimischer Pfefferbaum; schnellwüchsig, sehr trockenheitstolerant, gut zur Erosionssicherung."},
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
