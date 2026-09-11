"""Harvest Chief Delphi threads about SPARK failures via the Discourse JSON API."""
import json, subprocess, time, os

QUERIES = [
    "sparkflex", "spark flex problem", "spark max duplicate can id",
    "spark max can id reset", "spark can id 0", "sparkflex brownout",
    "spark max sticky fault", "can utilization 100 frc", "spark max unresponsive",
    "spark max not detected can", "sparkflex follower mode", "spark max motor type brushed",
    "spark max encoder fault", "spark flex gate driver fault", "spark max overheat",
    "can bus termination 120 ohm frc", "spark max burn flash", "spark max factory reset",
    "spark max firmware mismatch", "spark max intermittent can", "neo vortex problem",
    "spark max timeout waiting for status", "spark max can error frc",
]

def get(url):
    r = subprocess.run(["curl","-sSL","--max-time","45","-A","Mozilla/5.0",url],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return None

topics = {}
for q in QUERIES:
    d = get("https://www.chiefdelphi.com/search.json?q=" + q.replace(" ", "%20"))
    if not d:
        print(f"  search failed: {q}"); continue
    n = 0
    for t in d.get("topics", []):
        if t["id"] not in topics:
            topics[t["id"]] = {"title": t["title"], "posts": t.get("posts_count", 0),
                               "queries": []}
            n += 1
        topics[t["id"]]["queries"].append(q)
    print(f"  {len(d.get('topics', [])):>3} hits ({n:>2} new)  {q}")
    time.sleep(0.6)

json.dump(topics, open("cd_topics.json", "w"), indent=1)
print(f"\n{len(topics)} unique topics collected")
