import json, os
import feedparser, requests
from googlenewsdecoder import gnewsdecoder

RSS = "https://news.google.com/rss/search?q=%22S%C3%A3o+Bernardo+do+Campo%22+when:1d&hl=pt-BR&gl=BR&ceid=BR:pt-419"
TOKEN = os.environ["WHAPI_TOKEN"]
CANAL = os.environ["CANAL_ID"]
ARQ = "postados.json"
MAX_POR_RODADA = 5


def link_real(url):
    """Troca o link gigante do Google News pelo link verdadeiro da materia."""
    try:
        r = gnewsdecoder(url, interval=1)
        if r.get("status") and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception as ex:
        print("nao deu pra decodificar:", ex)
    return url


def separa(e):
    """Separa o titulo do nome do portal."""
    titulo = e.title
    fonte = e.get("source", {}).get("title", "")
    if fonte and titulo.endswith(" - " + fonte):
        titulo = titulo[: -len(fonte) - 3]
    elif " - " in titulo:
        titulo, fonte = titulo.rsplit(" - ", 1)
    return titulo.strip(), fonte.strip()


primeira_vez = not os.path.exists(ARQ)
postados = set() if primeira_vez else set(json.load(open(ARQ)))
enviadas = 0

for e in reversed(feedparser.parse(RSS).entries[:10]):
    if e.link in postados:
        continue
    if primeira_vez:  # na 1a execucao so marca como visto
        postados.add(e.link)
        continue
    if enviadas >= MAX_POR_RODADA:
        break

    titulo, fonte = separa(e)
    msg = f"📰 *{titulo}*\n"
    if fonte:
        msg += f"🗞️ _{fonte}_\n"
    msg += f"\n{link_real(e.link)}"

    r = requests.post(
        "https://gate.whapi.cloud/messages/text",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"to": CANAL, "body": msg},
        timeout=30,
    )
    print(r.status_code, titulo)
    if r.ok:
        postados.add(e.link)
        enviadas += 1

json.dump(sorted(postados)[-500:], open(ARQ, "w"), indent=1)
