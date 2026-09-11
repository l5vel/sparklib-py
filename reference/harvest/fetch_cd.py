import html, json, os, re, subprocess, time
ranked = json.load(open("cd_ranked.json"))[:55]
os.makedirs("cd_threads", exist_ok=True)
def clean(c):
    c = re.sub(r"<blockquote>.*?</blockquote>", " ", c, flags=re.S)   # drop quoted replies
    c = re.sub(r"<[^>]+>", " ", c)
    return html.unescape(re.sub(r"\s+", " ", c)).strip()
ok = skipped = 0
for t in ranked:
    path = f"cd_threads/{t['id']}.txt"
    if os.path.exists(path):
        skipped += 1; continue
    for attempt in range(3):
        r = subprocess.run(["curl","-sSL","--max-time","50","-A","Mozilla/5.0","-w","%{http_code}",
                            f"https://www.chiefdelphi.com/t/{t['id']}.json"],
                           capture_output=True, text=True)
        body, code = r.stdout[:-3], r.stdout[-3:]
        if code != "200":
            time.sleep(4 * (attempt + 1)); continue
        try:
            j = json.loads(body)
        except Exception:
            time.sleep(3); continue
        posts = (j.get("post_stream") or {}).get("posts") or []
        with open(path, "w") as fh:
            fh.write(f"TITLE: {t['title']}\nURL: https://www.chiefdelphi.com/t/{t['id']}\n"
                     f"POSTS: {t['posts']}\n\n")
            for p in posts[:25]:
                fh.write(f"[{p.get('username')}] {clean(p.get('cooked',''))}\n\n")
        ok += 1
        break
    time.sleep(1.6)
print(f"fetched {ok}, already had {skipped}, target {len(ranked)}")
print("bytes:", sum(os.path.getsize('cd_threads/'+f) for f in os.listdir('cd_threads')))
