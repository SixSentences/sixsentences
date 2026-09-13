"""Claim-support firewall (docs/QUALITY.md layer 2).

"The link works" is not "the source supports the claim": deep-research agents
hold >94% link validity but only 39-77% factual support. So every claim the
system surfaces is checked for ENTAILMENT against its cited evidence — not just
for the presence of a quote. The entailment engine is swappable behind
`EntailmentChecker`: the default uses a cheap LLM (provider-agnostic, no extra
dependency); a dedicated NLI model (MiniCheck / DeBERTa-v3) is a later drop-in
for cost and latency at scale. Abstention is first-class: an unsupported claim
is flagged, never silently shown as backed.
"""
