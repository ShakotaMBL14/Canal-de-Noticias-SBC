import os, time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import feedparser, requests
from dotenv import load_dotenv
from googlenewsdecoder import gnewsdecoder
from selectolax.parser import HTMLParser
from supabase import create_client, Client

load_dotenv()

RSS = "https://news.google.com/rss/search?q=%22S%C3%A3o+Bernardo+do+Campo%22+when:1d&hl=pt-BR&gl=BR&ceid=BR:pt-419"
TOKEN = os.getenv("WHAPI_TOKEN")
CANAL = os.getenv("CANAL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
MAX_POR_RODADA = 5
SIMILARIDADE_LIMIAR = 0.70  # Ignora se 70% ou mais do título for similar a uma já postada

if not TOKEN or not CANAL:
    raise ValueError("Erro: WHAPI_TOKEN e CANAL_ID precisam ser definidos no .env ou nas secrets.")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Erro: SUPABASE_URL e SUPABASE_KEY precisam ser definidos no .env ou nas secrets.")

# Conexão com o Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def formata_data_brasilia(entry):
    """Converte a data de publicação do RSS para o Horário de Brasília (UTC-3)."""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt_utc = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            fuso_brasilia = timezone(timedelta(hours=-3))
            dt_br = dt_utc.astimezone(fuso_brasilia)
            return dt_br.strftime("%d/%m/%Y às %H:%M")
    except Exception as ex:
        print("AVISO DATA:", ex)
    return None


def eh_similar(titulo_novo, titulos_existentes, limiar=SIMILARIDADE_LIMIAR):
    """Verifica se o título é muito similar a alguma matéria já postada recentemente."""
    t1 = titulo_novo.lower().strip()
    for t2_original in titulos_existentes:
        t2 = t2_original.lower().strip()
        score = SequenceMatcher(None, t1, t2).ratio()
        if score >= limiar:
            return True, t2_original, score
    return False, None, 0.0


def link_real(url):
    """Tenta trocar o link gigante do Google News pelo link verdadeiro."""
    try:
        r = gnewsdecoder(url, interval=1)
        # O decoder pode retornar 'status': True ou 'success': True
        if (r.get("status") or r.get("success")) and r.get("decoded_url"):
            return r["decoded_url"]
        print("DECODER FALHOU:", r)
    except Exception as ex:
        print("DECODER ERRO:", ex)
    return url


def extrair_imagem(url):
    """Acessa a página da notícia e extrai a imagem principal (og:image)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.ok:
            tree = HTMLParser(resp.text)
            tag = tree.css_first('meta[property="og:image"], meta[name="og:image"], meta[name="twitter:image"]')
            if tag and tag.attributes.get("content"):
                img = tag.attributes["content"]
                if img.startswith("http"):
                    return img
    except Exception as ex:
        print("AVISO IMAGEM:", ex)
    return None


def separa(e):
    """Separa o título do nome do portal."""
    titulo = e.title
    fonte = e.get("source", {}).get("title", "")
    if fonte and titulo.endswith(" - " + fonte):
        titulo = titulo[: -len(fonte) - 3]
    elif " - " in titulo:
        titulo, fonte = titulo.rsplit(" - ", 1)
    return titulo.strip(), fonte.strip()


def enviar_noticia(canal, msg, img_url=None):
    """Envia a notícia para o Whapi, como imagem com legenda ou como texto se não houver imagem."""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if img_url:
        payload = {"to": canal, "media": img_url, "caption": msg}
        endpoint = "https://gate.whapi.cloud/messages/image"
    else:
        payload = {"to": canal, "body": msg}
        endpoint = "https://gate.whapi.cloud/messages/text"
    return requests.post(endpoint, headers=headers, json=payload, timeout=30)


# Carrega links e títulos já postados do Supabase
try:
    res = supabase.table("noticias_postadas").select("link, titulo").execute()
    postados = {item["link"] for item in (res.data or []) if "link" in item}
    titulos_postados = [item["titulo"] for item in (res.data or []) if item.get("titulo")]
except Exception as ex:
    print("Erro ao consultar Supabase:", ex)
    postados = set()
    titulos_postados = []

enviadas = 0

for e in reversed(feedparser.parse(RSS).entries[:10]):
    # 1. Filtro por link exato
    if e.link in postados:
        continue

    titulo, fonte = separa(e)

    # 2. Filtro inteligente por similaridade de título (evita a mesma notícia de portais diferentes)
    similar, t_existente, score = eh_similar(titulo, titulos_postados)
    if similar:
        print(f"Notícia ignorada por similaridade ({score:.0%}) com '{t_existente}': {titulo}")
        continue

    if enviadas >= MAX_POR_RODADA:
        break

    data_pub = formata_data_brasilia(e)
    url_final = link_real(e.link)

    # Monta a mensagem formatada para o WhatsApp
    msg = f"📰 *{titulo}*\n"
    detalhes = []
    if fonte:
        detalhes.append(f"🗞️ _{fonte}_")
    if data_pub:
        detalhes.append(f"🕒 _{data_pub}_")

    if detalhes:
        msg += " • ".join(detalhes) + "\n"

    msg += f"\n🔗 {url_final}"

    # Busca a imagem de capa da notícia
    img_url = extrair_imagem(url_final) if url_final.startswith("http") else None
    if img_url:
        print("Imagem encontrada:", img_url)

    r = enviar_noticia(CANAL, msg, img_url)
    print(r.status_code, titulo)
    if r.ok:
        # Salva o registro completo enriquecido no Supabase
        dados_noticia = {
            "link": e.link,
            "link_real": url_final,
            "titulo": titulo,
            "fonte": fonte,
            "imagem_url": img_url,
            "publicado_em": data_pub,
        }
        try:
            supabase.table("noticias_postadas").insert(dados_noticia).execute()
        except Exception as ex:
            print("Erro ao registrar no Supabase:", ex)

        postados.add(e.link)
        titulos_postados.append(titulo)
        enviadas += 1
        time.sleep(2)  # Pausa breve anti-spam

print(f"Finalizado. Total de notícias enviadas nesta rodada: {enviadas}")
