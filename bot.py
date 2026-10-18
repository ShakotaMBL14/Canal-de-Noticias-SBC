import os, time, re, unicodedata, json
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import feedparser, requests
from dotenv import load_dotenv
from googlenewsdecoder import gnewsdecoder
from selectolax.parser import HTMLParser
from supabase import create_client, Client

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILTROS_PATH = os.path.join(BASE_DIR, "filtros.json")

RSS = "https://news.google.com/rss/search?q=%22S%C3%A3o+Bernardo+do+Campo%22+when:1d&hl=pt-BR&gl=BR&ceid=BR:pt-419"
TOKEN = os.getenv("WHAPI_TOKEN")
CANAL = os.getenv("CANAL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
MAX_POR_RODADA = 5
MAX_FUTEBOL_POR_RODADA = 1  # Limite de no máximo 1 notícia de futebol por rodada
SIMILARIDADE_LIMIAR = 0.70  # Similaridade de sequência de caracteres (difflib)
SIMILARIDADE_PALAVRAS_LIMIAR = 0.65  # Sobreposição de palavras-chave essenciais


def carregar_filtros():
    """Carrega termos de futebol, spam, stopwords, termos genéricos e pós-jogo de filtros.json."""
    if os.path.exists(FILTROS_PATH):
        try:
            with open(FILTROS_PATH, "r", encoding="utf-8") as f:
                dados = json.load(f)
                return (
                    dados.get("termos_spam_futebol", []),
                    dados.get("termos_futebol", []),
                    set(dados.get("stopwords", [])),
                    set(dados.get("termos_genericos_futebol", [])),
                    dados.get("termos_pos_jogo", []),
                )
        except Exception as ex:
            print("AVISO: Falha ao ler filtros.json:", ex)
    return [], [], set(), set(), []


TERMOS_SPAM_FUTEBOL, TERMOS_FUTEBOL, STOPWORDS, TERMOS_GENERICOS_FUTEBOL, TERMOS_POS_JOGO = carregar_filtros()

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


def extrair_tokens(texto):
    """Normaliza texto removendo acentos, pontuações e stopwords para comparar palavras relevantes."""
    txt = unicodedata.normalize("NFKD", texto)
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    txt = re.sub(r'[^a-z0-9\s]', ' ', txt)
    return set(w for w in txt.split() if len(w) > 2 and w not in STOPWORDS)


def extrair_tokens_adversario(texto):
    """Extrai palavras específicas que identificam adversários em notícias de futebol."""
    txt = unicodedata.normalize("NFKD", texto)
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    txt = re.sub(r'[^a-z0-9\s]', ' ', txt)
    return set(w for w in txt.split() if len(w) >= 4 and w not in STOPWORDS and w not in TERMOS_GENERICOS_FUTEBOL)


def eh_futebol(titulo):
    """Identifica se a notícia é temática de futebol/esporte."""
    t = titulo.lower()
    if any(termo in t for termo in TERMOS_FUTEBOL):
        return True
    if re.search(r'\s+(?:x|vs|\bcontra\b)\s+', t):
        return True
    if re.search(r'\b(gol|gols|g4|g2|z4)\b', t):
        return True
    if re.search(r'\b(vence|venceu|bate|bateu|empata|empatou|derrota|derrotou|perde|perdeu|visita|visitam|enfrenta|enfrentam|recebe|recebem|duela|duelam|joga|jogam)\b', t) and re.search(r'\b(tigre|bern[ôo]|s[ãa]o bernardo)\b', t):
        return True
    return False


def eh_resultado_futebol(titulo):
    """Verifica se a notícia informa o resultado final (pós-jogo) da partida."""
    t = titulo.lower()
    return any(re.search(rf'\b{re.escape(termo)}\b', t) for termo in TERMOS_POS_JOGO)


def eh_spam_esporte(titulo):
    """Verifica se o título é caça-clique de transmissão, palpites ou apostas."""
    t = titulo.lower()
    return any(termo in t for termo in TERMOS_SPAM_FUTEBOL)


def eh_similar(titulo_novo, titulos_existentes, limiar=SIMILARIDADE_LIMIAR, limiar_tokens=SIMILARIDADE_PALAVRAS_LIMIAR):
    """Verifica se o título é muito similar por sequência de caracteres, termos ou mesmo confronto de futebol."""
    t1 = titulo_novo.lower().strip()
    tokens1 = extrair_tokens(titulo_novo)
    is_futebol_t1 = eh_futebol(titulo_novo)
    adversarios1 = extrair_tokens_adversario(titulo_novo) if is_futebol_t1 else set()

    for t2_original in titulos_existentes:
        t2 = t2_original.lower().strip()

        # 1. Comparação direta de caracteres (SequenceMatcher)
        score_seq = SequenceMatcher(None, t1, t2).ratio()
        if score_seq >= limiar:
            return True, t2_original, score_seq

        # 2. Comparação de sobreposição de termos-chave (Token Overlap)
        tokens2 = extrair_tokens(t2_original)
        if tokens1 and tokens2:
            inter = tokens1 & tokens2
            menor = min(len(tokens1), len(tokens2))
            if menor > 0:
                overlap = len(inter) / menor
                if overlap >= limiar_tokens:
                    return True, t2_original, overlap

        # 3. Confronto esportivo (mesmo adversário)
        if is_futebol_t1 and eh_futebol(t2_original):
            adversarios2 = extrair_tokens_adversario(t2_original)
            confronto_comum = adversarios1 & adversarios2
            if any(len(w) >= 5 for w in confronto_comum):
                eh_res_novo = eh_resultado_futebol(titulo_novo)
                eh_res_antigo = eh_resultado_futebol(t2_original)

                # Se a matéria anterior era pré-jogo e a nova traz o resultado da partida, PERMITE!
                if not eh_res_antigo and eh_res_novo:
                    continue

                # Se ambas são pré-jogo ou se já noticiou um resultado, bloqueia repetição
                return True, t2_original, 1.0

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


def main():
    # Carrega links e títulos já postados do Supabase (registros mais recentes)
    try:
        res = (
            supabase.table("noticias_postadas")
            .select("link, titulo")
            .order("id", desc=True)
            .limit(300)
            .execute()
        )
        postados = {item["link"] for item in (res.data or []) if "link" in item}
        titulos_postados = [item["titulo"] for item in (res.data or []) if item.get("titulo")]
    except Exception:
        try:
            res = supabase.table("noticias_postadas").select("link, titulo").limit(300).execute()
            postados = {item["link"] for item in (res.data or []) if "link" in item}
            titulos_postados = [item["titulo"] for item in (res.data or []) if item.get("titulo")]
        except Exception as ex:
            print("Erro ao consultar Supabase:", ex)
            postados = set()
            titulos_postados = []

    enviadas = 0
    futebol_enviadas = 0

    for e in reversed(feedparser.parse(RSS).entries[:15]):
        # 1. Filtro por link exato
        if e.link in postados:
            continue

        titulo, fonte = separa(e)

        # 2. Filtro anti-spam esportivo (elimina palpites, odds, guias de transmissão)
        if eh_spam_esporte(titulo):
            print(f"Notícia ignorada (spam/guia esportivo): {titulo}")
            continue

        # 3. Cota de futebol por rodada (máximo 1 por rodada para não afogar o canal)
        is_futebol = eh_futebol(titulo)
        if is_futebol and futebol_enviadas >= MAX_FUTEBOL_POR_RODADA:
            print(f"Notícia de futebol ignorada (cota de {MAX_FUTEBOL_POR_RODADA} por rodada atingida): {titulo}")
            continue

        # 4. Filtro inteligente por similaridade de título (SequenceMatcher + Palavras-chave + Confronto)
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
            if is_futebol:
                futebol_enviadas += 1
            enviadas += 1
            time.sleep(2)  # Pausa breve anti-spam

    print(f"Finalizado. Total de notícias enviadas nesta rodada: {enviadas} (sendo {futebol_enviadas} de futebol)")


if __name__ == "__main__":
    main()

