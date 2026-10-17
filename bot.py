import calendar, json, os, time
from urllib.parse import parse_qs, urlparse
import feedparser, requests

# Bing News: os links ja trazem o endereco verdadeiro da materia dentro deles
RSS = "https://www.bing.com/news/search?q=%22S%C3%A3o+Bernardo+do+Campo%22&format=rss&setmkt=pt-BR"
TOKEN = os.environ["WHAPI_TOKEN"]
CANAL = os.environ["CANAL_ID"]
ARQ = "postados.json"
MAX_POR_RODADA = 5
IDADE_MAX_HORAS = 48


def link_real(url):
    """Extrai o link verdadeiro da materia de dentro do link do Bing."""
    try:
        qs = parse_qs(urlparse(url).query)
        if qs.get("url"):
            return qs["url"][0]
    except Exception as ex:
        print("nao deu pra extrair o link:", ex)
    return url


def fonte_do(url):
    nome = urlparse(url).netloc.lower()
    return nome.removeprefix("www.")


def recente(e):
    t = e.get("published_parsed")
    if not t:
        return True
    return time.time() - calendar.timegm(t) < IDADE_MAX_HORAS * 3600


primeira_vez = not os.path.exists(ARQ)
postados = set() if primeira_vez else set(json.load(open(ARQ)))
enviadas = 0

entradas = feedparser.parse(RSS).entries[:15]
print("noticias no feed:", len(entradas))

for e in reversed(entradas):
    link = link_real(e.link)
    if link in postados or not recente(e):
        continue
    if primeira_vez:  # na 1a execucao so marca como visto
        postados.add(link)
        continue
    if enviadas >= MAX_POR_RODADA:
        break

    titulo = e.title.strip()
    fonte = fonte_do(link)
    msg = f"📰 *{titulo}*\n"
    if fonte:
        msg += f"🗞️ _{fonte}_\n"
    msg += f"\n{link}"

    r = requests.post(
        "https://gate.whapi.cloud/messages/text",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"to": CANAL, "body": msg},
        timeout=30,
    )
    print(r.status_code, titulo, "->", link)
    if r.ok:
        postados.add(link)
        enviadas += 1

json.dump(sorted(postados)[-500:], open(ARQ, "w"), indent=1)
