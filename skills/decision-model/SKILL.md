---
name: decision-model
group: Data
mcp:
- d1
description: 'Use the local d1 decision model (MCP "d1": decide, classify, yes_no, extract, score) for fast, calibrated routing, triage, moderation, classification, field extraction and judging instead of spending an LLM call. Use when a task is "pick one of N labels", "is X true?", "how urgent/risky/relevant is this?" or "pull these fields out of this text" - especially in bulk or as a guardrail before a bigger step.'
---

# Decision model (d1-2B, local)

d1 is **not a chat model**. It reads a *state* (text, JSON, or images) plus *named typed questions* and returns all
answers in **one forward pass**: no generated tokens, calibrated probabilities, milliseconds on a GPU, no API cost,
nothing leaves the machine. Tools (prefixed `d1_`): `decide`, `classify`, `yes_no`, `extract`, `score`, `status`.

If the tools are missing or return "not installed", tell the user to open **MCP servers > Decision model** and click
Install (about 6 GB, one-time). Do not try to install anything yourself.

## Use d1 when
- The answer is a **choice, a probability, a level or a field** (route a ticket, flag unsafe content, rank 50 search
  hits, check "does this text mention a refund?", read an amount/date/name from a document).
- You will ask the **same kind of question many times** (triage a queue, filter a corpus, judge candidates).
- You want a **cheap guardrail/gate** before an expensive step ("is this request in scope?").
- Several questions about the **same text** - ask them together in one `decide` call; extra questions are almost free.

## Use a normal LLM instead when
- You need prose, code, plans, summaries, rewrites, or step-by-step reasoning.
- The answer needs **world knowledge** or multi-hop reasoning (d1 is small; knowledge-heavy questions favour big models).
- The input is very long (answers get less reliable past ~64K tokens; the server refuses > 200K characters).
- The choice has stakes you cannot verify: use d1 to **pre-filter and prioritise**, then let a human or a stronger
  model confirm the borderline cases.

## Writing good questions
1. **One decision per question, phrased concretely.** "Is the customer asking for their money back?" beats "Is this
   about billing?". Put the *definition* in the question, not in your head.
2. **Describe every choice.** `classify` options as `{label: description}` ("billing": "charges, refunds, invoices").
   Labels alone work but are less accurate. Keep options mutually exclusive, or pass `multiple=true`.
3. **Scores need ordered levels, lowest first**, 2-10 of them, each a short label (`["low","medium","high"]`).
   Describe what each level means in the question if it is not obvious.
4. **Extraction: name the thing, give the type.** `extract` with `{"amount_due": {"type": "number", "instructions":
   "Total amount due"}, "due": {"type": "date", "instructions": "Payment deadline"}}`. A value with `found` < 0.5
   comes back `null` - treat it as "not in the text", do not guess.
5. **Put all facts in the state.** The model sees only what you pass. Include the relevant fields as JSON when the input
   is structured (`state` may be a dict).
6. **Batch related questions** in one `decide` call (up to 32) rather than many calls.
7. For counts/boxes in pictures pass `image_paths` (local files); use `number` with `range` for counts.

## Reading answers
- Probabilities are **calibrated but not magic**: choose thresholds on a few labelled examples of your own data
  (e.g. auto-handle if P >= 0.9, human review between 0.5 and 0.9). Report the probability, not just the label.
- `classify` returns `choice` + `confidence` + `probabilities`; low confidence or two close options means ambiguity -
  escalate instead of forcing a label.
- `yes_no` returns `probability` (P(yes)) and `answer` (>= 0.5).
- Every result has `meta`: device, dtype, `latency_ms`. The first call loads the model (`load_seconds`); later calls are
  warm. `{ok: false, error}` means a user/setup problem - read the message, fix the input, or fall back to the LLM.

## Example
Triage an email: one call, three answers.
`d1_decide(state=email_text, questions={"refund": {"type":"noul","instructions":"Is the sender asking for a refund?"},
"team": {"type":"choice","instructions":"Which team should handle this?","criteria":{"billing":"charges, refunds",
"shipping":"delivery, parcels","technical":"app or site faults"}}, "urgency": {"type":"score","instructions":"How
urgent is this?","criteria":["low","medium","high"]}})`

## Licence reminder (LFM Open License v1.0)
Free to use and modify; **commercial use is licensed only for organisations under US$10M annual revenue** (separate
agreement with Liquid AI above that). If the user's use may be commercial and the organisation is larger, say so
before building anything that depends on d1.
