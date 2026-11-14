# Sample meeting record

A fictional meeting, written as sample content for retrieval demos.
Names, teams, and decisions are invented. The format mirrors real
meeting notes: date, attendees, discussion, and decisions.

## Details

Date: 2026-09-14. Attendees: Priya (lead), Tomás (infra), Dana
(research), Ade (docs). Topic: the launch plan for the knowledge base.

## Discussion

Priya opened with the goal: every engineer should get cited answers
from the project corpus this quarter. Tomás reported the database is
healthy. One ingestion run hit a model outage last week; the degraded
chunks were repaired with the reembed command overnight.

Dana walked through the evaluation results. Recall at ten reached the
target on the internal golden set. She asked for two more labeled
questions per team before the next evaluation run. Ade demonstrated the
browser pages and collected feedback: the Ask page needs plainer error
messages when no evidence matches.

## Decisions

1. The launch date stays as planned for next month.
2. Each team labels two golden questions by the end of the sprint.
3. The Ask page gets friendlier empty states before launch.
4. Tracing stays off by default and turns on during launch week.

## Follow-ups

Tomás will automate the reembed repair step. Ade will draft the empty
state copy. Dana will circulate the golden-set template. Priya will
book the launch review.
