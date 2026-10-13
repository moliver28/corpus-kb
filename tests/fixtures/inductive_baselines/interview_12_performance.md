# Interview P12 — Data engineer

Nightly batch throughput dropped after the schema change and nothing flagged
it. The slow jobs back up the queue, and cache invalidation makes the morning
latency worse before it gets better. Profile-guided tuning found the real
bottleneck; response times recovered once we fixed the skew.

Meeting notes from that sprint are archived with the project files.
