import json, re
topics = json.load(open("cd_topics.json"))
# Weight titles that name a concrete SPARK/CAN failure over generic chatter.
STRONG = {"spark": 3, "flex": 2, "vortex": 2, "can id": 5, "duplicate": 5,
          "brownout": 5, "sticky": 4, "fault": 3, "unresponsive": 5,
          "not detected": 4, "disconnect": 4, "intermittent": 5, "utilization": 4,
          "termination": 4, "burn flash": 5, "factory reset": 4, "firmware": 3,
          "follower": 4, "brushed": 4, "encoder": 3, "gate driver": 5,
          "overheat": 4, "reboot": 4, "led": 3, "magenta": 4, "blink": 3,
          "lost": 4, "config": 3, "error": 2, "timeout": 4, "kff": 4,
          "no signal": 4, "dead": 3, "stopped working": 4, "reset": 3}
NOISE = {"pathplanner", "photonvision", "limelight", "kickoff", "scouting",
         "auction", "sale", "wanted", "chairman", "award", "welcome", "reveal"}
scored = []
for tid, t in topics.items():
    title = t["title"].lower()
    if any(n in title for n in NOISE):
        continue
    score = sum(w for k, w in STRONG.items() if k in title)
    if not score:
        continue
    score += min(t.get("posts", 0), 20) * 0.3
    score += len(set(t.get("queries", []))) * 1.5
    scored.append((score, int(tid), t["title"], t.get("posts", 0)))
scored.sort(reverse=True)
json.dump([{"id": i, "title": ti, "posts": p, "score": round(s, 1)}
           for s, i, ti, p in scored], open("cd_ranked.json", "w"), indent=1)
print(f"{len(scored)} scored topics; top 45:\n")
for s, i, ti, p in scored[:45]:
    print(f"  {s:5.1f}  [{p:>3}p] {ti[:88]}")
