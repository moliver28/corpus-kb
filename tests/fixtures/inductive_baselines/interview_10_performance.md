# Interview P10 — SRE on-call

Pagers fire when the response lag passes two seconds. The slow path turns out
to be a cold cache after each deploy, and warmup jobs hide the real
performance numbers. We benchmarked throughput and found the bottleneck in
the serialization layer, not the database.

Weekly notes go out to the whole team after the review.
