---
description: Execute accept/override decisions on the review queue
---
# corpus-review — execute review decisions

Record one human decision per CodingAssignment; the event lands through the
projection so weights and audits see it.

- Confirm the model's assignment:

  ```
  corpus-kb review accept <assignment-id> --reviewer <you> [--note ...]
  ```

- Overrule it (link corrections describe the change):

  ```
  corpus-kb review override <assignment-id> --reviewer <you> --note "<fix>"
  ```

- All flags: `corpus-kb review accept --help`
