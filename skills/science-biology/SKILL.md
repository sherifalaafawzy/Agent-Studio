---
name: science-biology
group: Science
description: "Biology questions: proteins, structures, sequences and papers from UniProt, AlphaFold, RCSB PDB and PubMed. Use for gene/protein lookups and structure questions."
---

# Science: biology

**When to use:** a gene, protein, structure or biomedical literature question.

1. Proteins: `science_search` with uniprot (search or lookup an accession). Report accession, gene, organism and length.
2. Predicted structures: alphafold lookup by UniProt accession. State the mean pLDDT and that it is a prediction, not an experiment. Experimental structures: rcsb.
3. Sequences you are given: `science_calc` kind sequence (length, GC, translation). Counting and translating only.
4. Literature: PubMed and Europe PMC with citation checks.

**Safety:** no sequence design, no enhancement of pathogens or toxins, no wet-lab protocols for hazardous agents (a person must confirm even benign-looking ones that name a toxin or pathogen). Clinical content carries a "not medical advice" note.
