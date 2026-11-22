import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from gtts import gTTS
from playwright.sync_api import sync_playwright

BASE_DIR = Path("/home/dark09/vendas-auto")
VENV_TTS = str(BASE_DIR / ".venv/bin/edge-tts")
LINKS_FILE = BASE_DIR / "links_produtos.txt"
SKILLS_FILE = BASE_DIR / "marketingskills/skills/copywriting/references/ai-tells.md"
INDEX_HTML = BASE_DIR / "index.html"
ENV_FILE = BASE_DIR / ".env"


def carregar_env() -> None:
    if not ENV_FILE.exists():
        return
    for linha in ENV_FILE.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ[chave.strip()] = valor.strip().strip('"').strip("'")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def limpar_url(url: str) -> str:
    if url.count("http") > 1:
        url = "http" + url.split("http")[-1]
    return url.strip()


def encontrar_fonte_ttf() -> str:
    candidatas = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for caminho in candidatas:
        if Path(caminho).exists():
            return caminho
    for ttf in Path("/usr/share/fonts").rglob("*.ttf"):
        return str(ttf)
    return ""


def extrair_produto(url: str, pasta_destino: Path) -> dict:
    pasta_destino.mkdir(parents=True, exist_ok=True)
    dados = {"url": url, "titulo": "", "video": None, "imagens": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1366, "height": 768},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        video_urls = []

        page.on(
            "response",
            lambda res: video_urls.append(res.url)
            if ".mp4" in res.url and ("alicdn" in res.url or "aliexpress" in res.url)
            else None,
        )

        log(f"A aceder ao produto em segundo plano: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(1500)

        url_atual = page.url
        if "/item/" in url_atual and ("login" in url_atual.lower() or "punish" in url_atual.lower()):
            page.goto(url_atual.split("?")[0], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

        html_bruto = page.content()
        soup = BeautifulSoup(html_bruto, "html.parser")

        og_title = soup.find("meta", property="og:title")
        titulo_candidato = og_title["content"].strip() if og_title and og_title.get("content") else ""
        if not titulo_candidato or titulo_candidato.lower() == "aliexpress":
            h1 = soup.find("h1")
            titulo_candidato = h1.get_text(strip=True) if h1 else page.title()

        dados["titulo"] = re.sub(r"\s*[-|]\s*AliExpress.*$", "", titulo_candidato, flags=re.I).strip()
        log(f"Produto detetado: {dados['titulo'][:75]}")

        if video_urls:
            vid_path = pasta_destino / "bruto.mp4"
            try:
                r = requests.get(video_urls[0], timeout=30)
                if r.status_code == 200 and len(r.content) > 50000:
                    vid_path.write_bytes(r.content)
                    dados["video"] = str(vid_path)
                    log("Vídeo real do fornecedor baixado automaticamente.")
            except Exception:
                pass

        urls_imagens = []
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            urls_imagens.append(og_img["content"])

        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if any(dom in src for dom in ["alicdn.com", "aliexpress-media.com"]):
                urls_imagens.append(src)

        urls_script = re.findall(
            r'https://[a-zA-Z0-9.-]+\.(?:alicdn\.com|aliexpress-media\.com)/kf/[A-Za-z0-9_-]+\.(?:jpg|png|jpeg|webp)',
            html_bruto,
        )
        urls_imagens.extend(urls_script)

        vistos = set()
        for img_url in urls_imagens:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            clean_url = re.sub(r"_\d+x\d+.*", "", img_url)
            if clean_url in vistos or any(x in clean_url.lower() for x in ["icon", "logo", "avatar", "badge", "sprite", "154x64"]):
                continue
            vistos.add(clean_url)
            try:
                resp = requests.get(clean_url, timeout=15)
                if resp.status_code == 200 and len(resp.content) > 20000:
                    img_path = pasta_destino / f"img{len(dados['imagens']) + 1}.jpg"
                    img_path.write_bytes(resp.content)
                    dados["imagens"].append(str(img_path))
                if len(dados["imagens"]) >= 5:
                    break
            except Exception:
                continue

        browser.close()

    log(f"Mídias capturadas: {len(dados['imagens'])} imagens | Vídeo: {'Sim' if dados['video'] else 'Não'}")
    return dados


def gerar_copy_inteligente(dados: dict) -> dict:
    regras_anti_slop = SKILLS_FILE.read_text(encoding="utf-8")[:2000] if SKILLS_FILE.exists() else ""
    nome_produto = dados["titulo"] if dados["titulo"] and dados["titulo"].lower() != "aliexpress" else "Achado Inteligente em Promoção"

    prompt = f"""
És um especialista em vendas diretas no Brasil. Cria uma oferta para este produto:
Título: {nome_produto}
Link de afiliado: {dados['url']}

Regras obrigatórias (evita linguagem robótica):
{regras_anti_slop}

Responde APENAS em JSON válido com estas chaves:
{{
  "headline": "Título curto e direto de até 65 caracteres focado no benefício prático",
  "subtitulo": "1 frase curta matando a principal objeção (ex: estoque no Brasil, sem taxa)",
  "preco_texto": "Oferta Relâmpago • Frete Grátis",
  "banner_video": "TEXTO CURTO MAIUSCULO PARA TOPO DO VIDEO (max 26 chars)",
  "narracao": "Roteiro natural de 14 segundos em português do Brasil com pausas (...) e chamada para o link da bio"
}}
"""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    resposta_texto = ""

    if api_key and api_key != "SUA_CHAVE_AQUI":
        for modelo in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={api_key}"
                r = requests.post(
                    url_api,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=20,
                )
                if r.status_code == 200:
                    resposta_texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    log(f"Copy gerada via Gemini API ({modelo}).")
                    break
                elif r.status_code in (400, 403):
                    log(f"Chave GEMINI_API_KEY inválida (HTTP {r.status_code}); a usar gerador rápido local.")
                    break
            except Exception:
                continue
    else:
        log("GEMINI_API_KEY não configurada no .env; a usar gerador rápido local.")

    match = re.search(r"\{.*\}", resposta_texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    nome_curto = nome_produto.split(",")[0][:55].strip()
    return {
        "headline": f"{nome_curto} — Praticidade no Dia a Dia",
        "subtitulo": "Envio rápido direto do estoque no Brasil, com frete grátis e sem taxa extra.",
        "preco_texto": "Desconto Ativo • Frete Grátis",
        "banner_video": "ESTOQUE NO BRASIL - OFERTA",
        "narracao": f"Olha a praticidade disso aqui... {nome_curto}. Resolve o problema em segundos sem precisar furar nada. Já tá com estoque no Brasil, frete grátis, e sem taxa nenhuma. Clica no link da bio antes que acabe.",
    }


def sintetizar_voz_blindada(texto: str, audio_mp3: Path) -> None:
    for voz in ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"]:
        try:
            res = subprocess.run(
                [VENV_TTS, "--voice", voz, "--rate=+10%", "--text", texto, "--write-media", str(audio_mp3)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode == 0 and audio_mp3.exists() and audio_mp3.stat().st_size > 1000:
                log(f"Áudio gerado com sucesso ({voz}).")
                return
        except Exception:
            continue

    log("Servidor Edge-TTS indisponível; a usar motor de reserva gTTS...")
    tts = gTTS(text=texto, lang="pt", tld="com.br")
    tts.save(str(audio_mp3))
    log("Áudio gerado via gTTS.")


def produzir_video(dados: dict, copy: dict, pasta: Path) -> Path:
    if not dados.get("video") and not dados["imagens"]:
        raise RuntimeError("Nenhuma imagem ou vídeo foi extraído da página do AliExpress.")

    audio_mp3 = pasta / "voz_auto.mp3"
    video_out = pasta / "pronto_para_postar.mp4"

    sintetizar_voz_blindada(copy["narracao"], audio_mp3)

    banner = re.sub(r"[^A-Za-z0-9À-ÿ $.,!-]", "", copy["banner_video"]).strip()[:28]
    fonte_ttf = encontrar_fonte_ttf()
    opcao_fonte = f"fontfile='{fonte_ttf}':" if fonte_ttf else ""
    filtro_banner = (
        f"drawbox=x=60:y=150:w=960:h=110:color=#dc2626@0.92:t=fill,"
        f"drawtext={opcao_fonte}text='{banner}':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=185"
    )

    if dados.get("video") and Path(dados["video"]).exists():
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", dados["video"],
            "-i", str(audio_mp3),
            "-filter_complex",
            f"[0:v]setpts=0.88*PTS,scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=#0b0f19,setsar=1,{filtro_banner}[outv];"
            f"[1:a]highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50[outa]",
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
            str(video_out),
        ]
    else:
        dur = float(
            subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_mp3)],
                text=True,
            ).strip()
        )
        imgs = dados["imagens"]
        t_img = round(dur / len(imgs) + 0.15, 2)
        frames_img = int((t_img + 0.1) * 25)
        cmd = ["ffmpeg", "-y"]
        filtros = []
        concat_s = ""
        for i, im in enumerate(imgs):
            cmd.extend(["-loop", "1", "-t", str(t_img), "-i", im])
            filtros.append(
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=#0b0f19,setsar=1,"
                f"zoompan=z='min(zoom+0.0015,1.15)':d={frames_img}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=25,"
                f"trim=duration={t_img},setpts=PTS-STARTPTS[v{i}]"
            )
            concat_s += f"[v{i}]"
        cmd.extend(["-i", str(audio_mp3)])
        filtros.append(f"{concat_s}concat=n={len(imgs)}:v=1:a=0,{filtro_banner}[outv]")
        filtros.append(f"[{len(imgs)}:a]highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50[outa]")
        cmd.extend([
            "-filter_complex", ";".join(filtros),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
            str(video_out),
        ])

    res_ff = subprocess.run(cmd, capture_output=True, text=True)
    if res_ff.returncode != 0 or not video_out.exists() or video_out.stat().st_size < 10000:
        raise RuntimeError(f"FFmpeg falhou: {res_ff.stderr[-300:]}")

    return video_out


def atualizar_vitrine_e_publicar(dados: dict, copy: dict, slug: str) -> None:
    if not dados["imagens"] or not INDEX_HTML.exists():
        raise RuntimeError("Sem imagem válida para atualizar a vitrine.")

    img_publica = f"{slug}.jpg"
    Path(dados["imagens"][0]).replace(BASE_DIR / img_publica)

    novo_card = f"""
    <div class="hero" style="margin-top:16px;">
      <img class="product-img" src="./{img_publica}" alt="{copy['headline']}">
      <div class="rating">★★★★★ Oferta Verificada • Estoque no Brasil</div>
      <h2 style="font-size:1.2rem; margin:0 0 8px;">{copy['headline']}</h2>
      <p class="subtitle">{copy['subtitulo']}</p>
      <div class="price-box">
        <span class="now" style="font-size:1.35rem;">{copy['preco_texto']}</span>
      </div>
      <a class="btn" href="{dados['url']}" target="_blank" rel="noopener">
        CONFERIR DESCONTO NO ALIEXPRESS →
      </a>
    </div>
    """

    html = INDEX_HTML.read_text(encoding="utf-8")
    html = html.replace('<div class="wrap">', f'<div class="wrap">\n{novo_card}', 1)
    INDEX_HTML.write_text(html, encoding="utf-8")

    subprocess.run(["git", "-C", str(BASE_DIR), "add", "index.html", img_publica], check=True)
    subprocess.run(["git", "-C", str(BASE_DIR), "commit", "-m", f"auto: adiciona {slug}"], check=True)
    subprocess.run(["git", "-C", str(BASE_DIR), "push", "origin", "main"], check=True)
    log("Vitrine GitHub Pages atualizada e publicada automaticamente.")


def processar_fila() -> None:
    carregar_env()
    if not LINKS_FILE.exists():
        return
    linhas = [l.strip() for l in LINKS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    pendentes = [l for l in linhas if not l.startswith("#")]

    if not pendentes:
        log("Nenhum link novo na fila (links_produtos.txt).")
        return

    url_bruta = pendentes[0]
    url_limpa = limpar_url(url_bruta)
    slug = f"prod_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    pasta_prod = BASE_DIR / slug

    try:
        dados = extrair_produto(url_limpa, pasta_prod)
        copy = gerar_copy_inteligente(dados)
        video = produzir_video(dados, copy, pasta_prod)
        atualizar_vitrine_e_publicar(dados, copy, slug)

        novas_linhas = [f"# CONCLUIDO | {url_limpa}" if l == url_bruta else l for l in linhas]
        LINKS_FILE.write_text("\n".join(novas_linhas) + "\n", encoding="utf-8")
        log(f"✅ Ciclo autónomo concluído! Vídeo real pronto em: {video}")
    except Exception as e:
        log(f"❌ Falha neste item ({e}). Marcado como ERRO para não bloquear os próximos.")
        novas_linhas = [f"# ERRO | {url_limpa}" if l == url_bruta else l for l in linhas]
        LINKS_FILE.write_text("\n".join(novas_linhas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    processar_fila()
