# Where every claim came from

This repository makes a lot of specific assertions about hardware. Each traces to
one of four kinds of source, and the kind matters more than the claim, because
two of the four are strong and two are leads.

| grade | what it means | where it lives |
| --- | --- | --- |
| vendor | REV states it in a published specification, a header or their documentation | verbatim, in this directory |
| hardware | measured on a bus here, with the run written up | [../docs/runs/](../docs/runs/) |
| field report | someone on Chief Delphi reported it | cited by thread URL in [../docs/FIELD-REPORTS.md](../docs/FIELD-REPORTS.md) |
| inferred | taken from the neighbouring product or parameter and not checked | named as such in `sparklib/provenance.py` |

`sparklib/provenance.py` is the register. Every belief the driver rests on is a
row, graded, with its evidence and, for the open ones, the command that would
settle it. `spark verify` prints the open rows and exits non-zero, so a check
nobody has run shows up as work rather than as a pass.

## What is vendored here, and what is not

Vendored: REV's own specification files and the REVLib headers. They are here
because the tests read them, because they are small, and because a claim citing a
document you cannot open is one you cannot check. An earlier copy lived in a
session scratchpad and nearly went with it.

Not vendored: the Chief Delphi thread bodies, and REV's documentation site. Those
are other people's writing, and a mirror goes stale silently while the original
does not. Every finding drawn from them is cited by URL at the point it is used,
so you can read the original and judge it yourself.

[../docs/FIELD-REPORTS.md](../docs/FIELD-REPORTS.md) carries 158 findings, each
with its thread URL and a grade. Read the WITHDRAWN banner near the top: one
widely repeated claim about the current limit tapering with speed came from a
post whose own author retracted it, and it sits in the corpus dozens of times.

## Re-fetching the corpus

`harvest/` holds the scripts that built it. Chief Delphi blocks HTML scraping
while its Discourse JSON API is open, so the scripts use the API:

    https://www.chiefdelphi.com/search.json?q=<terms>
    https://www.chiefdelphi.com/t/<topic_id>.json

REV's documentation serves Markdown by appending `.md` to a page URL.

Run them, be polite about the rate, and point `SPARK_CD_CORPUS` at the result.
`tests/unit/test_spark_hardware_catalogue.py` then checks that every failure mode
in the catalogue traces to a thread that says so, and skips when the corpus is
absent, which is the normal state of a clone.

**Harvesting truncates silently, and this cost real time.** The search API caps
at 50 results. A thread fetch returns 20 posts while `post_stream.stream` lists
every id. A sitemap index nests. A self-imposed page budget drops pages with no
error at all. Six separate truncations turned up in one session, five of them
self-inflicted. Count what you got against what exists before concluding that
something is absent from the corpus.

That generalises. An adversarial pass over six "not in the corpus" conclusions
found all six wrong, every time because only part of the tree had been searched,
and in one case the unsearched part held REV's own spec files. A negative result
covers what you looked at and nothing else.
