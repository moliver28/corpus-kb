# Codebook Rules

A codebook is a list of codes. Each code carries the six MacQueen
fields (MacQueen, McLellan, Kay, and Milstein, 1998). The engine's
`code_registry` stores all six, and `codify load-codebook` validates
them, so write all six for every code.

## The six fields

| Field | Question it answers | Engine column |
|---|---|---|
| Code name | What is this code called? One short label. | `name` |
| Brief definition | What does it mean in one sentence? | `brief_definition` |
| Full definition | What does it mean in full, with its edge cases? | `full_definition` |
| Inclusion criteria | When does a chunk get this code? | `inclusion_criteria` |
| Exclusion criteria | When does it not, even if it looks close? | `exclusion_criteria` |
| Examples | What does a clean hit look like? Real quotes when possible. | `examples` |

Rules of thumb:

- Inclusion and exclusion criteria decide the hard cases. A code with
  no exclusion criteria will swallow the corpus.
- One idea per code. If a definition needs "and", split the code.
- Keep names short and distinct. The coder sees the name beside the
  definition; confusing names produce confusing decisions.
- Examples teach the coder the register of the corpus, not just the
  code. Quote participants when you can.

## The guided interview

Build the codebook with the researcher, one code at a time. For each
proposed code ask, in order:

1. "What do you call this?" (name)
2. "Say what it means in one sentence." (brief definition)
3. "Where does it get unclear or arguable?" (full definition)
4. "What must a chunk say for this code to apply?" (inclusion)
5. "What looks close but is not it?" (exclusion)
6. "Read me a line from the corpus that is exactly this." (example)

Stop after each code and read the six fields back. A field the
researcher cannot answer is a code that is not ready; park it, do not
guess it. When the list feels done, ask what is missing, then stop.

## File shape

The codebook JSON holds a `codes` list; each code object uses the six
field names above (`name`, `brief_definition`, `full_definition`,
`inclusion_criteria`, `exclusion_criteria`, `examples`). Optional keys
the engine accepts: `code_id` (stable id; the engine can mint one),
`actor_scope`, and `is_provisional`.

A minimal one-code book:

```json
{
  "codes": [
    {
      "code_id": "trust-vendor",
      "name": "Trust in vendor",
      "brief_definition": "The participant expects the vendor to deliver.",
      "full_definition": "Statements that express, question, or withdraw confidence that the vendor will meet its commitments.",
      "inclusion_criteria": "Mentions of expected delivery, promises, track record, or confidence in the vendor's team.",
      "exclusion_criteria": "General praise of the product that does not touch delivery or the relationship.",
      "examples": ["They have never missed a release date for us."]
    }
  ]
}
```

## Worked example

A researcher studies interviews about a software vendor. In an early
read, three chunks mention missed deadlines, one mentions a strong
sales relationship, and one praises the product's speed.

- "Missed deadlines" becomes a code: inclusion covers schedule
  commitments and their breaches; exclusion covers product quality
  complaints that never mention time.
- "Strong sales relationship" becomes a different code, not a second
  name for trust: the inclusion criteria talk about people and
  responsiveness, and the exclusion criteria fence out delivery
  promises, which belong to the first code.
- "Praises the product's speed" matches neither code. The researcher
  parks it as a possible new code after the inductive pass, rather
  than stretching an existing code to catch it.

The loaded book answers with a version id. Every later stage (pool,
cells, code, reliability, export) hangs off that version id.
