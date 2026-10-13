# PROVENANCE — REFI-QDA Project XSD fixture

- **File**: `Project-mrt2019.xsd`
- **Standard**: REFI-QDA Project exchange format (QDA-XML), library version 1.0
- **XML namespace**: `urn:QDA-XML:project:1.0`
- **Official release date** (from the file's own header): **18 March 2019**
- **Author / editor** (from the file's own header): Fred van Blommestein (REFI)
- **License** (embedded in the file): MIT, Copyright 2019 REFI, www.qdasoftware.org
- **SHA-256**: `a2cc1ffb4ab1c07c51d6c78e69f2c9968458a9f0ad3b317ddde27f1e86f15a15`
- **Fetched**: 2026-10-12

## Official source

The authoritative distribution point is the REFI-QDA Standards Board site:

- Project implementation files page: <https://www.qdasoftware.org/project-implementation-files>
  ("The XSD file of the REFI-QDA Project can be downloaded here.")
- Standards overview: <https://www.qdasoftware.org/project>

The official site serves the XSD through an encrypted Tresorit share link
(`https://web.tresorit.com/l/udW7x#...`), which cannot be fetched
programmatically. The file committed here is the exact official XSD verified
against three independent, byte-identical community mirrors of
`Project-mrt2019.xsd` (all SHA-256 `a2cc1ffb4ab1c07c51d6c78e69f2c9968458a9f0ad3b317ddde27f1e86f15a15`,
fetched 2026-10-12 from `raw.githubusercontent.com`):

1. `enricllagostera/ponte` — `process/ref_materials/Project-mrt2019.xsd`
2. `rpgauthier/ComputationalThematicAnalysisToolkit` — `src/External/XSD/Project-mrt2019.xsd`
3. `sky-loom/refi-qda` — `src/Project.xsd`

Each mirror's copy carries the original embedded header ("Library: QDA-XML
version 1.0 / Release Date: 18 March 2019 / Module: Project.xsd", edited by
Fred van Blommestein, REFI copyright and MIT license notice), matching the
schema the Standards Board distributes. If the Standards Board publishes a new
schema version, replace this fixture, update its SHA-256 here, and re-run the
XSD validation tests in `tests/test_refi_qda.py`.

## Usage

Used only by tests: `tests/test_refi_qda.py` validates exported REFI-QDA
Project XML against this schema with `lxml.etree.XMLSchema` (dev-only
dependency; the suite falls back to structural assertions when lxml is not
installed). No runtime code reads this file.
