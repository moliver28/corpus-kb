# Welcome to the sample knowledge corpus

This folder holds a tiny, general-purpose corpus for trying Corpus-KB.

The files are short on purpose. Each one exercises a different shape of
text: an FAQ, notes, a meeting record, and a glossary. Load them with one
click from the browser, or run `corpus-kb demo-kb` in a terminal.

## What the loader does

The loader ingests every markdown file in this folder through the normal
pipeline. Chunks, vectors, and entities are written to your database. The
receipt tells you which files went in and how many chunks each file made.

## What to try after loading

Open the **Ask** page and ask about reciprocal rank fusion. The glossary
file should supply the evidence. Ask about the meeting next: the record
holds the team's launch decisions. Every answer cites the file it came
from.

## Where this corpus lives

The folder is part of the repository checkout, under `docs/demo-kb`. An
installed wheel does not carry it. The loader says so honestly when the
folder is missing, and the fix is to run from a repository clone.
