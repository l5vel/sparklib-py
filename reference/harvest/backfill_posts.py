"""Recover the posts Discourse withheld on the first fetch.

/t/{id}.json returns only the first 20 posts, with no field saying so -- the same
shape of silent truncation as the search page cap. The complete id list is in
post_stream.stream, and bodies come from /t/{id}/posts.json?post_ids[]=...

Highest-value threads first: REV's own product threads run to 700+ posts and are
where vendor statements live, and we had 20 of them.
"""
import html, json, os, re, subprocess, sys, time

CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 240   # posts kept per thread
BATCH = 20

def clean(c):
    c = re.sub(r"<blockquote>.*?</blockquote>", " ", c, flags=re.S)
    c = re.sub(r"<aside.*?</aside>", " ", c, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c))).strip()

def get(url, tries=4):
    for i in range(tries):
        r = subprocess.run(["curl", "-sSL", "--max-time", "50", "-A", "Mozilla/5.0",
                            "-w", "\\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        if code.strip() == "200":
            try:
                return json.loads(body)
            except Exception:
                pass
        time.sleep(4 * (i + 1))
    return None

# which files are short of their declared post count, worst shortfall first
todo = []
for fn in os.listdir("threads"):
    t = open(f"threads/{fn}", errors="ignore").read()
    m = re.search(r"^POSTS: (\d+)", t, re.M)
    if not m:
        continue
    declared, got = int(m.group(1)), t.count("\n[")
    want = min(declared, CAP)
    if got < want - 2:
        todo.append((want - got, fn[:-4], declared, got))
todo.sort(reverse=True)
print(f"{len(todo)} threads short of their post count", flush=True)

done = added = 0
for _, tid, declared, got in todo:
    d = get(f"https://www.chiefdelphi.com/t/{tid}.json")
    if not d:
        continue
    stream = (d.get("post_stream") or {}).get("stream") or []
    have_ids = {p["id"] for p in (d.get("post_stream") or {}).get("posts") or []}
    rest = [i for i in stream[:CAP] if i not in have_ids]
    body = []
    for k in range(0, len(rest), BATCH):
        chunk = rest[k:k + BATCH]
        q = "&".join(f"post_ids[]={i}" for i in chunk)
        r = get(f"https://www.chiefdelphi.com/t/{tid}/posts.json?{q}")
        if not r:
            break
        for p in (r.get("post_stream") or {}).get("posts") or []:
            body.append(f"[{p.get('username')}] {clean(p.get('cooked',''))}\n")
        time.sleep(1.4)
    if body:
        with open(f"threads/{tid}.txt", "a") as fh:
            fh.write("\n".join(body) + "\n")
        added += len(body)
    done += 1
    if done % 10 == 0:
        print(f"  ... {done}/{len(todo)} threads, {added} posts recovered", flush=True)
print(f"DONE threads={done} posts_recovered={added}")
