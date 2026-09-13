"""Full-text acquisition (H1).

Legally acquired full texts only (principle 7). Every document carries an
explicit legal basis; the resolver is architecturally incapable of producing a
non-open-access candidate — there is no code path for credential replay,
Sci-Hub, or scraping. What cannot be reached through an open-access location is
honestly reported as *not retrieved*, which fills the lower half of the PRISMA
flow (reports sought for retrieval / reports not retrieved).
"""
