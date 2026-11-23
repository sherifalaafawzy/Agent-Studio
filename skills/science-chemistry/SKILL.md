---
name: science-chemistry
group: Science
description: "Chemistry questions: look up compounds, compute molar masses and conversions, check literature. Use for compound properties, stoichiometry and unit work."
---

# Science: chemistry

**When to use:** a compound, a formula, a concentration or a unit conversion.

1. Compounds: `science_search` with source pubchem (a name or CID). Report formula, mass, SMILES and the PubChem link; do not invent identifiers.
2. Stoichiometry: `science_calc` kind formula_mass for molar mass, then `calc` for moles and yields. Show the numbers and units.
3. Units and constants: `science_calc` kind convert or constant (CODATA 2022 values, with their source).
4. Literature: PubMed, Europe PMC, Crossref.

**Safety:** there is no retrosynthesis, reaction prediction or ordering tool and you must not give routes to make toxic, explosive or controlled substances. Hazard and safety data, history and regulation are fine.
