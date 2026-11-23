"""Science safety: what the lab will not help with, and what it asks you to confirm.

Chemistry and biology are dual-use. This layer is a RISK REDUCER, not a guarantee: word patterns can be bypassed, so it works together with tool-level
limits (there is no retrosynthesis, reaction prediction or sequence-design tool here, and connectors only read public reference databases), call budgets and
an audit trail. A real deployment that handles hazardous material needs institutional review as well.

Decisions
* block    asking for a way to MAKE, SCALE UP, ACQUIRE, WEAPONISE or ENHANCE a hazardous agent (chemical-weapon, toxin, select-agent/pathogen or explosive classes).
           Always explained; never silent.
* confirm  wet-lab procedure requests (culture, transfect, assay protocol...) that name a biological agent: a person must confirm before anything is done.
* allow    everything else, including education, hazard information, history, medicine, detection, decontamination and regulation.

The settings can only TIGHTEN this ("strict" turns every confirm into a block). There is no switch that turns it off.
No hazardous prompt is stored in this repository: tests use abstract, category-labelled inputs built from the same word lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LEVELS = ("standard", "strict")

# Classes of agent, by name. A name alone is never a problem (people ask about them for safety, history and medicine); it matters together with an "intent" word.
SUBJECTS: dict[str, tuple[str, ...]] = {
    "chemical_weapon": ("nerve agent", "sarin", "soman", "tabun", "novichok", r"\bvx\b", "mustard gas", "sulfur mustard", "nitrogen mustard weapon", "phosgene", "chlorine gas weapon", "chemical weapon", "blister agent"),
    "toxin": ("ricin", "botulinum", "abrin", "saxitoxin", "tetrodotoxin", "aflatoxin", "conotoxin", "staphylococcal enterotoxin", "biological toxin"),
    "pathogen": ("anthrax", "bacillus anthracis", "variola", "smallpox", "ebola", "marburg", "yersinia pestis", "plague bacterium", "francisella", "tularemia", "select agent", "pandemic pathogen",
                 "potential pandemic", "h5n1", "pathogen"),
    "explosive": ("explosive", r"\btatp\b", r"\bpetn\b", r"\brdx\b", r"\bhmx\b", "nitroglycerin", "detonator", "ammonium nitrate bomb", "pipe bomb", "improvised explosive"),
}
# Words that ask for a way to produce, obtain or strengthen something.
INTENT = (r"synthesi[sz]e", r"synthesis (route|of|for)", r"how (to|do i|can i) (make|produce|prepare|manufacture|cook|culture|grow|obtain|get|acquire|buy)", r"\b(manufacture|cultivate|brew)\b",
          r"scale[- ]?up", r"large[- ]scale", r"mass[- ]produc", r"weaponi[sz]", r"aerosoli[sz]|disperse|dissemination|deliver(y)? (system|method)", r"\bacquire|\bsource (of|for)|where to (buy|get)|without (a )?licen[cs]e|avoid detection|evade",
          r"increase (the )?(transmissibility|virulence|lethality|toxicity|stability|potency|yield)", r"enhanc\w+ (the )?(transmissibility|virulence|lethality|toxicity|stability|potency)",
          r"gain[- ]of[- ]function", r"make (it )?(more )?(contagious|lethal|deadly|resistant|transmissible)", r"step[- ]by[- ]step (procedure|protocol|instructions)", r"precursors? (for|to|of)|reagent list|reaction conditions (for|to)")
# Everyday wet-lab procedure words: with a biological agent they need a person to confirm.
WETLAB = (r"protocol", r"\btransfect", r"\bculture (medium|conditions)", r"\bplaque assay", r"\bpassag(e|ing)\b", r"\bcloning\b", r"\bcrispr (guide|design)", r"\bprimer design", r"\bviral vector", r"\bpurif(y|ication) (of )?(the )?(toxin|virus|protein)")
BIO_CLASSES = ("toxin", "pathogen")

_SUBJ = {k: re.compile("|".join(v), re.I) for k, v in SUBJECTS.items()}
_INTENT = re.compile("|".join(INTENT), re.I)
_WET = re.compile("|".join(WETLAB), re.I)
MAX_TEXT = 200_000

REASONS = {
    "chemical_weapon": "this asks for a way to make, obtain or use a chemical weapon agent",
    "toxin": "this asks for a way to make, obtain or use a biological toxin",
    "pathogen": "this asks for a way to produce, enhance or obtain a dangerous pathogen",
    "explosive": "this asks for a way to make or use an explosive",
}


@dataclass(frozen=True)
class Decision:
    action: str = "allow"                  # allow | confirm | block
    category: str = ""
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    def public(self) -> dict:
        return {"action": self.action, "category": self.category, "reason": self.reason,
                "help": "" if self.action == "allow" else "Education, hazard information, history, medicine, detection and decontamination are fine to ask about. Ways to make, obtain or strengthen a hazardous agent are not available here."}


ALLOW = Decision()


def screen(text: str, level: str = "standard") -> Decision:
    """Decide what to do with a request (or a piece of code). Never raises; an empty text is allowed."""
    if not isinstance(text, str) or not text.strip():
        return ALLOW
    t = text[:MAX_TEXT]
    subjects = [k for k, rx in _SUBJ.items() if rx.search(t)]
    if subjects and _INTENT.search(t):
        k = subjects[0]
        return Decision("block", k, "Blocked: " + REASONS[k] + ".")
    bio = [k for k in subjects if k in BIO_CLASSES]
    if bio and _WET.search(t):
        k = bio[0]
        if level == "strict":
            return Decision("block", "wetlab", "Blocked (strict safety level): a wet-lab procedure involving a biological hazard needs institutional review.")
        return Decision("confirm", "wetlab", "A person must confirm this: it asks for a wet-lab procedure involving a biological hazard.")
    return ALLOW


def tighten(level: str, wanted: str) -> str:
    """A level may only go up: standard -> strict. Asking for a lower one keeps the current level."""
    if level not in LEVELS or wanted not in LEVELS:
        raise ValueError(f"the safety level must be one of: {', '.join(LEVELS)}")
    return wanted if LEVELS.index(wanted) >= LEVELS.index(level) else level


POLICY = {
    "blocked": ["Ways to make, scale up, obtain, weaponise or enhance chemical-weapon agents, toxins, select agents / dangerous pathogens or explosives."],
    "confirm": ["Wet-lab procedures (culturing, transfection, assays, cloning) that name a toxin or pathogen: a person confirms first."],
    "allowed": ["Education, hazard and safety data, history, medicine and treatment, detection, decontamination and regulation.", "Searching public reference databases (read-only)."],
    "tools_not_offered": ["Retrosynthesis and reaction prediction", "Sequence or toxin design", "Anything that orders, buys or makes a physical substance"],
    "limits": ["Word patterns can be bypassed. This lowers risk; it does not remove it.", "The level can be tightened but not switched off.", "Hazardous work needs institutional review whatever this tool says."],
}
