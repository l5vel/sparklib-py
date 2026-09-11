"""Re-run the capped queries across pages.

Discourse returns 50 topics a page and the first sweep never asked for page 2,
so half the queries had their tail silently truncated. Nothing in the response
says the result was cut; the only tell is a count that lands exactly on the cap.
"""
import collections, json, subprocess, sys, time

topics = json.load(open("cd_topics.json"))
counts = collections.Counter()
for v in topics.values():
    for q in v["queries"]:
        counts[q] += 1
capped = sorted(q for q, n in counts.items() if n >= 45)
before = set(topics)
MAX_PAGE = int(sys.argv[1]) if len(sys.argv) > 1 else 5

def get(url, tries=4):
    for i in range(tries):
        r = subprocess.run(["curl", "-sSL", "--max-time", "45", "-A", "Mozilla/5.0",
                            "-w", "\\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        if code.strip() == "200":
            try:
                return json.loads(body)
            except Exception:
                pass
        time.sleep(5 * (i + 1))
    return None

print(f"{len(capped)} capped queries, up to page {MAX_PAGE}", flush=True)
for q in capped:
    added = 0
    for page in range(2, MAX_PAGE + 1):
        d = get("https://www.chiefdelphi.com/search.json?q="
                + q.replace(" ", "%20") + f"&page={page}")
        if d is None:
            print(f"    page {page} FAILED  {q}", flush=True); break
        got = d.get("topics", [])
        for t in got:
            tid = str(t["id"])
            if tid not in topics:
                topics[tid] = {"title": t["title"], "posts": t.get("posts_count", 0),
                               "queries": []}
                added += 1
            if q not in topics[tid]["queries"]:
                topics[tid]["queries"].append(q)
        time.sleep(2.2)
        if len(got) < 50:
            break                      # short page means the tail is reached
    print(f"  +{added:>3} new   {q}", flush=True)

json.dump(topics, open("cd_topics.json", "w"), indent=1)
print(f"\n{len(topics)} topics total, {len(set(topics) - before)} new from pagination")
