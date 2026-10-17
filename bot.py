import json, os
import feedparser, requests

RSS = "https://news.google.com/rss/search?q=%22S%C3%A3o+Bernardo+do+Campo%22+when:1d&hl=pt-BR&gl=BR&ceid=BR:pt-419"
TOKEN = os.environ["WHAPI_TOKEN"]
CANAL = os.environ["CANAL_ID"]
ARQ = "postados.json"

primeira_vez = not os.path.exists(ARQ)
postados = set() if primeira_vez else set(json.load(open(ARQ)))

for e in reversed(feedparser.parse(RSS).entries[:10]):
    if e.link in postados:
        continue
    if primeira_vez:  # na 1a execucao so marca como visto, sem postar
        postados.add(e.link)
        continue
    r = requests.post(
        "https://gate.whapi.cloud/messages/text",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"to": CANAL, "body": f"📰 *{e.title}*\n\n{e.link}"},
        timeout=30,
    )
    print(r.status_code, e.title)
    if r.ok:
        postados.add(e.link)

json.dump(sorted(postados)[-500:], open(ARQ, "w"), indent=1)
