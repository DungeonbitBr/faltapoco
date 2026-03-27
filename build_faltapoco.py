from __future__ import annotations

import json
import os
import re
import sys
import html
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

try:
    import requests
except ImportError:
    print("Instale dependências com: pip install -r requirements.txt", file=sys.stderr)
    raise

BASE_URL = os.getenv("FALTAPOCO_BASE_URL", "https://faltapoco.com.br").rstrip("/")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
DEFAULT_INPUT = os.getenv("FALTAPOCO_INPUT", "games_input.json")
OUTPUT_DIR = Path(os.getenv("FALTAPOCO_OUTPUT", "site_build"))
ANNOUNCEMENT_START = os.getenv("FALTAPOCO_ANNOUNCEMENT_START", "2023-01-01")

# search.list custa 100 unidades de quota por chamada.
# videos.list custa 1 unidade por chamada.
# Por isso o script tenta no máximo 2 consultas por jogo.
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

PREFERRED_CHANNEL_TERMS = [
    "rockstar games",
    "playstation",
    "xbox",
    "nintendo",
    "capcom",
    "bandai namco",
    "bethesda",
    "electronic arts",
    "ea",
    "ubisoft",
    "sega",
    "atlus",
    "konami",
    "koei tecmo",
    "warner bros",
    "insomniac games",
    "io interactive",
    "wizards of the coast",
    "square enix",
    "focus entertainment",
    "deep silver",
]

STATUS_LABELS = {
    "released": "Disponível agora",
    "confirmed": "Data confirmada",
    "window": "Janela de lançamento",
    "rumor": "Rumor",
    "unknown": "Sem data confirmada",
}


def slugify(text: str) -> str:
    text = text.lower().strip()
    replacements = {
        "á": "a", "à": "a", "â": "a", "ã": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u", "ü": "u",
        "ç": "c",
        "'": "",
        ":": " ",
        "/": " ",
        "&": " and ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "jogo"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def parse_release_iso(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def display_release(value: str, time: str = "") -> str:
    iso = parse_release_iso(value)
    if iso:
        y, m, d = iso.split("-")
        meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
        base = f"{int(d)} de {meses[int(m)-1]} de {y}"
        if time and time.strip():
            return f"{base} às {time.strip()} (Brasília)"
        return base
    return value or "Sem data confirmada"


def make_release_time_display(time: str) -> str:
    """Formata o horário para exibição: '19:00' → '19:00 (horário de Brasília)'"""
    t = (time or "").strip()
    if not t:
        return ""
    return f"{t} (horário de Brasília)"


def parse_date_obj(value: str) -> Optional[date]:
    iso = parse_release_iso(value)
    if not iso:
        return None
    return date.fromisoformat(iso)


def days_left(value: str) -> Optional[int]:
    d = parse_date_obj(value)
    if not d:
        return None
    return max((d - date.today()).days, 0)


def iso8601_to_seconds(duration: str) -> Optional[int]:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def safe_json_load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class TrailerInfo:
    video_id: str
    title: str
    channel_title: str
    published_at: str
    url: str
    embed_url: str
    duration_seconds: Optional[int]
    view_count: int
    confidence: str
    search_query: str


@dataclass
class GameRecord:
    slug: str
    name: str
    release: str
    release_display: str
    release_time: str          # "19:00" — horário local de Brasília, ou ""
    release_time_display: str  # "19:00 (horário de Brasília)" ou ""
    status: str
    confidence_date: str
    developer: str
    publisher: str
    platforms: List[str]
    background_image: str
    description: str
    video: Optional[TrailerInfo]
    news: List[Dict[str, str]]
    reviews: List[Dict[str, str]]
    sys_req: Dict[str, Any]        # {"minimum": {...}, "recommended": {...}}
    affiliate_ml: str              # link afiliado Mercado Livre
    affiliate_amz: str             # link afiliado Amazon
    source: Dict[str, str]
    days_left: Optional[int]
    api_url: str
    page_url: str
    updated_at: str
    # Campos opcionais — não quebram jogos antigos
    priority: str              # "high" | "medium" | "low" | ""
    event: str                 # slug do evento, ex: "xbox-partner-preview-2026"
    announcement_type: str     # "World Premiere" | "New Info" | "Port" | etc.
    release_window_raw: str    # valor bruto da data, ex: "Summer 2026"
    premium: bool              # True = renderiza blocos extras de retenção
    story: str                 # bloco história/ambientação (premium)
    context: str               # bloco "por que importa" (premium)
    seo_text: str              # bloco SEO extra (premium)
    confirmed_features: List[str]  # lista "o que já foi confirmado" (premium)
    related_games: List[str]       # slugs de jogos relacionados para linkagem interna


class YouTubeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def search(self, query: str, max_results: int = 6) -> List[Dict[str, Any]]:
        params = {
            "part": "snippet",
            "type": "video",
            "videoEmbeddable": "true",
            "maxResults": max_results,
            "q": query,
            "key": self.api_key,
        }
        r = self.session.get(YOUTUBE_SEARCH_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = []
        ids = []
        for item in data.get("items", []):
            video_id = item["id"]["videoId"]
            ids.append(video_id)
            items.append({
                "videoId": video_id,
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "channelTitle": item["snippet"]["channelTitle"],
                "publishedAt": item["snippet"]["publishedAt"],
            })
        details = self.videos(ids)
        by_id = {d["videoId"]: d for d in details}
        for item in items:
            item.update(by_id.get(item["videoId"], {}))
        return items

    def videos(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        params = {
            "part": "contentDetails,statistics",
            "id": ",".join(ids),
            "key": self.api_key,
            "maxResults": len(ids),
        }
        r = self.session.get(YOUTUBE_VIDEOS_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("items", []):
            out.append({
                "videoId": item["id"],
                "duration_seconds": iso8601_to_seconds(item.get("contentDetails", {}).get("duration", "")),
                "viewCount": int(item.get("statistics", {}).get("viewCount", 0)),
            })
        return out


def _score_video(item: Dict[str, Any], game_name: str) -> int:
    title = item.get("title", "").lower()
    desc = item.get("description", "").lower()
    channel = item.get("channelTitle", "").lower()
    game = game_name.lower()
    score = 0

    if game in title:
        score += 40
    elif game in desc:
        score += 12

    for kw, val in {
        "official trailer": 25,
        "official reveal trailer": 30,
        "reveal trailer": 18,
        "announcement trailer": 15,
        "gameplay trailer": 10,
        "launch trailer": 8,
    }.items():
        if kw in title:
            score += val

    for bad in ["reaction", "review", "preview", "análise", "analysis", "theory", "fan made", "concept"]:
        if bad in title:
            score -= 20

    for term in PREFERRED_CHANNEL_TERMS:
        if term in channel:
            score += 25
            break

    dur = item.get("duration_seconds")
    if isinstance(dur, int):
        if 45 <= dur <= 240:
            score += 8
        elif dur > 1200:
            score -= 8

    views = item.get("viewCount", 0)
    if views > 1_000_000:
        score += 4

    return score


def find_best_trailer(client: YouTubeClient, game_name: str) -> Optional[TrailerInfo]:
    # Nomes curtos/genéricos precisam de contexto extra na busca
    short_or_generic = len(game_name.split()) <= 2
    context = " game" if short_or_generic else ""
    queries = [
        f"{game_name}{context} official trailer",
        f"{game_name}{context} reveal trailer",
        f"{game_name} videogame trailer",
    ]
    pool: List[Tuple[Dict[str, Any], str]] = []
    seen = set()

    for query in queries:
        try:
            results = client.search(query)
        except Exception as e:
            print(f"[WARN] Falha YouTube para '{game_name}' / '{query}': {e}")
            continue
        for item in results:
            if item["videoId"] in seen:
                continue
            seen.add(item["videoId"])
            pool.append((item, query))

    if not pool:
        return None

    ranked = sorted(pool, key=lambda x: (_score_video(x[0], game_name), x[0].get("viewCount", 0)), reverse=True)
    best, query = ranked[0]
    score = _score_video(best, game_name)
    # Aceita qualquer resultado com score positivo (>= 10); só descarta lixo total
    if score < 10:
        return None
    confidence = "high" if score >= 70 else "medium" if score >= 40 else "low"

    return TrailerInfo(
        video_id=best["videoId"],
        title=best.get("title", ""),
        channel_title=best.get("channelTitle", ""),
        published_at=best.get("publishedAt", ""),
        url=f"https://www.youtube.com/watch?v={best['videoId']}",
        embed_url=f"https://www.youtube.com/embed/{best['videoId']}",
        duration_seconds=best.get("duration_seconds"),
        view_count=int(best.get("viewCount", 0)),
        confidence=confidence,
        search_query=query,
    )


def build_game_record(raw: Dict[str, Any], youtube: Optional[YouTubeClient]) -> GameRecord:
    name = raw["name"].strip()
    slug = raw.get("slug") or slugify(name)
    release = raw.get("release", "Sem data confirmada")
    status = raw.get("status", "unknown")
    confidence_date = raw.get("confidence_date", "media")
    video = None
    # Prioridade 1: video_id direto no JSON (ex: "video_id": "dQw4w9WgXcQ")
    if raw.get("video_id"):
        vid = raw["video_id"].strip()
        video = TrailerInfo(
            video_id=vid,
            title=raw.get("video_title", "Trailer Oficial"),
            channel_title=raw.get("video_channel", ""),
            published_at="",
            url=f"https://www.youtube.com/watch?v={vid}",
            embed_url=f"https://www.youtube.com/embed/{vid}",
            duration_seconds=None,
            view_count=0,
            confidence="manual",
            search_query="manual",
        )
    # Prioridade 2: campo video como dict com url
    elif isinstance(raw.get("video"), dict):
        manual = raw["video"]
        video_id = extract_youtube_id(manual.get("url", ""))
        if video_id:
            video = TrailerInfo(
                video_id=video_id,
                title=manual.get("title", "Trailer"),
                channel_title=manual.get("channel_title", ""),
                published_at=manual.get("published_at", ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
                embed_url=f"https://www.youtube.com/embed/{video_id}",
                duration_seconds=None,
                view_count=0,
                confidence="manual",
                search_query="manual",
            )
    # Prioridade 3: busca automática via YouTube API
    elif youtube and raw.get("video", "auto") == "auto":
        video = find_best_trailer(youtube, name)

    page_url = f"{BASE_URL}/jogos/{slug}/"
    api_url = f"{BASE_URL}/api/v1/games/{slug}.json"
    return GameRecord(
        slug=slug,
        name=name,
        release=release,
        release_display=display_release(release, raw.get("release_time", "")),
        release_time=raw.get("release_time", "").strip(),
        release_time_display=make_release_time_display(raw.get("release_time", "")),
        status=status,
        confidence_date=confidence_date,
        developer=raw.get("developer", ""),
        publisher=raw.get("publisher", ""),
        platforms=raw.get("platforms", []),
        background_image=raw.get("background_image", ""),
        description=raw.get("description", f"Acompanhe a data de lançamento, trailer e atualizações de {name}."),
        video=video,
        news=raw.get("news", []),
        reviews=raw.get("reviews", []),
        sys_req=raw.get("sys_req", {}),
        affiliate_ml=raw.get("affiliate_ml", "").strip(),
        affiliate_amz=raw.get("affiliate_amz", "").strip(),
        source=raw.get("source", {"type": "manual"}),
        days_left=days_left(release),
        api_url=api_url,
        page_url=page_url,
        updated_at=datetime.now(timezone.utc).isoformat(),
        priority=raw.get("priority", ""),
        event=raw.get("event", ""),
        announcement_type=raw.get("announcement_type", ""),
        release_window_raw=raw.get("release_window_raw", ""),
        premium=bool(raw.get("premium", False)),
        story=raw.get("story", ""),
        context=raw.get("context", ""),
        seo_text=raw.get("seo_text", ""),
        confirmed_features=raw.get("confirmed_features", []),
        related_games=raw.get("related_games", []),
    )


def extract_youtube_id(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.strip("/") or None
    if "youtube.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "embed":
            return parts[1]
    return None


def render_json_ld(game: GameRecord) -> str:
    """Schema.org VideoGame — markup completo para SEO."""
    release_iso = parse_release_iso(game.release)
    # Imagem OG: usa bg.jpg relativo à página, ou fallback do site
    og_image = f"{game.page_url}bg.jpg" if game.background_image else f"{BASE_URL}/og-default.jpg"
    payload = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": game.name,
        "description": game.description or f"Acompanhe a data de lançamento de {game.name}.",
        "url": game.page_url,
        "image": og_image,
        "publisher": {"@type": "Organization", "name": game.publisher} if game.publisher else None,
        "author":    {"@type": "Organization", "name": game.developer} if game.developer else None,
        "datePublished": release_iso or None,
        "gamePlatform": game.platforms or None,
        "applicationCategory": "Game",
        "operatingSystem": "PlayStation 5, Xbox Series X|S, PC, Nintendo Switch 2" if game.platforms else None,
        "trailer": {
            "@type": "VideoObject",
            "name": f"Trailer oficial de {game.name}",
            "embedUrl": game.video.embed_url,
            "url": game.video.url,
            "thumbnailUrl": f"https://img.youtube.com/vi/{game.video.video_id}/maxresdefault.jpg",
        } if game.video else None,
        "potentialAction": {
            "@type": "WatchAction",
            "target": game.video.url,
        } if game.video else None,
        # BreadcrumbList para navegação
        "breadcrumb": {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "FaltaPouco", "item": BASE_URL},
                {"@type": "ListItem", "position": 2, "name": "Jogos", "item": f"{BASE_URL}/jogos/"},
                {"@type": "ListItem", "position": 3, "name": game.name, "item": game.page_url},
            ]
        },
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "", [])}
    return json.dumps(payload, ensure_ascii=False, indent=None)


def render_website_jsonld(total_games: int) -> str:
    """Schema.org WebSite para a home — habilita sitelinks search no Google."""
    payload = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "FaltaPouco",
        "alternateName": "faltapoco.com.br",
        "url": BASE_URL,
        "description": "Countdowns e datas de lançamento dos jogos mais aguardados. Base de dados pública para o mercado gamer brasileiro.",
        "inLanguage": "pt-BR",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{BASE_URL}/?q={{search_term_string}}"
            },
            "query-input": "required name=search_term_string"
        },
        "about": {
            "@type": "ItemList",
            "name": "Jogos monitorados",
            "numberOfItems": total_games,
        }
    }
    return json.dumps(payload, ensure_ascii=False, indent=None)


# ── SVG PLATFORM ICONS — logos oficiais ──────────────────────────────────────

# PS5: 4 símbolos PlayStation (triângulo, círculo, X, quadrado) + texto PS5
_ICON_PS5 = (
    '<svg viewBox="0 0 52 22" xmlns="http://www.w3.org/2000/svg" style="height:17px;flex-shrink:0">'
    # símbolos na linha superior
    '<polygon points="0,11 4,4 8,11" fill="#3cd" opacity=".9"/>'
    '<circle cx="13" cy="7.5" r="3.8" fill="#f55"/>'
    '<line x1="18" y1="4" x2="23" y2="11" stroke="#88f" stroke-width="1.8" stroke-linecap="round"/>'
    '<line x1="23" y1="4" x2="18" y2="11" stroke="#88f" stroke-width="1.8" stroke-linecap="round"/>'
    '<rect x="26" y="4" width="7" height="7" fill="#f9c" opacity=".9"/>'
    # PS5 texto na linha inferior
    '<text x="0" y="21" font-size="11" font-weight="900" font-family="Arial Black,Arial,sans-serif" '
    'fill="#ffffff" letter-spacing="-0.5">PS5</text>'
    '</svg>'
)

# Xbox: esfera verde com X em borboleta — logo oficial
_ICON_XBOX = (
    '<svg viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" style="height:17px;flex-shrink:0">'
    '<circle cx="10" cy="10" r="9.5" fill="#107C10"/>'
    '<circle cx="10" cy="10" r="9.5" fill="none" stroke="#1db81d" stroke-width=".8"/>'
    '<ellipse cx="8" cy="6" rx="4" ry="2.5" fill="rgba(255,255,255,0.18)"/>'
    # X em formato de borboleta idêntico ao logo Xbox oficial
    '<path d="M5,4.5 C7,7.5 8.5,9 10,10.5 C8.5,12 7,13.5 5,16.5 '
    'C7.5,14.5 9,13 10,10.5 C11,13 12.5,14.5 15,16.5 '
    'C13,13.5 11.5,12 10,10.5 C11.5,9 13,7.5 15,4.5 '
    'C12.5,6.5 11,8 10,10.5 C9,8 7.5,6.5 5,4.5 Z" fill="#ffffff"/>'
    '</svg>'
)

# Switch 2: console com joy-cons vermelho+azul e "2" na tela — logo Nintendo oficial
_ICON_SWITCH2 = (
    '<svg viewBox="0 0 48 20" xmlns="http://www.w3.org/2000/svg" style="height:17px;flex-shrink:0">'
    # joy-con esquerdo vermelho
    '<rect x="0" y="1" width="10" height="18" rx="5" fill="#cc0010"/>'
    '<rect x="0" y="1" width="10" height="18" rx="5" fill="none" stroke="#ff1122" stroke-width=".5"/>'
    '<circle cx="5" cy="6.5" r="2.5" fill="rgba(0,0,0,0.35)"/>'
    '<circle cx="5" cy="6.5" r="1.5" fill="rgba(255,255,255,0.3)"/>'
    '<circle cx="3.5" cy="15" r="1.5" fill="rgba(0,0,0,0.3)"/>'
    '<circle cx="7" cy="13" r="1.2" fill="rgba(0,0,0,0.3)"/>'
    # corpo central
    '<rect x="10" y="0" width="28" height="20" rx="2.5" fill="#111" stroke="#333" stroke-width=".5"/>'
    # tela
    '<rect x="12" y="2" width="24" height="16" rx="1.5" fill="#0a0a14"/>'
    # "2" na tela
    '<text x="24" y="13" font-size="10" font-weight="900" font-family="Arial Black,sans-serif" '
    'fill="#ffffff" text-anchor="middle" opacity=".85">2</text>'
    # joy-con direito azul
    '<rect x="38" y="1" width="10" height="18" rx="5" fill="#0040bb"/>'
    '<rect x="38" y="1" width="10" height="18" rx="5" fill="none" stroke="#2255ee" stroke-width=".5"/>'
    '<circle cx="43" cy="13" r="2.5" fill="rgba(0,0,0,0.35)"/>'
    '<circle cx="43" cy="13" r="1.5" fill="rgba(255,255,255,0.3)"/>'
    '<circle cx="40" cy="6" r="1.5" fill="rgba(255,255,255,0.25)"/>'
    '<circle cx="44" cy="6" r="1.5" fill="rgba(255,255,255,0.25)"/>'
    '</svg>'
)

# PC: gabinete torre compacto e proporcional com LED verde
_ICON_PC = (
    '<svg viewBox="0 0 16 22" xmlns="http://www.w3.org/2000/svg" style="height:17px;flex-shrink:0">'
    # corpo
    '<rect x="0" y="0" width="16" height="22" rx="2" fill="#b8720a"/>'
    '<rect x="0" y="0" width="16" height="22" rx="2" fill="none" stroke="#FFB300" stroke-width=".8"/>'
    # painel superior
    '<rect x="2" y="2" width="12" height="7" rx="1" fill="rgba(255,179,0,0.2)"/>'
    # drive bay 1
    '<rect x="2" y="11" width="12" height="2.5" rx=".5" fill="#FFB300" opacity=".7"/>'
    # drive bay 2
    '<rect x="2" y="15" width="12" height="2.5" rx=".5" fill="#FFB300" opacity=".4"/>'
    # power button
    '<circle cx="5" cy="19.5" r="1.5" fill="none" stroke="#FFB300" stroke-width=".7"/>'
    '<circle cx="5" cy="19.5" r=".7" fill="#FFB300"/>'
    # LED verde
    '<circle cx="9" cy="19.5" r=".9" fill="#00e676" opacity=".9"/>'
    '</svg>'
)


PLATFORM_META: Dict[str, Dict] = {
    "PS5": {
        "bg": "linear-gradient(135deg,#00267a,#003da8)",
        "text": "#ffffff",
        "label": "PS5",
        "icon": _ICON_PS5,
        "border": "#1a5fd4",
        "glow": "rgba(0,60,168,0.55)",
    },
    "Xbox Series X|S": {
        "bg": "linear-gradient(135deg,#083d08,#0e6b0e)",
        "text": "#ffffff",
        "label": "XBOX",
        "icon": _ICON_XBOX,
        "border": "#1aaa1a",
        "glow": "rgba(16,124,16,0.55)",
    },
    "Switch 2": {
        "bg": "linear-gradient(135deg,#a0000a,#E60012)",
        "text": "#ffffff",
        "label": "SWITCH 2",
        "icon": _ICON_SWITCH2,
        "border": "#ff3322",
        "glow": "rgba(230,0,18,0.55)",
    },
    "PC": {
        "bg": "linear-gradient(135deg,#7a5500,#c8860a)",
        "text": "#ffffff",
        "label": "PC",
        "icon": _ICON_PC,
        "border": "#FFB300",
        "glow": "rgba(255,179,0,0.55)",
    },
}

_BADGE_STYLE = (
    "display:inline-flex;align-items:center;gap:7px;"
    "padding:6px 13px 6px 10px;"
    "border-radius:7px;"
    "font-family:'Space Grotesk',Arial,sans-serif;"
    "font-size:11px;font-weight:800;"
    "letter-spacing:.08em;text-transform:uppercase;"
    "line-height:1;white-space:nowrap;"
    "border:1px solid {border};"
    "background:{bg};"
    "color:{text};"
    "box-shadow:0 2px 12px {glow},0 1px 3px rgba(0,0,0,0.6);"
)

def render_platform_badges(platforms: List[str]) -> str:
    if not platforms:
        return '<span style="color:rgba(244,244,245,.5);font-size:13px">Não informado</span>'
    badges = []
    for p in platforms:
        meta = PLATFORM_META.get(p)
        if meta:
            style = _BADGE_STYLE.format(bg=meta["bg"], text=meta["text"], border=meta["border"], glow=meta.get("glow", "rgba(0,0,0,0.3)"))
            badges.append(
                f'<span style="{style}">'
                f'{meta["icon"]}'
                f'<span>{html.escape(meta["label"])}</span>'
                f'</span>'
            )
        else:
            badges.append(
                f'<span style="display:inline-flex;align-items:center;padding:5px 12px;border-radius:6px;'
                f'background:#1f1f23;color:#fff;font-size:11px;font-weight:700;letter-spacing:.06em;'
                f'text-transform:uppercase;border:1px solid rgba(255,255,255,.15)">'
                f'{html.escape(p)}</span>'
            )
    return '<div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:10px">' + "".join(badges) + '</div>'


def html_page(game: GameRecord, all_games: Optional[List["GameRecord"]] = None) -> str:
    platforms_text = ", ".join(game.platforms) if game.platforms else "plataformas não informadas"
    badge = STATUS_LABELS.get(game.status, STATUS_LABELS["unknown"])
    platforms_html = render_platform_badges(game.platforms)

    # --- badge dot ---
    badge_dot = ""
    if game.status in ("confirmed", "released"):
        dot_color = "#22c55e" if game.status == "confirmed" else "#a855f7"
        badge_dot = f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{dot_color};animation:pulse 2s infinite;margin-right:6px;flex-shrink:0"></span>'

    # --- release_iso & JS date ---
    release_iso = parse_release_iso(game.release)
    ymd_js = "null"
    announced_js = "null"
    if release_iso:
        y, m, d = release_iso.split("-")
        # Usa hora exata se disponível (horário de Brasília = UTC-3)
        # Converte para UTC somando 3h para o JS (que usa horário local do browser)
        # Melhor abordagem: passa string ISO com offset para o JS parsear
        if game.release_time:
            hh, mm = (game.release_time.strip() + ":00").split(":")[:2]
            # ISO string com offset BRT (UTC-3)
            iso_str = f"{y}-{m}-{d}T{hh}:{mm}:00-03:00"
            ymd_js = f'new Date("{iso_str}")'
        else:
            ymd_js = f"new Date({int(y)}, {int(m)-1}, {int(d)}, 0, 0, 0)"
        announced_js = f"new Date({int(y)-1}, {int(m)-1}, {int(d)}, 0, 0, 0)"

    # --- background: bg.jpg fica em jogos/<slug>/bg.jpg, mesma pasta do index.html ---
    bg_html = '<img src="bg.jpg" alt="" onerror="this.style.display=\'none\'">' if game.background_image else ''

    # --- video embed ---
    video_block = ""
    if game.video:
        yt_channel_info = f' · {html.escape(game.video.channel_title)}' if game.video.channel_title else ""
        video_block = f"""
<section class="content-section" id="trailer">
  <div class="section-eyebrow">🎬 Trailer Oficial</div>
  <div class="video-wrap">
    <iframe
      src="{html.escape(game.video.embed_url)}?rel=0&modestbranding=1&color=white"
      title="Trailer de {html.escape(game.name)}"
      loading="lazy"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      allowfullscreen></iframe>
  </div>
  <p class="video-meta">
    <a href="{html.escape(game.video.url)}" target="_blank" rel="noopener">▶ Ver no YouTube</a>{yt_channel_info}
  </p>
</section>"""

    # --- system requirements (PC only) ---
    # Multi-tier: {"tiers":[{"name":"...","performance":"...","gpu":"...","cpu":"..."},...], "shared":{...}, "notes":"..."}
    # Simples:    {"minimum":{...}, "recommended":{...}}
    sys_req_block = ""
    sr = game.sys_req
    if sr and "PC" in game.platforms:
        ROW_KEYS = [
            ("performance", "Desempenho"),
            ("gpu",         "GPU"),
            ("cpu",         "CPU"),
            ("ram",         "RAM"),
            ("os",          "Sistema Operacional"),
            ("storage",     "Armazenamento"),
            ("directx",     "DirectX"),
            ("notes",       "Observa\u00e7\u00f5es"),
        ]
        TIER_COLORS = ["#4a6fa5","#3a7d5a","#7a5a9a","#a05020","#8a1a1a"]

        if "tiers" in sr:
            tiers  = sr["tiers"]
            shared = sr.get("shared", {})
            n      = len(tiers)
            tier_headers = "".join(
                f'<th style="background:{TIER_COLORS[i % len(TIER_COLORS)]}22;border-bottom:2px solid {TIER_COLORS[i % len(TIER_COLORS)]};">{html.escape(t.get("name",""))}</th>'
                for i, t in enumerate(tiers)
            )
            table_rows = ""
            for key, label in ROW_KEYS:
                vals = [t.get(key, shared.get(key, "")) for t in tiers]
                if not any(vals):
                    continue
                if len(set(str(v) for v in vals)) == 1 and vals[0]:
                    cells = f'<td colspan="{n}" class="sr-td sr-shared">{html.escape(str(vals[0]))}</td>'
                else:
                    cells = "".join(
                        f'<td class="sr-td">{html.escape(str(v)).replace(chr(10),"<br>") if v else "&#8212;"}</td>'
                        for v in vals
                    )
                table_rows += f'<tr><td class="sr-row-label">{html.escape(label)}</td>{cells}</tr>'
            notes_html = f'<p class="sr-notes">{html.escape(sr["notes"])}</p>' if sr.get("notes") else ""
            sys_req_block = f"""
<section class="content-section" id="requisitos">
  <div class="section-eyebrow">&#x1F5A5;&#xFE0F; Requisitos de Sistema &mdash; PC</div>
  <div class="sr-scroll">
    <table class="sr-full">
      <thead><tr><th class="sr-corner"></th>{tier_headers}</tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>
  {notes_html}
</section>"""
        else:
            def simple_rows(spec):
                rows = ""
                for k, label in ROW_KEYS:
                    v = spec.get(k, "")
                    if v:
                        rows += f'<tr><td class="sr-row-label">{html.escape(label)}</td><td class="sr-td">{html.escape(str(v))}</td></tr>'
                return rows
            min_r = simple_rows(sr.get("minimum", {}))
            rec_r = simple_rows(sr.get("recommended", {}))
            cols = ""
            if min_r:
                cols += f'<div class="sr-col"><div class="sr-tier-badge" style="background:{TIER_COLORS[0]}22;border-bottom:2px solid {TIER_COLORS[0]}">M\u00ednimo</div><table class="sr-full">{min_r}</table></div>'
            if rec_r:
                cols += f'<div class="sr-col"><div class="sr-tier-badge" style="background:{TIER_COLORS[2]}22;border-bottom:2px solid {TIER_COLORS[2]}">Recomendado</div><table class="sr-full">{rec_r}</table></div>'
            if cols:
                sys_req_block = f"""
<section class="content-section" id="requisitos">
  <div class="section-eyebrow">&#x1F5A5;&#xFE0F; Requisitos de Sistema &mdash; PC</div>
  <div class="sr-two-col">{cols}</div>
</section>"""
    # --- affiliate buy block ---
    affiliate_block = ""
    has_ml  = bool(game.affiliate_ml)
    has_amz = bool(game.affiliate_amz)
    if has_ml or has_amz:
        icon_cart = '<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" style="width:26px;height:26px;flex-shrink:0"><path d="M7 18c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zM5.2 5H3V3H1v2h2l3.6 7.6L5.25 15A2 2 0 007 18h14v-2H7.42l1.1-2H19a2 2 0 001.76-1.06L23 7H5.2z"/></svg>'
        btn_ml = ""
        btn_amz = ""
        if has_ml:
            btn_ml = f'<a class="aff-btn aff-ml" href="{html.escape(game.affiliate_ml)}" target="_blank" rel="noopener nofollow sponsored">{icon_cart}<span><strong>Mercado Livre</strong><small>Ver oferta</small></span></a>'
        if has_amz:
            btn_amz = f'<a class="aff-btn aff-amz" href="{html.escape(game.affiliate_amz)}" target="_blank" rel="noopener nofollow sponsored">{icon_cart}<span><strong>Amazon</strong><small>Ver oferta</small></span></a>'
        affiliate_block = f"""
<section class="content-section" id="comprar">
  <div class="section-eyebrow">\U0001f6d2 Encomende este Jogo</div>
  <p class="aff-disclaimer">Links de afiliados \u2014 comprando por aqui voc\u00ea apoia o faltapoco.com.br sem custo extra.</p>
  <div class="aff-grid">{btn_ml}{btn_amz}</div>
</section>"""

    # --- synopsis ---
    synopsis_block = ""
    if game.description and game.description.strip():
        synopsis_block = f"""
<section class="content-section" id="sobre">
  <div class="section-eyebrow">📖 Sobre o Jogo</div>
  <p class="synopsis-text">{html.escape(game.description)}</p>
</section>"""

    # --- news with thumb ---
    news_block = ""
    if game.news:
        news_items_html = ""
        for n in game.news:
            thumb_html = ""
            if n.get("thumb"):
                thumb_html = f'<img class="news-thumb" src="{html.escape(n["thumb"])}" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            date_str = f' · {html.escape(n["date"])}' if n.get("date") else ""
            news_items_html += f"""
  <a class="news-card" href="{html.escape(n.get('url', '#'))}" target="_blank" rel="noopener">
    {thumb_html}
    <div class="news-body">
      <div class="news-title">{html.escape(n.get('title', ''))}</div>
      <div class="news-meta">{html.escape(n.get('source', ''))}{date_str}</div>
    </div>
  </a>"""
        news_block = f"""
<section class="content-section" id="noticias">
  <div class="section-eyebrow">📰 Últimas Notícias</div>
  <div class="news-list">{news_items_html}
  </div>
</section>"""

    # --- reviews ---
    reviews_block = ""
    if game.reviews:
        rev_items_html = ""
        for r in game.reviews:
            rev_items_html += f"""
  <a class="review-card" href="{html.escape(r.get('url', '#'))}" target="_blank" rel="noopener">
    <div class="review-score">{html.escape(r.get('score', ''))}</div>
    <div>
      <div class="review-vehicle">{html.escape(r.get('vehicle', ''))}</div>
      <div class="review-excerpt">{html.escape(r.get('excerpt', ''))}</div>
    </div>
  </a>"""
        reviews_block = f"""
<section class="content-section" id="reviews">
  <div class="section-eyebrow">⭐ Reviews</div>
  <div class="reviews-list">{rev_items_html}
  </div>
</section>"""

    # --- premium blocks (GTA VI e jogos com premium=True) ---
    premium_blocks = ""
    is_premium = game.premium or game.slug == "gta-6"
    if is_premium:
        premium_blocks = render_premium_blocks(game, all_games or [])

    # --- dev / pub meta ---
    dev_pub = ""
    if game.developer or game.publisher:
        parts = []
        if game.developer:
            parts.append(f"<span><span class='meta-label'>Dev</span> {html.escape(game.developer)}</span>")
        if game.publisher and game.publisher != game.developer:
            parts.append(f"<span><span class='meta-label'>Pub</span> {html.escape(game.publisher)}</span>")
        dev_pub = '<div class="dev-pub">' + " · ".join(parts) + "</div>"

    # --- JSON-LD ---
    json_ld = render_json_ld(game)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{html.escape(game.name)} — Data de Lançamento, Trailer e Countdown | FaltaPouco</title>
<meta name="description" content="{html.escape((game.description or game.name + ' — datas, trailer e notícias.')[:155])}">
<meta name="keywords" content="{html.escape(game.name)}, data de lançamento {html.escape(game.name)}, quando sai {html.escape(game.name)}, trailer {html.escape(game.name)}, {html.escape(', '.join(game.platforms))}, lançamento jogos 2026, faltapoco">
<link rel="canonical" href="{html.escape(game.page_url)}">
<link rel="sitemap" type="application/xml" href="{BASE_URL}/sitemap.xml">
<meta property="og:site_name" content="FaltaPouco">
<meta property="og:title" content="{html.escape(game.name)} — Data de Lançamento e Countdown">
<meta property="og:description" content="{html.escape((game.description or game.name + ' — datas, trailer e notícias.')[:200])}">
<meta property="og:type" content="game">
<meta property="og:url" content="{html.escape(game.page_url)}">
<meta property="og:image" content="{html.escape(game.page_url)}bg.jpg">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="720">
<meta property="og:image:alt" content="Capa de {html.escape(game.name)}">
<meta property="og:locale" content="pt_BR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@faltapoco">
<meta name="twitter:title" content="{html.escape(game.name)} — FaltaPouco">
<meta name="twitter:description" content="{html.escape((game.description or '')[:200])}">
<meta name="twitter:image" content="{html.escape(game.page_url)}bg.jpg">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="alternate" type="application/json" href="{html.escape(game.api_url)}">
<script type="application/ld+json">{json_ld}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,600;0,700;1,300&family=Sora:wght@300;400;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:        #080809;
    --surface:   #111114;
    --surface2:  #18181c;
    --border:    rgba(255,255,255,0.07);
    --border2:   rgba(255,255,255,0.13);
    --text:      #f0efe8;
    --muted:     rgba(240,239,232,0.48);
    --muted2:    rgba(240,239,232,0.24);
    --accent:    #c9a84c;
    --accent2:   #e8d5a3;
    --accent-bg: rgba(201,168,76,0.08);
    --accent-bd: rgba(201,168,76,0.22);
    --green:     #22c55e;
    --purple:    #a855f7;
    --blue:      #3b82f6;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 400; line-height: 1.65;
    min-height: 100vh; -webkit-font-smoothing: antialiased;
  }}

  /* NAV */
  nav {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(8,8,9,0.92); backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 0 2rem; height: 58px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .nav-logo {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: 1rem; letter-spacing: -0.02em;
    color: var(--text); text-decoration: none;
  }}
  .nav-logo span {{ color: var(--accent); }}
  .nav-logo small {{ color: var(--muted); font-weight: 400; font-size: 0.82em; }}
  .nav-back {{
    font-size: 0.8rem; font-weight: 500;
    color: var(--muted); text-decoration: none;
    letter-spacing: 0.04em; transition: color 0.2s;
    display: flex; align-items: center; gap: 0.35rem;
  }}
  .nav-back:hover {{ color: var(--text); }}

  /* HERO */
  .hero {{
    position: relative; overflow: hidden; min-height: 520px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: flex-end;
    padding: 5rem 2rem 3.5rem; text-align: center;
  }}
  /* bg fica fixed atrás da hero — preenche a viewport inteira,
     overflow:hidden no .hero a clippa corretamente */
  .hero-bg {{
    position: absolute; inset: 0; z-index: 0; background: var(--bg);
  }}
  .hero-bg img {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center center;
    opacity: 0.55;
    /* parallax via transform — desce mais devagar que o scroll */
    will-change: transform;
  }}
  .hero-vignette {{
    position: absolute; inset: 0; z-index: 1;
    background:
      radial-gradient(ellipse at 50% 30%, transparent 10%, rgba(8,8,9,0.55) 60%, rgba(8,8,9,0.98) 100%),
      linear-gradient(to bottom, rgba(8,8,9,0.3) 0%, transparent 20%, transparent 45%, rgba(8,8,9,1) 100%);
  }}
  .hero > * {{ position: relative; z-index: 2; }}

  /* HERO STATUS BADGE */
  .hero-badge {{
    display: inline-flex; align-items: center;
    font-size: 0.65rem; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; padding: 5px 16px;
    border-top: 1px solid rgba(201,168,76,0.45);
    border-bottom: 1px solid rgba(201,168,76,0.45);
    color: var(--accent); margin-bottom: 1.2rem;
  }}
  .hero-badge.status-released  {{ border-color: rgba(168,85,247,.45); color: var(--purple); }}
  .hero-badge.status-confirmed {{ border-color: rgba(34,197,94,.45);  color: var(--green); }}
  .hero-badge.status-window    {{ border-color: rgba(59,130,246,.45);  color: var(--blue); }}
  .hero-badge.status-unknown   {{ border-color: rgba(255,255,255,.15); color: var(--muted); }}

  .hero-eyebrow {{
    font-size: 0.68rem; font-weight: 600; letter-spacing: 0.45em;
    text-transform: uppercase; color: var(--accent); margin-bottom: 0.5rem;
  }}
  .hero-title {{
    font-family: 'Cormorant Garamond', Georgia, serif;
    font-size: clamp(3rem, 9vw, 7.5rem);
    font-weight: 300; line-height: 0.92;
    letter-spacing: 0.1em; text-transform: uppercase; color: var(--text);
    filter: drop-shadow(0 0 50px rgba(201,168,76,0.15)); margin-bottom: 1.1rem;
  }}
  .meta-label {{ color: var(--muted2); font-size: 0.65rem; margin-right: 0.3em; }}
  .dev-pub {{
    display: flex; align-items: center; justify-content: center;
    gap: 1.2rem; flex-wrap: wrap; margin-bottom: 1.4rem;
    font-size: 0.76rem; color: var(--muted);
  }}

  /* PLATFORM BADGES in HERO */
  .hero-platforms {{
    display: flex; flex-wrap: wrap; justify-content: center;
    gap: 8px; margin-bottom: 2rem;
  }}

  /* GOLD DIVIDER */
  .gold-line {{
    width: 160px; height: 1px;
    background: linear-gradient(to right, transparent, var(--accent), transparent);
    margin: 0 auto 2.5rem;
  }}

  /* COUNTDOWN */
  .countdown-wrap {{ margin-bottom: 1.8rem; }}
  .countdown {{
    display: flex; align-items: flex-end; justify-content: center; gap: 0;
  }}
  .days-block {{
    display: flex; flex-direction: column; align-items: center;
    padding-right: 28px; margin-right: 10px;
    border-right: 1px solid rgba(201,168,76,0.18);
  }}
  .days-number {{
    font-family: 'Cormorant Garamond', serif;
    font-size: clamp(5rem, 14vw, 11rem);
    font-weight: 600; line-height: 0.9; color: var(--accent);
    text-shadow: 0 0 60px rgba(201,168,76,0.25), 0 2px 4px rgba(0,0,0,0.7);
  }}
  /* dias: flip somente quando mudar */
  .days-number.flip {{ animation: flipDays 0.4s ease; }}
  @keyframes flipDays {{
    0%   {{ transform: scaleY(1); opacity: 1 }}
    45%  {{ transform: scaleY(0); opacity: 0.1 }}
    100% {{ transform: scaleY(1); opacity: 1 }}
  }}
  .unit-label {{
    font-size: 0.52rem; font-weight: 700;
    letter-spacing: 0.3em; text-transform: uppercase;
    color: var(--muted); margin-top: 6px;
  }}

  /* HMS — cada unidade animada de forma independente */
  .hms-block {{
    display: flex; flex-direction: column; align-items: center; padding-left: 28px;
  }}
  .hms-inner {{
    display: flex; align-items: baseline; gap: 0;
    font-family: 'Cormorant Garamond', serif;
    font-size: clamp(2.2rem, 5.5vw, 5rem);
    font-weight: 300; line-height: 1;
    color: var(--text); opacity: 0.72; letter-spacing: 0.04em;
  }}
  /* separador ':' fixo, nunca anima */
  .hms-sep {{ padding: 0 2px; opacity: 0.35; }}
  /* cada unidade tem overflow hidden para conter o slide */
  .hms-unit {{ display: inline-block; min-width: 2ch; text-align: center; overflow: hidden; position: relative; }}
  /* roll: slide suave apenas na unidade que mudou */
  .hms-unit.roll {{ animation: rollUnit 0.22s cubic-bezier(0.4,0,0.2,1); }}
  @keyframes rollUnit {{
    0%   {{ transform: translateY(0);    opacity: 1 }}
    40%  {{ transform: translateY(-30%); opacity: 0.15 }}
    41%  {{ transform: translateY(30%);  opacity: 0.15 }}
    100% {{ transform: translateY(0);    opacity: 0.72 }}
  }}

  /* LAUNCH DATE + PROGRESS */
  .launch-date-label {{
    font-size: 0.7rem; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 1.8rem;
  }}
  .launch-date-label strong {{ color: var(--accent2); font-weight: 500; }}

  .progress-wrap {{ width: 100%; max-width: 420px; margin: 0 auto 2.8rem; }}
  .progress-header {{
    display: flex; justify-content: space-between;
    font-size: 0.56rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 8px;
  }}
  .progress-track {{ width: 100%; height: 2px; background: rgba(255,255,255,0.05); position: relative; }}
  .progress-fill {{
    height: 100%; width: 0%;
    background: linear-gradient(to right, rgba(201,168,76,0.3), var(--accent), #f0d878);
    transition: width 1.8s cubic-bezier(0.4,0,0.2,1); position: relative;
  }}
  .progress-fill::after {{
    content: '◆'; position: absolute; right: -5px; top: -7px;
    font-size: 7px; color: var(--accent); filter: drop-shadow(0 0 5px var(--accent));
  }}
  .progress-pct {{
    font-size: 0.58rem; letter-spacing: 0.1em;
    color: var(--accent); margin-top: 6px; text-align: right; font-weight: 500;
  }}

  /* LAUNCHED STATE */
  .launched-box {{
    display: none; flex-direction: column; align-items: center; gap: 12px;
    font-family: 'Cormorant Garamond', serif;
    font-size: clamp(2rem, 5vw, 5rem); font-weight: 600; color: var(--accent);
    text-shadow: 0 0 40px rgba(201,168,76,0.5); margin-bottom: 2rem;
  }}
  .launched-box span {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: clamp(0.8rem, 2vw, 1.1rem);
    color: var(--text); letter-spacing: 0.15em; font-weight: 300;
  }}

  /* CONTENT */
  .page-wrap {{ max-width: 860px; margin: 0 auto; padding: 0 1.5rem 5rem; }}
  .content-section {{
    margin-top: 3rem; padding-top: 2.8rem; border-top: 1px solid var(--border);
  }}
  .content-section:first-child {{ border-top: none; margin-top: 0; }}
  .section-eyebrow {{
    font-size: 0.62rem; font-weight: 700; letter-spacing: 0.22em;
    text-transform: uppercase; color: var(--accent); margin-bottom: 1.2rem;
  }}

  /* INFO GRID */
  .info-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem;
  }}
  .info-item {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.9rem 1rem;
  }}
  .info-label {{
    font-size: 0.58rem; font-weight: 600; letter-spacing: 0.14em;
    text-transform: uppercase; color: var(--muted2); margin-bottom: 0.35rem;
  }}
  .info-value {{
    font-family: 'Sora', sans-serif; font-weight: 600; font-size: 0.88rem; color: var(--text);
  }}
  .info-value a {{ color: var(--muted); font-size: 0.76rem; font-weight: 400; text-decoration: none; }}
  .info-value a:hover {{ color: var(--accent); }}

  /* SYNOPSIS */
  .synopsis-text {{
    font-size: 0.96rem; color: rgba(240,239,232,0.75);
    line-height: 1.85; max-width: 720px;
  }}

  /* VIDEO */
  .video-wrap {{
    position: relative; padding-top: 56.25%;
    border-radius: 10px; overflow: hidden;
    border: 1px solid var(--border2); background: #000;
  }}
  .video-wrap iframe {{
    position: absolute; inset: 0; width: 100%; height: 100%; border: 0;
  }}
  .video-meta {{
    font-size: 0.7rem; color: var(--muted2); margin-top: 0.65rem; letter-spacing: 0.04em;
  }}
  .video-meta a {{ color: var(--muted); text-decoration: none; transition: color 0.15s; }}
  .video-meta a:hover {{ color: var(--accent); }}

  /* NEWS with THUMB */
  .news-list {{ display: flex; flex-direction: column; gap: 0.7rem; }}
  .news-card {{
    display: flex; align-items: center; gap: 1rem;
    padding: 0.9rem 1.1rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent-bd);
    border-radius: 8px;
    text-decoration: none; color: inherit;
    transition: border-left-color 0.2s, background 0.15s;
    overflow: hidden;
  }}
  .news-card:hover {{ border-left-color: var(--accent); background: var(--surface2); }}
  .news-thumb {{
    width: 80px; height: 52px; object-fit: cover;
    border-radius: 5px; flex-shrink: 0; background: var(--surface2);
  }}
  .news-body {{ flex: 1; min-width: 0; }}
  .news-title {{
    font-family: 'Sora', sans-serif; font-weight: 600;
    font-size: 0.87rem; letter-spacing: -0.01em;
    margin-bottom: 0.28rem; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .news-meta {{ font-size: 0.68rem; color: var(--muted2); letter-spacing: 0.04em; }}

  /* REVIEWS */
  .reviews-list {{ display: flex; flex-direction: column; gap: 0.6rem; }}
  .review-card {{
    display: flex; align-items: center; gap: 1.2rem; padding: 1rem 1.2rem;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; text-decoration: none; color: inherit; transition: border-color 0.15s;
  }}
  .review-card:hover {{ border-color: var(--border2); }}
  .review-score {{
    font-family: 'Cormorant Garamond', serif; font-weight: 700;
    font-size: 1.6rem; color: var(--accent); white-space: nowrap; min-width: 48px; text-align: center;
  }}
  .review-vehicle {{ font-family: 'Sora', sans-serif; font-weight: 600; font-size: 0.84rem; margin-bottom: 0.2rem; }}
  .review-excerpt {{ font-size: 0.76rem; color: var(--muted); line-height: 1.5; }}

  /* FOOTER */
  footer {{
    border-top: 1px solid var(--border); padding: 2rem 1.5rem; text-align: center;
    font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: rgba(240,239,232,0.18); max-width: 860px; margin: 0 auto;
  }}

  @keyframes pulse {{
    0%,100% {{ opacity:1; transform:scale(1) }}
    50%      {{ opacity:0.3; transform:scale(0.6) }}
  }}
  @media (max-width: 600px) {{
    .countdown {{ flex-direction: column; align-items: center; gap: 16px; }}
    .days-block {{ border-right: none; border-bottom: 1px solid rgba(201,168,76,0.18); padding-right: 0; padding-bottom: 16px; margin-right: 0; }}
    .hms-block {{ padding-left: 0; }}
    nav {{ padding: 0 1rem; }}
    .hero {{ padding: 4rem 1.2rem 3rem; }}
    .page-wrap {{ padding: 0 1rem 4rem; }}
    .news-thumb {{ width: 64px; height: 42px; }}
    .sr-two-col {{ grid-template-columns: 1fr; }}
  }}

  /* SYS REQ — tabela multi-tier */
  .sr-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; border: 1px solid var(--border); }}
  .sr-full {{ width: 100%; border-collapse: collapse; font-size: 0.76rem; min-width: 520px; }}
  .sr-full thead th {{ padding: 0.55rem 0.9rem; font-size: 0.62rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); text-align: center; white-space: nowrap; }}
  .sr-corner {{ width: 130px; min-width: 110px; background: var(--surface2); }}
  .sr-row-label {{ font-size: 0.65rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted2); padding: 0.65rem 0.9rem; background: var(--surface2); border-right: 1px solid var(--border); white-space: nowrap; vertical-align: top; }}
  .sr-td {{ padding: 0.65rem 0.9rem; color: var(--text); border-left: 1px solid var(--border); vertical-align: top; line-height: 1.55; text-align: center; }}
  .sr-shared {{ text-align: center; color: var(--muted); font-style: italic; }}
  .sr-full tbody tr {{ border-top: 1px solid var(--border); transition: background 0.1s; }}
  .sr-full tbody tr:hover {{ background: rgba(255,255,255,0.025); }}
  .sr-notes {{ font-size: 0.68rem; color: var(--muted2); margin-top: 0.75rem; line-height: 1.6; }}
  .sr-two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  .sr-col {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  .sr-tier-badge {{ font-size: 0.62rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); padding: 0.5rem 0.9rem; }}

  /* AFFILIATE */
  .aff-disclaimer {{ font-size: 0.68rem; color: var(--muted2); margin-bottom: 1rem; letter-spacing: 0.03em; }}
  .aff-grid {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
  .aff-btn {{ display: inline-flex; align-items: center; gap: 0.8rem; padding: 0.9rem 1.5rem; border-radius: 10px; min-width: 190px; text-decoration: none; border: 1px solid transparent; transition: transform 0.15s, box-shadow 0.15s; }}
  .aff-btn:hover {{ transform: translateY(-2px); }}
  .aff-btn span {{ display: flex; flex-direction: column; gap: 0.1rem; }}
  .aff-btn strong {{ font-size: 0.9rem; font-weight: 700; font-family: 'Sora',sans-serif; letter-spacing: -0.01em; }}
  .aff-btn small {{ font-size: 0.68rem; opacity: 0.75; letter-spacing: 0.04em; }}
  .aff-ml {{ background: linear-gradient(135deg,#ffe600,#f5d000); color: #1a1200; border-color: #e8c800; }}
  .aff-ml:hover {{ box-shadow: 0 6px 20px rgba(255,230,0,0.35); }}
  .aff-amz {{ background: linear-gradient(135deg,#ff9900,#e68a00); color: #1a0800; border-color: #cc7700; }}
  .aff-amz:hover {{ box-shadow: 0 6px 20px rgba(255,153,0,0.35); }}
</style>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-YJ2GY5FM9B"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-YJ2GY5FM9B');
</script>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">falta<span>pouco</span><small>.com.br</small></a>
  <a href="/" class="nav-back">← Todos os jogos</a>
</nav>

<section class="hero">
  <div class="hero-bg">{bg_html}</div>
  <div class="hero-vignette"></div>

  <div class="hero-badge status-{html.escape(game.status)}">{badge_dot}{html.escape(badge)}</div>
  <div class="hero-eyebrow">{html.escape(game.developer or '')}</div>
  <h1 class="hero-title">{html.escape(game.name)}</h1>

  {dev_pub}

  <div class="hero-platforms">
    {platforms_html}
  </div>

  <div class="gold-line"></div>

  <div class="countdown-wrap">
    <div id="countdownWrap">
      <div class="countdown">
        <div class="days-block">
          <div class="days-number" id="cd-days">--</div>
          <div class="unit-label">Dias</div>
        </div>
        <div class="hms-block">
          <div class="hms-inner">
            <span class="hms-unit" id="cd-h">--</span>
            <span class="hms-sep">:</span>
            <span class="hms-unit" id="cd-m">--</span>
            <span class="hms-sep">:</span>
            <span class="hms-unit" id="cd-s">--</span>
          </div>
          <div class="unit-label">Horas · Min · Seg</div>
        </div>
      </div>
    </div>
    <div class="launched-box" id="launchedBox">
      🎮 {html.escape(game.name)} Chegou!
      <span>Disponível agora para {html.escape(platforms_text)}</span>
    </div>
  </div>

  <div class="launch-date-label">
    Lançamento: <strong>{html.escape(game.release_display)}</strong>
    {f'<br><span style="font-size:0.62rem;opacity:0.7;letter-spacing:0.15em">⏰ {html.escape(game.release_time_display)}</span>' if game.release_time_display else ''}
  </div>

  <div class="progress-wrap">
    <div class="progress-header"><span>Anúncio</span><span>Lançamento</span></div>
    <div class="progress-track">
      <div class="progress-fill" id="progressFill"></div>
    </div>
    <div class="progress-pct" id="progressPct">—</div>
  </div>
</section>

<div class="page-wrap">

  <section class="content-section">
    <div class="section-eyebrow">📋 Detalhes</div>
    <div class="info-grid">
      <div class="info-item">
        <div class="info-label">Status</div>
        <div class="info-value">{html.escape(badge)}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Data de Lançamento</div>
        <div class="info-value">{html.escape(game.release_display)}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Desenvolvedor</div>
        <div class="info-value">{html.escape(game.developer or '—')}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Publisher</div>
        <div class="info-value">{html.escape(game.publisher or '—')}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Plataformas</div>
        <div class="info-value">{html.escape(', '.join(game.platforms) if game.platforms else '—')}</div>
      </div>
      <div class="info-item">
        <div class="info-label">API</div>
        <div class="info-value"><a href="{html.escape(game.api_url)}">JSON deste jogo ↗</a></div>
      </div>
    </div>
  </section>

  {synopsis_block}
  {video_block}
  {sys_req_block}
  {affiliate_block}
  {news_block}
  {reviews_block}
  {premium_blocks}

  <footer>Fan site independente · Não afiliado a {html.escape(game.publisher or 'qualquer publisher')}</footer>
</div>

<script>
  const launch    = {ymd_js};
  const announced = {announced_js};
  const gameName  = {json.dumps(game.name)};

  const elDays   = document.getElementById('cd-days');
  const elH      = document.getElementById('cd-h');
  const elM      = document.getElementById('cd-m');
  const elS      = document.getElementById('cd-s');
  const cwrap    = document.getElementById('countdownWrap');
  const lbox     = document.getElementById('launchedBox');
  const progFill = document.getElementById('progressFill');
  const progPct  = document.getElementById('progressPct');

  function pad(n) {{ return String(n).padStart(2, '0'); }}

  // Anima SOMENTE o elemento que mudou — zero flicker nos demais
  function rollUpdate(el, newVal) {{
    if (el.textContent === newVal) return;   // nada mudou, não toca
    el.textContent = newVal;
    el.classList.remove('roll');
    void el.offsetWidth;                     // reflow para reiniciar animation
    el.classList.add('roll');
  }}

  let prevDays = null, prevH = null, prevM = null;

  function tick() {{
    if (!launch) {{
      elDays.textContent = '—'; elH.textContent = '--';
      elM.textContent = '--';   elS.textContent = '--';
      return;
    }}
    const diff = launch - new Date();
    if (diff <= 0) {{
      cwrap.style.display = 'none';
      lbox.style.display  = 'flex';
      document.title = '🎮 ' + gameName + ' — disponível!';
      updateProgress();
      return;
    }}
    const totalSecs = Math.floor(diff / 1000);
    const d = Math.floor(totalSecs / 86400);
    const h = Math.floor((totalSecs % 86400) / 3600);
    const m = Math.floor((totalSecs % 3600) / 60);
    const s = totalSecs % 60;

    // Dias: flip animation, só quando o valor muda (uma vez por dia)
    if (d !== prevDays) {{
      elDays.textContent = d;
      elDays.classList.remove('flip');
      void elDays.offsetWidth;
      elDays.classList.add('flip');
      prevDays = d;
    }}

    // Horas: roll só quando mudar (uma vez por hora)
    if (h !== prevH) {{ rollUpdate(elH, pad(h)); prevH = h; }}

    // Minutos: roll só quando mudar (uma vez por minuto)
    if (m !== prevM) {{ rollUpdate(elM, pad(m)); prevM = m; }}

    // Segundos: atualiza todo segundo, roll suave — é o único que anima constantemente
    rollUpdate(elS, pad(s));

    document.title = 'Faltam ' + d + ' dias — ' + gameName;
    updateProgress();
  }}

  function updateProgress() {{
    if (!launch || !announced) return;
    const now   = new Date();
    const total   = launch - announced;
    const elapsed = now - announced;
    const pct = Math.min(100, Math.max(0, (elapsed / total) * 100));
    progFill.style.width = pct.toFixed(1) + '%';
    progPct.textContent  = pct.toFixed(1) + '%';
  }}

  tick();
  setInterval(tick, 1000);
  setTimeout(updateProgress, 300);

  // Parallax suave na imagem de fundo
  (function() {{
    const img = document.querySelector('.hero-bg img');
    if (!img) return;
    function onScroll() {{
      const scrollY = window.scrollY;
      // Move a imagem a 40% da velocidade do scroll — efeito parallax
      img.style.transform = 'translateY(' + (scrollY * 0.4) + 'px)';
    }}
    window.addEventListener('scroll', onScroll, {{ passive: true }});
  }})();
</script>
</body>
</html>"""

# ── PREMIUM BLOCKS — blocos extras para páginas de alta retenção ───────────────

def render_premium_blocks(game: "GameRecord", all_games: List["GameRecord"]) -> str:
    """
    Renderiza blocos extras de retenção para jogos premium.
    Ativado quando game.premium == True ou game.slug == "gta-6".
    Retorna HTML completo dos blocos extras.
    """
    blocks = []

    # Bloco 1: O que já foi confirmado
    if game.confirmed_features:
        items_html = "".join(
            f'<li class="prem-feature-item"><span class="prem-check">✓</span>{html.escape(f)}</li>'
            for f in game.confirmed_features
        )
        blocks.append(f"""
<section class="content-section" id="confirmado">
  <div class="section-eyebrow">✅ O Que Já Foi Confirmado</div>
  <ul class="prem-feature-list">{items_html}</ul>
</section>""")

    # Bloco 2: História e ambientação
    if game.story:
        blocks.append(f"""
<section class="content-section" id="historia">
  <div class="section-eyebrow">🗺️ História e Ambientação</div>
  <p class="synopsis-text">{html.escape(game.story)}</p>
</section>""")

    # Bloco 3: Por que importa
    if game.context:
        blocks.append(f"""
<section class="content-section" id="contexto">
  <div class="section-eyebrow">🎯 Por Que Este Jogo Importa</div>
  <p class="synopsis-text">{html.escape(game.context)}</p>
</section>""")

    # Bloco 4: Prepare seu setup (apenas jogos com PC nas plataformas)
    if "PC" in game.platforms and (game.affiliate_ml or game.affiliate_amz):
        icon_cart = '<svg viewBox="0 0 24 24" fill="currentColor" style="width:22px;height:22px;flex-shrink:0"><path d="M7 18c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zM5.2 5H3V3H1v2h2l3.6 7.6L5.25 15A2 2 0 007 18h14v-2H7.42l1.1-2H19a2 2 0 001.76-1.06L23 7H5.2z"/></svg>'
        btn_ml  = f'<a class="aff-btn aff-ml" href="{html.escape(game.affiliate_ml)}" target="_blank" rel="noopener nofollow sponsored">{icon_cart}<span><strong>Mercado Livre</strong><small>Pré-venda e lançamento</small></span></a>' if game.affiliate_ml else ""
        btn_amz = f'<a class="aff-btn aff-amz" href="{html.escape(game.affiliate_amz)}" target="_blank" rel="noopener nofollow sponsored">{icon_cart}<span><strong>Amazon</strong><small>Pré-venda e lançamento</small></span></a>' if game.affiliate_amz else ""
        blocks.append(f"""
<section class="content-section" id="setup">
  <div class="section-eyebrow">🖥️ Prepare Seu Setup</div>
  <p class="synopsis-text" style="margin-bottom:1.2rem">Garanta já sua cópia e prepare o hardware para o lançamento.</p>
  <div class="aff-grid">{btn_ml}{btn_amz}</div>
</section>""")

    # Bloco 5: Jogos relacionados (linkagem interna)
    if game.related_games and all_games:
        slug_map = {g.slug: g for g in all_games}
        rel_cards = ""
        for slug in game.related_games[:6]:
            rel = slug_map.get(slug)
            if not rel:
                continue
            rel_cards += f"""
  <a class="rel-card" href="/jogos/{html.escape(rel.slug)}/">
    <div class="rel-name">{html.escape(rel.name)}</div>
    <div class="rel-date">{html.escape(rel.release_display)}</div>
    <div class="rel-plat">{html.escape(', '.join(rel.platforms[:2]))}</div>
  </a>"""
        if rel_cards:
            blocks.append(f"""
<section class="content-section" id="relacionados">
  <div class="section-eyebrow">🎮 Veja Também</div>
  <div class="rel-grid">{rel_cards}
  </div>
</section>""")

    # Bloco 6: SEO text (texto longo para indexação, visualmente discreto)
    if game.seo_text:
        blocks.append(f"""
<section class="content-section" id="mais-info">
  <div class="section-eyebrow">📝 Mais Informações</div>
  <p class="synopsis-text">{html.escape(game.seo_text)}</p>
</section>""")

    return "\n".join(blocks)


_PREMIUM_CSS = """
  /* PREMIUM BLOCKS */
  .prem-feature-list { list-style:none; display:flex; flex-direction:column; gap:0.55rem; }
  .prem-feature-item {
    display:flex; align-items:flex-start; gap:0.7rem;
    font-size:0.88rem; color:var(--text); line-height:1.55;
    padding:0.6rem 0.9rem;
    background:var(--surface); border:1px solid var(--border);
    border-radius:8px;
  }
  .prem-check {
    color:var(--green); font-weight:800; font-size:0.9rem;
    flex-shrink:0; margin-top:0.05rem;
  }
  .rel-grid {
    display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
    gap:0.75rem;
  }
  .rel-card {
    background:var(--surface); border:1px solid var(--border);
    border-radius:8px; padding:0.9rem 1rem;
    text-decoration:none; color:inherit;
    transition:border-color 0.15s, transform 0.15s;
    display:flex; flex-direction:column; gap:0.25rem;
  }
  .rel-card:hover { border-color:var(--border2); transform:translateY(-2px); }
  .rel-name { font-family:'Sora',sans-serif; font-weight:700; font-size:0.85rem; letter-spacing:-0.02em; color:var(--text); }
  .rel-date { font-size:0.72rem; color:var(--accent); font-weight:600; }
  .rel-plat { font-size:0.65rem; color:var(--muted); }
"""


# ── EVENTO — página editorial de hub de evento ─────────────────────────────────

def render_event_page(event: Dict[str, Any], games: List["GameRecord"]) -> str:
    """
    Gera página editorial de evento (ex: /xbox-partner-preview-2026/).
    event = {
        "slug": "xbox-partner-preview-2026",
        "title": "Xbox Partner Preview 2026",
        "subtitle": "Todos os jogos anunciados",
        "description": "...",
        "date": "2026-03-26",
        "organizer": "Microsoft",
        "hero_color": "#107C10",
    }
    """
    slug        = event["slug"]
    title       = event.get("title", slug)
    subtitle    = event.get("subtitle", "")
    description = event.get("description", "")
    date_str    = event.get("date", "")
    organizer   = event.get("organizer", "")
    hero_color  = event.get("hero_color", "#1a1a2e")
    page_url    = f"{BASE_URL}/{slug}/"

    # Filtra jogos deste evento
    event_games = [g for g in games if g.event == slug]
    # Ordena: high primeiro, depois medium, depois low; dentro de cada grupo por data
    priority_order = {"high": 0, "medium": 1, "low": 2, "": 3}
    event_games.sort(key=lambda g: (
        priority_order.get(g.priority, 3),
        parse_date_obj(g.release) or date.max,
        g.name.lower()
    ))

    total = len(event_games)
    high_count   = sum(1 for g in event_games if g.priority == "high")
    medium_count = sum(1 for g in event_games if g.priority == "medium")

    # Cards dos jogos
    cards_html = ""
    for g in event_games:
        priority_badge = ""
        if g.priority == "high":
            priority_badge = '<span class="ev-prio ev-prio-high">🔥 Destaque</span>'
        elif g.priority == "medium":
            priority_badge = '<span class="ev-prio ev-prio-med">⭐ Relevante</span>'

        atype_html = f'<span class="ev-atype">{html.escape(g.announcement_type)}</span>' if g.announcement_type else ""
        date_label = g.release_window_raw or g.release_display or "A confirmar"
        plat_badges = render_platform_badges(g.platforms)

        cards_html += f"""
  <a class="ev-card" href="/jogos/{html.escape(g.slug)}/">
    <div class="ev-card-top">
      {priority_badge}
      {atype_html}
    </div>
    <div class="ev-card-name">{html.escape(g.name)}</div>
    <div class="ev-card-date">📅 {html.escape(date_label)}</div>
    <div class="ev-card-plat">{plat_badges}</div>
  </a>"""

    # JSON-LD Event
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Event",
        "name": title,
        "description": description,
        "organizer": {"@type": "Organization", "name": organizer} if organizer else None,
        "startDate": date_str or None,
        "url": page_url,
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)} — Todos os Jogos Anunciados | FaltaPouco</title>
<meta name="description" content="{html.escape(description[:155] if description else title + ' — todos os jogos anunciados, datas e plataformas.')}">
<meta name="keywords" content="{html.escape(title)}, jogos {html.escape(title)}, lançamentos xbox 2026, novos jogos xbox, faltapoco">
<link rel="canonical" href="{page_url}">
<link rel="sitemap" type="application/xml" href="{BASE_URL}/sitemap.xml">
<meta property="og:site_name" content="FaltaPouco">
<meta property="og:title" content="{html.escape(title)} — FaltaPouco">
<meta property="og:description" content="{html.escape(description[:200] if description else title)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{page_url}">
<meta property="og:locale" content="pt_BR">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="index, follow">
<script type="application/ld+json">{jsonld}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:#0f0f10;--surface:#18181b;--surface2:#1f1f23;
    --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.12);
    --text:#f4f4f5;--muted:rgba(244,244,245,0.42);--muted2:rgba(244,244,245,0.22);
    --accent:#ff3b3b;--accent-bg:rgba(255,59,59,0.08);--accent-bd:rgba(255,59,59,0.22);
    --green:#22c55e;--blue:#3b82f6;--hype:#ff6b00;
    --ev-color:{html.escape(hero_color)};
    --radius:10px;--radius-lg:14px;
  }}
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  html{{scroll-behavior:smooth}}
  body{{background:var(--bg);color:var(--text);font-family:'Space Grotesk',sans-serif;line-height:1.6;min-height:100vh;-webkit-font-smoothing:antialiased}}
  nav{{position:sticky;top:0;z-index:100;background:rgba(15,15,16,0.92);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:0 2rem;height:60px;display:flex;align-items:center;justify-content:space-between}}
  .nav-logo{{font-family:'Sora',sans-serif;font-weight:800;font-size:1.1rem;letter-spacing:-0.03em;color:var(--text);text-decoration:none}}
  .nav-logo span{{color:var(--accent)}}
  .nav-back{{font-size:0.8rem;font-weight:500;color:var(--muted);text-decoration:none;transition:color 0.2s}}
  .nav-back:hover{{color:var(--text)}}
  .ev-hero{{background:linear-gradient(135deg,var(--ev-color)22,var(--bg));border-bottom:1px solid var(--border);padding:4rem 2rem 3rem;text-align:center;position:relative;overflow:hidden}}
  .ev-hero::before{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at 50% 0%,var(--ev-color)18,transparent 70%);pointer-events:none}}
  .ev-hero>*{{position:relative;z-index:1}}
  .ev-organizer{{font-size:0.7rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:var(--muted);margin-bottom:0.8rem}}
  .ev-title{{font-family:'Sora',sans-serif;font-weight:800;font-size:clamp(2rem,6vw,4rem);letter-spacing:-0.04em;line-height:1.05;margin-bottom:0.7rem}}
  .ev-title span{{color:var(--ev-color)}}
  .ev-subtitle{{font-size:0.95rem;color:var(--muted);max-width:520px;margin:0 auto 2rem}}
  .ev-stats{{display:flex;justify-content:center;gap:2.5rem;flex-wrap:wrap}}
  .ev-stat{{text-align:center}}
  .ev-stat-num{{font-family:'Sora',sans-serif;font-weight:800;font-size:1.8rem;letter-spacing:-0.04em;color:var(--ev-color)}}
  .ev-stat-label{{font-size:0.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;margin-top:0.1rem}}
  .wrap{{max-width:1120px;margin:0 auto;padding:3rem 2rem 5rem}}
  .ev-section-label{{font-family:'Sora',sans-serif;font-weight:700;font-size:1rem;letter-spacing:-0.02em;margin-bottom:1.2rem;padding-bottom:0.7rem;border-bottom:1px solid var(--border)}}
  .ev-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.9rem}}
  .ev-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.1rem;text-decoration:none;color:inherit;display:flex;flex-direction:column;gap:0.5rem;transition:border-color 0.15s,transform 0.15s}}
  .ev-card:hover{{border-color:var(--border2);transform:translateY(-2px)}}
  .ev-card-top{{display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap;min-height:1.4rem}}
  .ev-prio{{font-size:0.6rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;padding:0.18rem 0.5rem;border-radius:100px}}
  .ev-prio-high{{background:rgba(255,107,0,0.12);color:#ff8c00;border:1px solid rgba(255,107,0,0.25)}}
  .ev-prio-med{{background:rgba(245,158,11,0.1);color:#f59e0b;border:1px solid rgba(245,158,11,0.2)}}
  .ev-atype{{font-size:0.6rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;padding:0.18rem 0.5rem;border-radius:100px;background:rgba(255,255,255,0.05);color:var(--muted);border:1px solid var(--border)}}
  .ev-card-name{{font-family:'Sora',sans-serif;font-weight:700;font-size:0.92rem;letter-spacing:-0.02em;color:var(--text);margin-top:0.2rem}}
  .ev-card-date{{font-size:0.74rem;color:var(--muted)}}
  .ev-card-plat{{margin-top:0.3rem}}
  footer{{border-top:1px solid var(--border);padding:2rem;text-align:center;font-size:0.68rem;letter-spacing:0.1em;text-transform:uppercase;color:rgba(244,244,245,0.2)}}
  @media(max-width:600px){{.ev-stats{{gap:1.5rem}}.wrap{{padding:2rem 1rem 4rem}}}}
</style>
</head>
<body>
<nav>
  <a href="/" class="nav-logo">falta<span>pouco</span><small>.com.br</small></a>
  <a href="/" class="nav-back">← Todos os jogos</a>
</nav>

<div class="ev-hero">
  <div class="ev-organizer">{html.escape(organizer)} · {html.escape(date_str)}</div>
  <h1 class="ev-title"><span>{html.escape(title)}</span></h1>
  <p class="ev-subtitle">{html.escape(subtitle or description[:120])}</p>
  <div class="ev-stats">
    <div class="ev-stat"><div class="ev-stat-num">{total}</div><div class="ev-stat-label">Jogos anunciados</div></div>
    <div class="ev-stat"><div class="ev-stat-num">{high_count}</div><div class="ev-stat-label">Destaques</div></div>
    <div class="ev-stat"><div class="ev-stat-num">{medium_count}</div><div class="ev-stat-label">Relevantes</div></div>
  </div>
</div>

<div class="wrap">
  <div class="ev-section-label">🎮 Todos os Jogos Anunciados</div>
  <div class="ev-grid">{cards_html}
  </div>
</div>

<footer>FaltaPouco · Fan site independente · Não afiliado à {html.escape(organizer or 'Microsoft')}</footer>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-YJ2GY5FM9B"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-YJ2GY5FM9B');
</script>
</body>
</html>"""


def render_home(games: List[GameRecord]) -> str:
    # Build JS games array from real data
    js_games_entries = []
    hype_seeds_entries = []

    # Sort: featured (GTA VI) first, then by release date, then unknown
    def sort_key(g: GameRecord):
        if g.slug == "gta-6":
            return (0, date.min, g.name)
        if g.status == "released":
            return (2, parse_date_obj(g.release) or date.max, g.name)
        if g.status == "confirmed":
            return (1, parse_date_obj(g.release) or date.max, g.name)
        if g.status == "window":
            return (3, date.max, g.name)
        return (4, date.max, g.name)

    sorted_games = sorted(games, key=sort_key)

    for g in sorted_games:
        date_val = f'"{g.release}"' if g.release else "null"
        is_featured = "true" if g.slug == "gta-6" else "false"
        is_released = "true" if g.status == "released" else "false"
        platforms_js = json.dumps(g.platforms)
        status_js = g.status.lower()
        js_games_entries.append(
            f'    {{ id:{json.dumps(g.slug)}, name:{json.dumps(g.name)}, '
            f'date:{date_val}, status:{json.dumps(status_js)}, '
            f'platforms:{platforms_js}, featured:{is_featured}, launched:{is_released} }}'
        )
        # seed hype for known big titles
        seed_map = {
            "gta-6": 142, "marvel-wolverine": 87, "crimson-desert": 64,
            "fable-4": 58, "gears-of-war-eday": 51, "phantom-blade-zero": 43,
            "resident-evil-requiem": 39, "007-first-light": 9,
            "control-resonant": 28, "atomic-heart-2": 12, "fatal-frame-2-remake": 18,
            "marathon-2026": 15,
        }
        if g.slug in seed_map:
            hype_seeds_entries.append(f'    {json.dumps(g.slug)}: {seed_map[g.slug]}')

    js_games = "[\n" + ",\n".join(js_games_entries) + "\n  ]"
    hype_seeds_js = "{\n" + ",\n".join(hype_seeds_entries) + "\n  }"

    total_count = len(games)
    confirmed_count = sum(1 for g in games if g.status == "confirmed")
    released_count  = sum(1 for g in games if g.status == "released")
    website_jsonld  = render_website_jsonld(total_count)

    # Featured game (GTA VI if present, else first confirmed)
    featured = next((g for g in sorted_games if g.slug == "gta-6"), None)
    if not featured:
        featured = next((g for g in sorted_games if g.status == "confirmed" and g.release), None)
    if not featured:
        featured = sorted_games[0] if sorted_games else None

    if featured and featured.release:
        feat_date_parts = featured.release.split("-")
        feat_js_date = f"new Date({feat_date_parts[0]}, {int(feat_date_parts[1])-1}, {feat_date_parts[2]}, 0, 0, 0)"
        feat_date_display = html.escape(featured.release_display)
    else:
        feat_js_date = "null"
        feat_date_display = "A confirmar"

    feat_platforms_html = "".join(
        f'<span class="platform-tag">{html.escape(p)}</span>'
        for p in (featured.platforms if featured else [])
    )

    gta_days_approx = days_left(featured.release) if featured and featured.release else 0

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FaltaPouco — Datas de Lançamento e Countdowns de Jogos 2026</title>
<meta name="description" content="Countdowns em tempo real, datas de lançamento confirmadas e trailers dos jogos mais aguardados de 2026. GTA VI, Crimson Desert, Wolverine e muito mais.">
<meta name="keywords" content="data de lançamento jogos 2026, countdown jogos, GTA 6 data lançamento, jogos mais aguardados 2026, lançamentos PS5 Xbox PC, faltapoco">
<link rel="canonical" href="{BASE_URL}/">
<link rel="sitemap" type="application/xml" href="{BASE_URL}/sitemap.xml">
<meta property="og:site_name" content="FaltaPouco">
<meta property="og:title" content="FaltaPouco — Datas de Lançamento e Countdowns de Jogos 2026">
<meta property="og:description" content="Countdowns em tempo real e datas confirmadas dos jogos mais aguardados de 2026. GTA VI, Crimson Desert, Wolverine e mais {total_count} jogos monitorados.">
<meta property="og:type" content="website">
<meta property="og:url" content="{BASE_URL}/">
<meta property="og:image" content="{BASE_URL}/og-home.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="pt_BR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@faltapoco">
<meta name="twitter:title" content="FaltaPouco — Countdowns de Jogos 2026">
<meta name="twitter:description" content="Datas de lançamento em tempo real. GTA VI, Crimson Desert e mais {total_count} jogos.">
<meta name="twitter:image" content="{BASE_URL}/og-home.jpg">
<meta name="robots" content="index, follow, max-image-preview:large">
<script type="application/ld+json">{website_jsonld}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:        #0f0f10;
    --surface:   #18181b;
    --surface2:  #1f1f23;
    --border:    rgba(255,255,255,0.07);
    --border2:   rgba(255,255,255,0.12);
    --text:      #f4f4f5;
    --muted:     rgba(244,244,245,0.42);
    --muted2:    rgba(244,244,245,0.22);
    --accent:    #ff3b3b;
    --accent-h:  #ff5c5c;
    --accent-bg: rgba(255,59,59,0.08);
    --accent-bd: rgba(255,59,59,0.22);
    --green:     #22c55e;
    --green-bg:  rgba(34,197,94,0.08);
    --green-bd:  rgba(34,197,94,0.2);
    --blue:      #3b82f6;
    --blue-bg:   rgba(59,130,246,0.08);
    --blue-bd:   rgba(59,130,246,0.2);
    --yellow:    #f59e0b;
    --yellow-bg: rgba(245,158,11,0.08);
    --yellow-bd: rgba(245,158,11,0.2);
    --purple:    #a855f7;
    --purple-bg: rgba(168,85,247,0.08);
    --purple-bd: rgba(168,85,247,0.2);
    --hype:      #ff6b00;
    --hype-bg:   rgba(255,107,0,0.08);
    --hype-bd:   rgba(255,107,0,0.22);
    --radius:    10px;
    --radius-lg: 14px;
  }}
  *, *::before, *::after {{ margin:0; padding:0; box-sizing:border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 400;
    line-height: 1.6;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }}
  nav {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(15,15,16,0.92);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 0 2rem; height: 60px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .nav-logo {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: 1.1rem; letter-spacing: -0.03em;
    color: var(--text); text-decoration: none;
  }}
  .nav-logo span {{ color: var(--accent); }}
  .nav-logo small {{ color: var(--muted); font-weight: 400; font-size: 0.85em; }}
  .nav-right {{ display: flex; align-items: center; gap: 1.5rem; }}
  .nav-links {{ display: flex; gap: 1.5rem; list-style: none; }}
  .nav-links a {{
    font-size: 0.84rem; font-weight: 500;
    color: var(--muted); text-decoration: none; transition: color 0.15s;
  }}
  .nav-links a:hover {{ color: var(--text); }}
  .nav-pill {{
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.05em;
    color: var(--accent); background: var(--accent-bg);
    border: 1px solid var(--accent-bd); padding: 0.3rem 0.8rem;
    border-radius: 100px; text-decoration: none; transition: background 0.15s;
  }}
  .nav-pill:hover {{ background: rgba(255,59,59,0.14); }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 0 2rem; }}
  .hero {{ padding: 4.5rem 0 3rem; text-align: center; }}
  .hero-badge {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--accent);
    background: var(--accent-bg); border: 1px solid var(--accent-bd);
    padding: 0.28rem 0.8rem; border-radius: 100px; margin-bottom: 1.4rem;
  }}
  .hero-badge::before {{
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent); animation: pulse-dot 2s ease-in-out infinite;
  }}
  @keyframes pulse-dot {{ 0%,100%{{transform:scale(1);opacity:1}} 50%{{transform:scale(0.6);opacity:0.4}} }}
  .hero h1 {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: clamp(2.2rem, 5.5vw, 3.8rem);
    letter-spacing: -0.04em; line-height: 1.08; margin-bottom: 1rem;
  }}
  .hero h1 em {{ font-style: normal; color: var(--accent); }}
  .hero-sub {{
    font-size: 0.97rem; color: var(--muted);
    max-width: 500px; margin: 0 auto 2.5rem; line-height: 1.7;
  }}
  .hero-stats {{ display: flex; justify-content: center; gap: 2.5rem; flex-wrap: wrap; }}
  .hero-stat {{ text-align: center; }}
  .hero-stat-num {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: 1.9rem; letter-spacing: -0.04em;
  }}
  .n-red {{ color: var(--accent); }}
  .n-green {{ color: var(--green); }}
  .n-blue {{ color: var(--blue); }}
  .n-orange {{ color: var(--hype); }}
  .hero-stat-label {{ font-size: 0.72rem; color: var(--muted); letter-spacing: 0.05em; text-transform: uppercase; font-weight: 500; margin-top: 0.1rem; }}
  .hero-div {{ width: 1px; height: 36px; background: var(--border2); align-self: center; }}
  .search-section {{ padding: 2.5rem 0 0; }}
  .search-box {{ position: relative; max-width: 620px; margin: 0 auto 0.9rem; }}
  .search-box svg {{
    position: absolute; left: 1.1rem; top: 50%; transform: translateY(-50%);
    color: var(--muted); pointer-events: none; width: 17px; height: 17px;
  }}
  .search-input {{
    width: 100%; background: var(--surface); border: 1px solid var(--border2);
    border-radius: var(--radius); padding: 0.88rem 1rem 0.88rem 2.85rem;
    color: var(--text); font-family: 'Space Grotesk', sans-serif;
    font-size: 0.93rem; outline: none; transition: border-color 0.2s, box-shadow 0.2s;
  }}
  .search-input::placeholder {{ color: var(--muted2); }}
  .search-input:focus {{
    border-color: rgba(255,59,59,0.4);
    box-shadow: 0 0 0 3px rgba(255,59,59,0.07);
  }}
  .filter-row {{ display: flex; justify-content: center; gap: 0.45rem; flex-wrap: wrap; }}
  .filter-btn {{
    font-family: 'Space Grotesk', sans-serif; font-size: 0.76rem; font-weight: 500;
    color: var(--muted); background: var(--surface); border: 1px solid var(--border);
    padding: 0.32rem 0.82rem; border-radius: 100px; cursor: pointer; transition: all 0.15s;
  }}
  .filter-btn:hover {{ color: var(--text); border-color: var(--border2); background: var(--surface2); }}
  .filter-btn.active {{ color: var(--accent); border-color: var(--accent-bd); background: var(--accent-bg); }}
  .section-label {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 1.1rem; padding-top: 3.2rem;
  }}
  .section-title {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 1.02rem; letter-spacing: -0.02em;
    display: flex; align-items: center; gap: 0.45rem;
  }}
  .section-count {{
    font-size: 0.73rem; font-weight: 500; color: var(--muted);
    background: var(--surface); border: 1px solid var(--border);
    padding: 0.18rem 0.58rem; border-radius: 100px;
  }}
  .featured-card {{
    background: var(--surface); border: 1px solid var(--border2);
    border-radius: var(--radius-lg); padding: 1.8rem 2rem;
    display: grid; grid-template-columns: 1fr auto;
    gap: 2rem; align-items: center; position: relative;
    overflow: hidden; margin-bottom: 0.85rem; transition: border-color 0.2s;
  }}
  .featured-card::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--accent), #ff8080);
  }}
  .featured-card:hover {{ border-color: rgba(255,59,59,0.28); }}
  .badge {{
    display: inline-flex; align-items: center; gap: 0.32rem;
    font-size: 0.67rem; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; padding: 0.2rem 0.58rem;
    border-radius: 100px; margin-bottom: 0.65rem;
  }}
  .badge-confirmed {{ color: var(--green);  background: var(--green-bg);  border: 1px solid var(--green-bd); }}
  .badge-window    {{ color: var(--blue);   background: var(--blue-bg);   border: 1px solid var(--blue-bd); }}
  .badge-rumor     {{ color: var(--yellow); background: var(--yellow-bg); border: 1px solid var(--yellow-bd); }}
  .badge-released  {{ color: var(--purple); background: var(--purple-bg); border: 1px solid var(--purple-bd); }}
  .badge-unknown   {{ color: var(--muted);  background: var(--surface2);  border: 1px solid var(--border); }}
  .badge-confirmed::before, .badge-released::before {{
    content: ''; width: 5px; height: 5px; border-radius: 50%;
    animation: pulse-dot 2s ease-in-out infinite;
  }}
  .badge-confirmed::before {{ background: var(--green); }}
  .badge-released::before  {{ background: var(--purple); }}
  .fc-name {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: 1.65rem; letter-spacing: -0.04em; margin-bottom: 0.35rem;
  }}
  .fc-meta {{
    font-size: 0.8rem; color: var(--muted);
    display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap;
  }}
  .platform-tag {{
    font-size: 0.67rem; font-weight: 500;
    background: var(--surface2); border: 1px solid var(--border);
    padding: 0.17rem 0.48rem; border-radius: 4px; color: var(--muted);
  }}
  .mini-cd {{ display: flex; align-items: flex-end; gap: 0.38rem; }}
  .mini-block {{
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.6rem 0.8rem;
    text-align: center; min-width: 56px;
  }}
  .mini-num {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 1.45rem; letter-spacing: -0.03em;
    color: var(--accent); line-height: 1; display: block;
  }}
  .mini-lbl {{
    font-size: 0.48rem; font-weight: 600; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--muted);
    margin-top: 0.22rem; display: block;
  }}
  .mini-sep {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 1.1rem; color: var(--border2); padding-bottom: 0.95rem;
    animation: blink 1s step-end infinite;
  }}
  @keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0.1}} }}
  .released-box {{
    background: var(--purple-bg); border: 1px solid var(--purple-bd);
    border-radius: 10px; padding: 0.8rem 1.2rem; text-align: center;
  }}
  .released-box .rb-label {{
    font-size: 0.65rem; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--purple); margin-bottom: 0.2rem;
  }}
  .released-box .rb-date {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 0.9rem; color: var(--text);
  }}
  .games-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 0.8rem;
  }}
  .game-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1.15rem;
    display: flex; flex-direction: column; gap: 0.65rem;
    text-decoration: none; color: inherit;
    transition: border-color 0.15s, transform 0.15s;
    animation: fade-up 0.3s ease both; position: relative;
  }}
  .game-card:hover {{ border-color: var(--border2); transform: translateY(-2px); }}
  @keyframes fade-up {{ from{{opacity:0;transform:translateY(8px)}} to{{opacity:1;transform:none}} }}
  .gc-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 0.7rem; }}
  .gc-name {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 0.9rem; letter-spacing: -0.02em; line-height: 1.3;
  }}
  .status-pill {{
    font-size: 0.6rem; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 0.18rem 0.52rem;
    border-radius: 100px; white-space: nowrap; flex-shrink: 0;
  }}
  .pill-confirmed {{ background: var(--green-bg);  color: var(--green);  border: 1px solid var(--green-bd); }}
  .pill-window    {{ background: var(--blue-bg);   color: var(--blue);   border: 1px solid var(--blue-bd); }}
  .pill-rumor     {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow-bd); }}
  .pill-released  {{ background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-bd); }}
  .pill-unknown   {{ background: var(--surface2);  color: var(--muted);  border: 1px solid var(--border); }}
  .gc-date {{ font-size: 0.77rem; color: var(--muted); display: flex; align-items: center; gap: 0.32rem; }}
  .gc-platforms {{ display: flex; gap: 0.28rem; flex-wrap: wrap; }}
  .gc-countdown {{
    font-family: 'Sora', sans-serif; font-size: 0.8rem;
    font-weight: 700; color: var(--accent); letter-spacing: -0.01em;
  }}
  .gc-countdown.released {{ color: var(--purple); font-weight: 600; font-size: 0.77rem; }}
  .gc-countdown.none {{ font-family: 'Space Grotesk', sans-serif; font-weight: 400; color: var(--muted2); font-size: 0.76rem; }}
  .gc-footer {{
    display: flex; align-items: center; justify-content: space-between;
    padding-top: 0.5rem; border-top: 1px solid var(--border); margin-top: auto;
  }}
  .hype-btn {{
    display: inline-flex; align-items: center; gap: 0.35rem;
    font-family: 'Space Grotesk', sans-serif; font-size: 0.78rem; font-weight: 600;
    color: var(--muted); background: transparent;
    border: 1px solid var(--border); border-radius: 100px;
    padding: 0.28rem 0.75rem; cursor: pointer;
    transition: all 0.15s; user-select: none;
  }}
  .hype-btn:hover {{ border-color: var(--hype-bd); color: var(--hype); background: var(--hype-bg); }}
  .hype-btn.voted {{ border-color: var(--hype-bd); color: var(--hype); background: var(--hype-bg); }}
  .hype-btn .fire {{ font-size: 0.9rem; transition: transform 0.2s; }}
  .hype-btn:hover .fire, .hype-btn.voted .fire {{ transform: scale(1.25); }}
  .hype-btn.bump {{ animation: hype-bump 0.3s ease; }}
  @keyframes hype-bump {{ 0%{{transform:scale(1)}} 50%{{transform:scale(1.12)}} 100%{{transform:scale(1)}} }}
  .hype-list {{ display: flex; flex-direction: column; gap: 0.55rem; }}
  .hype-item {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 0.9rem 1.3rem;
    display: grid; grid-template-columns: 2rem 1fr auto auto;
    align-items: center; gap: 1rem; transition: border-color 0.15s;
  }}
  .hype-item:hover {{ border-color: var(--border2); }}
  .hype-item.top1 {{ border-color: rgba(255,59,59,0.3); background: rgba(255,59,59,0.04); }}
  .hype-item.top2 {{ border-color: rgba(255,150,50,0.2); }}
  .hype-item.top3 {{ border-color: rgba(255,200,50,0.15); }}
  .hype-rank {{
    font-family: 'Sora', sans-serif; font-weight: 800;
    font-size: 1rem; color: var(--muted2); text-align: center;
  }}
  .hype-rank.r1 {{ color: var(--accent); }}
  .hype-rank.r2 {{ color: #ff9632; }}
  .hype-rank.r3 {{ color: #fbbf24; }}
  .hype-name {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 0.9rem; letter-spacing: -0.02em;
  }}
  .hype-platforms {{ font-size: 0.72rem; color: var(--muted); margin-top: 0.1rem; }}
  .hype-bar-wrap {{ width: 120px; }}
  .hype-bar-track {{ height: 4px; background: rgba(255,255,255,0.06); border-radius: 2px; overflow: hidden; }}
  .hype-bar-fill {{ height: 100%; background: var(--accent); border-radius: 2px; transition: width 0.4s ease; }}
  .hype-count {{ font-size: 0.7rem; color: var(--muted); margin-top: 0.25rem; font-weight: 500; }}
  .hype-vote-btn {{
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 600;
    background: transparent; border: 1px solid var(--border2);
    color: var(--muted); border-radius: 100px; padding: 0.3rem 0.7rem;
    cursor: pointer; transition: all 0.15s; white-space: nowrap;
  }}
  .hype-vote-btn:hover, .hype-vote-btn.voted {{
    border-color: var(--accent-bd); color: var(--accent); background: var(--accent-bg);
  }}
  .upcoming-list {{ display: flex; flex-direction: column; gap: 0.55rem; }}
  .upcoming-item {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 0.95rem 1.25rem;
    display: grid; grid-template-columns: 1fr auto auto;
    align-items: center; gap: 1rem;
    text-decoration: none; color: inherit; transition: border-color 0.15s;
  }}
  .upcoming-item:hover {{ border-color: var(--border2); }}
  .up-name {{ font-family: 'Sora', sans-serif; font-weight: 600; font-size: 0.88rem; letter-spacing: -0.02em; }}
  .up-platforms {{ font-size: 0.73rem; color: var(--muted); margin-top: 0.08rem; }}
  .up-date {{ font-size: 0.78rem; color: var(--muted); text-align: right; white-space: nowrap; }}
  .up-days {{
    font-family: 'Sora', sans-serif; font-weight: 700;
    font-size: 0.86rem; color: var(--accent); white-space: nowrap;
    min-width: 76px; text-align: right;
  }}
  .launched-section {{ padding-top: 3.5rem; }}
  .launched-list {{ display: flex; flex-direction: column; gap: 0.5rem; }}
  .launched-item {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 0.9rem 1.3rem;
    display: grid; grid-template-columns: 1fr auto auto;
    align-items: center; gap: 1rem; opacity: 0.6;
    text-decoration: none; color: inherit;
    transition: opacity 0.15s, border-color 0.15s;
  }}
  .launched-item:hover {{ opacity: 0.85; border-color: var(--border2); }}
  .li-name {{ font-family: 'Sora', sans-serif; font-weight: 700; font-size: 0.88rem; letter-spacing: -0.02em; }}
  .li-plat {{ font-size: 0.72rem; color: var(--muted); margin-top: 0.1rem; }}
  .li-date {{ font-size: 0.78rem; color: var(--muted); white-space: nowrap; }}
  .li-badge {{
    font-size: 0.62rem; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 0.2rem 0.55rem; border-radius: 100px;
    background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.3);
    border: 1px solid rgba(255,255,255,0.08); white-space: nowrap;
  }}
  .api-section {{
    margin-top: 3.5rem; background: var(--surface);
    border: 1px solid var(--border2); border-radius: var(--radius-lg);
    padding: 2.4rem; display: grid; grid-template-columns: 1fr 1fr;
    gap: 3rem; align-items: start; position: relative; overflow: hidden;
  }}
  .api-section::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--blue), #818cf8);
  }}
  .api-eyebrow {{ font-size: 0.68rem; font-weight: 600; letter-spacing: 0.15em; text-transform: uppercase; color: var(--blue); margin-bottom: 0.55rem; }}
  .api-title {{ font-family: 'Sora', sans-serif; font-weight: 800; font-size: 1.55rem; letter-spacing: -0.04em; line-height: 1.15; margin-bottom: 0.7rem; }}
  .api-desc {{ font-size: 0.85rem; color: var(--muted); line-height: 1.7; margin-bottom: 1.2rem; }}
  .api-features {{ list-style: none; display: flex; flex-direction: column; gap: 0.42rem; margin-bottom: 1.4rem; }}
  .api-features li {{ font-size: 0.82rem; color: var(--muted); display: flex; align-items: center; gap: 0.45rem; }}
  .api-features li::before {{ content: '✓'; color: var(--green); font-weight: 700; font-size: 0.75rem; flex-shrink: 0; }}
  .code-block {{
    background: #0c0c0e; border: 1px solid var(--border);
    border-radius: 10px; padding: 1.35rem;
    font-family: 'Fira Code', 'Cascadia Code', 'Courier New', monospace;
    font-size: 0.77rem; line-height: 1.9; overflow-x: auto;
  }}
  .c-comment {{ color: rgba(255,255,255,0.2); }}
  .c-key {{ color: #7dd3fc; }}
  .c-str {{ color: #86efac; }}
  .c-num {{ color: #fbbf24; }}
  .btn {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-family: 'Space Grotesk', sans-serif; font-size: 0.84rem; font-weight: 600;
    padding: 0.68rem 1.25rem; border-radius: var(--radius);
    border: none; cursor: pointer; text-decoration: none; transition: all 0.15s;
  }}
  .btn-ghost {{ background: transparent; color: var(--muted); border: 1px solid var(--border2); }}
  .btn-ghost:hover {{ color: var(--text); background: var(--surface2); }}
  footer {{ border-top: 1px solid var(--border); margin-top: 5rem; padding: 2.5rem 2rem; text-align: center; }}
  .footer-logo {{ font-family: 'Sora', sans-serif; font-weight: 800; font-size: 1rem; letter-spacing: -0.03em; margin-bottom: 0.45rem; }}
  .footer-logo span {{ color: var(--accent); }}
  .footer-text {{ font-size: 0.76rem; color: var(--muted2); max-width: 460px; margin: 0 auto; line-height: 1.6; }}
  .footer-links {{ display: flex; justify-content: center; gap: 1.5rem; margin-top: 1rem; list-style: none; }}
  .footer-links a {{ font-size: 0.76rem; color: var(--muted); text-decoration: none; transition: color 0.15s; }}
  .footer-links a:hover {{ color: var(--text); }}
  .empty-state {{ grid-column: 1/-1; text-align: center; padding: 2.5rem; color: var(--muted); font-size: 0.88rem; }}
  @media (max-width: 768px) {{
    .featured-card {{ grid-template-columns: 1fr; }}
    .mini-cd {{ flex-wrap: wrap; }}
    .api-section {{ grid-template-columns: 1fr; gap: 1.5rem; }}
    .upcoming-item {{ grid-template-columns: 1fr auto; }}
    .up-date {{ display: none; }}
    .nav-links {{ display: none; }}
    .hero {{ padding: 3rem 0 2rem; }}
    .hero-div {{ display: none; }}
    .hype-item {{ grid-template-columns: 2rem 1fr auto; }}
    .hype-bar-wrap {{ display: none; }}
  }}
  @media (max-width: 480px) {{
    .games-grid {{ grid-template-columns: 1fr; }}
    .wrap {{ padding: 0 1.1rem; }}
  }}
</style>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-YJ2GY5FM9B"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-YJ2GY5FM9B');
</script>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">falta<span>pouco</span><small>.com.br</small></a>
  <div class="nav-right">
    <ul class="nav-links">
      <li><a href="#jogos">Jogos</a></li>
      <li><a href="#hype">Hype</a></li>
      <li><a href="#proximos">Próximos</a></li>
      <li><a href="#api">API</a></li>
    </ul>
    <a href="#api" class="nav-pill">API Pública →</a>
  </div>
</nav>

<div class="wrap">

  <section class="hero">
    <div class="hero-badge">Base de dados gamer brasileira</div>
    <h1>Quanto falta pro<br>próximo <em>grande jogo?</em></h1>
    <p class="hero-sub">Countdowns em tempo real, datas confirmadas e API pública gratuita para todo o mercado gamer.</p>
    <div class="hero-stats">
      <div class="hero-stat">
        <div class="hero-stat-num n-red" id="stat-total">{total_count}</div>
        <div class="hero-stat-label">Jogos monitorados</div>
      </div>
      <div class="hero-div"></div>
      <div class="hero-stat">
        <div class="hero-stat-num n-green" id="stat-confirmed">{confirmed_count}</div>
        <div class="hero-stat-label">Confirmados</div>
      </div>
      <div class="hero-div"></div>
      <div class="hero-stat">
        <div class="hero-stat-num" id="stat-released" style="color:var(--purple)">{released_count}</div>
        <div class="hero-stat-label">Já lançados</div>
      </div>
      <div class="hero-div"></div>
      <div class="hero-stat">
        <div class="hero-stat-num n-orange" id="stat-hype">—</div>
        <div class="hero-stat-label">Votos de hype</div>
      </div>
    </div>
  </section>

  <section class="search-section" id="busca">
    <div class="search-box">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
      <input class="search-input" id="searchInput" type="text" placeholder="Buscar jogo, plataforma...">
    </div>
    <div class="filter-row">
      <button class="filter-btn active" onclick="setFilter(this,'')">Todos</button>
      <button class="filter-btn" onclick="setFilter(this,'confirmed')">✅ Confirmados</button>
      <button class="filter-btn" onclick="setFilter(this,'released')">🟣 Lançados</button>
      <button class="filter-btn" onclick="setFilter(this,'window')">📅 Janela</button>
      <button class="filter-btn" onclick="setFilter(this,'unknown')">❓ Sem data</button>
      <button class="filter-btn" onclick="setFilter(this,'PS5')">PS5</button>
      <button class="filter-btn" onclick="setFilter(this,'Xbox')">Xbox</button>
      <button class="filter-btn" onclick="setFilter(this,'PC')">PC</button>
    </div>
  </section>

  <section id="jogos">
    <div class="section-label">
      <div class="section-title">🔥 Jogos mais aguardados</div>
      <span class="section-count" id="grid-count">—</span>
    </div>

    <a class="featured-card" href="/jogos/{html.escape(featured.slug if featured else "")}/" style="text-decoration:none;color:inherit">
      <div>
        <div class="badge badge-confirmed">Data confirmada</div>
        <div class="fc-name">{html.escape(featured.name if featured else "")}</div>
        <div class="fc-meta">
          <span>📅 {feat_date_display}</span>
          <span>{feat_platforms_html}</span>
        </div>
      </div>
      <div class="mini-cd">
        <div class="mini-block"><span class="mini-num" id="f-d">—</span><span class="mini-lbl">Dias</span></div>
        <div class="mini-sep">:</div>
        <div class="mini-block"><span class="mini-num" id="f-h">—</span><span class="mini-lbl">Horas</span></div>
        <div class="mini-sep">:</div>
        <div class="mini-block"><span class="mini-num" id="f-m">—</span><span class="mini-lbl">Min</span></div>
        <div class="mini-sep">:</div>
        <div class="mini-block"><span class="mini-num" id="f-s">—</span><span class="mini-lbl">Seg</span></div>
      </div>
    </a>

    <div class="games-grid" id="gamesGrid"></div>
  </section>

  <section id="hype">
    <div class="section-label">
      <div class="section-title">🔥 Ranking de Hype</div>
      <span class="section-count" id="hype-count">—</span>
    </div>
    <div class="hype-list" id="hypeList"></div>
  </section>

  <section id="proximos">
    <div class="section-label">
      <div class="section-title">📅 Próximos lançamentos</div>
    </div>
    <div class="upcoming-list" id="upcomingList"></div>
  </section>

  <div class="launched-section" id="lancados">
    <div class="section-label">
      <div class="section-title">✅ Já lançados</div>
      <span class="section-count" id="launched-count">—</span>
    </div>
    <div class="launched-list" id="launchedList"></div>
  </div>

  <section id="api">
    <div class="api-section">
      <div>
        <div class="api-eyebrow">Para desenvolvedores</div>
        <div class="api-title">API Pública<br>&amp; Gratuita</div>
        <p class="api-desc">Acesse nossa base de dados diretamente. JSON estático com rate limit via Cloudflare. Sem cadastro, sem chave de API.</p>
        <ul class="api-features">
          <li>Sem autenticação necessária</li>
          <li>Rate limit via Cloudflare</li>
          <li>Atualizado semanalmente</li>
          <li>Schema padronizado e documentado</li>
          <li>CORS habilitado</li>
          <li>Endpoint por jogo individual</li>
        </ul>
        <div style="display:flex;gap:0.6rem;flex-wrap:wrap">
          <a href="/api/v1/games.json" class="btn btn-ghost">Ver games.json →</a>
        </div>
      </div>
      <div class="code-block">
<span class="c-comment">// GET {BASE_URL}/api/v1/games.json</span>

{{
  <span class="c-key">"games"</span>: [
    {{
      <span class="c-key">"id"</span>:          <span class="c-str">"{featured.slug if featured else ''}"</span>,
      <span class="c-key">"name"</span>:        <span class="c-str">"{html.escape(featured.name) if featured else ''}"</span>,
      <span class="c-key">"releaseDate"</span>: <span class="c-str">"{featured.release if featured else ''}"</span>,
      <span class="c-key">"status"</span>:      <span class="c-str">"confirmed"</span>,
      <span class="c-key">"platforms"</span>:   [<span class="c-str">"PS5"</span>, <span class="c-str">"Xbox"</span>, <span class="c-str">"PC"</span>],
      <span class="c-key">"daysLeft"</span>:    <span class="c-num" id="apiDays">{gta_days_approx}</span>,
      <span class="c-key">"hype"</span>:        <span class="c-num" id="apiHype">...</span>
    }}
  ],
  <span class="c-key">"meta"</span>: {{
    <span class="c-key">"total"</span>:     <span class="c-num" id="apiTotal">{total_count}</span>,
    <span class="c-key">"version"</span>:   <span class="c-str">"1.0"</span>,
    <span class="c-key">"updatedAt"</span>: <span class="c-str">"{date.today().isoformat()}"</span>
  }}
}}
      </div>
    </div>
  </section>

</div>

<footer>
  <div class="footer-logo">falta<span>pouco</span>.com.br</div>
  <p class="footer-text">Base de dados independente de lançamentos de jogos. Não afiliado a nenhuma publisher ou desenvolvedora. Dados atualizados semanalmente.</p>
  <ul class="footer-links">
    <li><a href="#api">API</a></li>
    <li><a href="#jogos">Jogos</a></li>
    <li><a href="#hype">Hype</a></li>
    <li><a href="#">Sobre</a></li>
  </ul>
</footer>

<script>
  const games = {js_games};

  const statusLabel = {{ confirmed:'Confirmado', window:'Janela', rumor:'Rumor', released:'Lançado', unknown:'Sem data' }};
  const pillClass   = {{ confirmed:'pill-confirmed', window:'pill-window', rumor:'pill-rumor', released:'pill-released', unknown:'pill-unknown' }};
  const pad = (n, l=2) => String(n).padStart(l,'0');

  function getHypeData() {{
    try {{ return JSON.parse(localStorage.getItem('fp_hype') || '{{}}'); }} catch(e) {{ return {{}}; }}
  }}
  function saveHype(data) {{
    try {{ localStorage.setItem('fp_hype', JSON.stringify(data)); }} catch(e) {{}}
  }}
  function getVoted() {{
    try {{ return JSON.parse(localStorage.getItem('fp_voted') || '[]'); }} catch(e) {{ return []; }}
  }}
  function saveVoted(arr) {{
    try {{ localStorage.setItem('fp_voted', JSON.stringify(arr)); }} catch(e) {{}}
  }}

  function voteHype(gameId) {{
    const voted = getVoted();
    const hype  = getHypeData();
    const isVoted = voted.includes(gameId);
    if (isVoted) {{
      voted.splice(voted.indexOf(gameId), 1);
      hype[gameId] = Math.max(0, (hype[gameId] || 0) - 1);
    }} else {{
      voted.push(gameId);
      hype[gameId] = (hype[gameId] || 0) + 1;
    }}
    saveVoted(voted);
    saveHype(hype);
    return {{ count: hype[gameId], voted: !isVoted }};
  }}

  function getTotalHype() {{
    const hype = getHypeData();
    return Object.values(hype).reduce((a,b) => a+b, 0);
  }}

  function daysLeft(dateStr) {{
    if (!dateStr) return null;
    const diff = new Date(dateStr + 'T00:00:00') - new Date();
    return Math.floor(diff / 86400000);
  }}

  function isReleased(g) {{
    return g.launched || g.status === 'released' || (g.date && daysLeft(g.date) !== null && daysLeft(g.date) < 0);
  }}

  // Seed hype for first-time visitors
  const hypeSeeds = {hype_seeds_js};
  const votes = getHypeData();
  Object.keys(hypeSeeds).forEach(id => {{ if (votes[id] === undefined) votes[id] = hypeSeeds[id]; }});
  saveHype(votes);

  // Stats
  document.getElementById('stat-hype').textContent = getTotalHype().toLocaleString('pt-BR');
  document.getElementById('apiHype').textContent = votes[{json.dumps(featured.slug if featured else '')}] || 0;

  // Featured countdown
  const featDate = {feat_js_date};
  function tick() {{
    if (!featDate) return;
    const diff = featDate - new Date();
    if (diff <= 0) {{ ['f-d','f-h','f-m','f-s'].forEach(id => document.getElementById(id).textContent = '00'); return; }}
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    document.getElementById('f-d').textContent = pad(d,3);
    document.getElementById('f-h').textContent = pad(h);
    document.getElementById('f-m').textContent = pad(m);
    document.getElementById('f-s').textContent = pad(s);
    document.getElementById('apiDays').textContent = d;
  }}
  tick(); setInterval(tick, 1000);

  // Filter state
  let activeFilter = '';
  let searchQuery  = '';

  function setFilter(btn, val) {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = val;
    renderGrid();
  }}

  document.getElementById('searchInput').addEventListener('input', function() {{
    searchQuery = this.value.toLowerCase();
    renderGrid();
  }});

  function renderGrid() {{
    const grid = document.getElementById('gamesGrid');
    const nonFeatured = games.filter(g => !g.featured);
    const filtered = nonFeatured.filter(g => {{
      const matchSearch = !searchQuery ||
        g.name.toLowerCase().includes(searchQuery) ||
        g.platforms.some(p => p.toLowerCase().includes(searchQuery));
      const matchFilter = !activeFilter || (
        ['confirmed','released','window','rumor','unknown'].includes(activeFilter)
          ? g.status === activeFilter || (activeFilter === 'released' && isReleased(g))
          : g.platforms.some(p => p.toLowerCase().includes(activeFilter.toLowerCase()))
      );
      return matchSearch && matchFilter;
    }});

    document.getElementById('grid-count').textContent = filtered.length + ' jogos';
    grid.innerHTML = '';

    if (filtered.length === 0) {{
      grid.innerHTML = '<div class="empty-state">Nenhum jogo encontrado com esses filtros.</div>';
      return;
    }}

    const hype = getHypeData();
    const voted = getVoted();

    filtered.forEach((g, i) => {{
      const released = isReleased(g);
      const dl = g.date ? daysLeft(g.date) : null;
      const statusKey = released ? 'released' : g.status;
      const pill = pillClass[statusKey] || 'pill-unknown';
      const label = statusLabel[statusKey] || g.status;

      let dateStr = '—';
      if (g.date) {{
        dateStr = new Date(g.date + 'T00:00:00').toLocaleDateString('pt-BR', {{day:'2-digit', month:'long', year:'numeric'}});
      }}

      let countdownHtml = '';
      if (released) {{
        countdownHtml = `<div class="gc-countdown released">✅ Disponível agora</div>`;
      }} else if (dl !== null && dl >= 0) {{
        countdownHtml = `<div class="gc-countdown">⏳ ${{dl}} dia${{dl !== 1 ? 's' : ''}}</div>`;
      }} else {{
        countdownHtml = `<div class="gc-countdown none">Sem data confirmada</div>`;
      }}

      const platformsHtml = g.platforms.slice(0,4).map(p =>
        `<span class="platform-tag">${{p}}</span>`).join('');

      const hypeCount = hype[g.id] || 0;
      const isVoted = voted.includes(g.id);

      const card = document.createElement('div');
      card.className = 'game-card';
      card.style.animationDelay = (i * 0.03) + 's';
      card.innerHTML = `
        <a href="/jogos/${{g.id}}/" class="gc-link" style="display:contents">
          <div class="gc-top">
            <div class="gc-name">${{g.name}}</div>
            <span class="status-pill ${{pill}}">${{label}}</span>
          </div>
          <div class="gc-date">📅 ${{dateStr}}</div>
          <div class="gc-platforms">${{platformsHtml}}</div>
          ${{countdownHtml}}
        </a>
        <div class="gc-footer">
          <span style="font-size:0.72rem;color:var(--muted2)">${{g.platforms.length}} plataforma${{g.platforms.length !== 1 ? 's' : ''}}</span>
          <button class="hype-btn ${{isVoted ? 'voted' : ''}}" data-id="${{g.id}}" onclick="handleHype(this,'${{g.id}}')">
            <span class="fire">🔥</span> <span class="hype-count-inline">${{hypeCount}}</span>
          </button>
        </div>
      `;
      grid.appendChild(card);
    }});
  }}

  function handleHype(btn, id) {{
    const result = voteHype(id);
    btn.querySelector('.hype-count-inline').textContent = result.count;
    btn.classList.toggle('voted', result.voted);
    btn.classList.add('bump');
    btn.addEventListener('animationend', () => btn.classList.remove('bump'), {{ once: true }});
    document.getElementById('stat-hype').textContent = getTotalHype().toLocaleString('pt-BR');
    renderHype();
  }}

  function renderHype() {{
    const hype = getHypeData();
    const voted = getVoted();
    const rankable = games.filter(g => !g.featured);
    const sorted = [...rankable].sort((a,b) => (hype[b.id]||0) - (hype[a.id]||0)).slice(0,8);
    const maxVotes = hype[sorted[0]?.id] || 1;
    const totalVotes = Object.values(hype).reduce((a,b) => a+b, 0);

    document.getElementById('hype-count').textContent = totalVotes.toLocaleString('pt-BR') + ' votos';

    const list = document.getElementById('hypeList');
    list.innerHTML = '';
    sorted.forEach((g, i) => {{
      const v = hype[g.id] || 0;
      const pct = Math.round((v / maxVotes) * 100);
      const rankClass = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
      const rClass = i === 0 ? 'r1' : i === 1 ? 'r2' : i === 2 ? 'r3' : '';
      const isVoted = voted.includes(g.id);
      const item = document.createElement('div');
      item.className = `hype-item ${{rankClass}}`;
      item.innerHTML = `
        <div class="hype-rank ${{rClass}}">${{i === 0 ? '🔥' : '#' + (i+1)}}</div>
        <div>
          <div class="hype-name">${{g.name}}</div>
          <div class="hype-platforms">${{g.platforms.join(' · ')}}</div>
        </div>
        <div class="hype-bar-wrap">
          <div class="hype-bar-track"><div class="hype-bar-fill" style="width:${{pct}}%"></div></div>
          <div class="hype-count">${{v.toLocaleString('pt-BR')}} votos</div>
        </div>
        <button class="hype-vote-btn ${{isVoted ? 'voted' : ''}}" onclick="handleHype(this,'${{g.id}}');this.classList.toggle('voted')">
          ${{isVoted ? '🔥 Votado' : '🔥 Hype!'}}
        </button>
      `;
      list.appendChild(item);
    }});
  }}

  // Upcoming list
  const upcoming = games
    .filter(g => g.date && !isReleased(g) && daysLeft(g.date) > 0)
    .sort((a,b) => new Date(a.date) - new Date(b.date))
    .slice(0, 8);

  const upList = document.getElementById('upcomingList');
  upcoming.forEach(g => {{
    const dl = daysLeft(g.date);
    const dateStr = new Date(g.date + 'T00:00:00').toLocaleDateString('pt-BR', {{day:'2-digit', month:'long', year:'numeric'}});
    const item = document.createElement('a');
    item.className = 'upcoming-item';
    item.href = '/jogos/' + g.id + '/';
    item.innerHTML = `
      <div>
        <div class="up-name">${{g.name}}</div>
        <div class="up-platforms">${{g.platforms.join(' · ')}}</div>
      </div>
      <div class="up-date">${{dateStr}}</div>
      <div class="up-days">${{dl}} dias</div>
    `;
    upList.appendChild(item);
  }});

  // Launched list
  const launchedGames = games.filter(g => isReleased(g));
  document.getElementById('launched-count').textContent = launchedGames.length + ' jogos';
  const launchedList = document.getElementById('launchedList');
  launchedGames.forEach(g => {{
    const dateStr = g.date
      ? new Date(g.date + 'T00:00:00').toLocaleDateString('pt-BR', {{day:'2-digit', month:'long', year:'numeric'}})
      : '—';
    const item = document.createElement('a');
    item.className = 'launched-item';
    item.href = '/jogos/' + g.id + '/';
    item.innerHTML = `
      <div>
        <div class="li-name">${{g.name}}</div>
        <div class="li-plat">${{g.platforms.join(' · ')}}</div>
      </div>
      <div class="li-date">📅 ${{dateStr}}</div>
      <div class="li-badge">Lançado</div>
    `;
    launchedList.appendChild(item);
  }});

  renderGrid();
  renderHype();
</script>
</body>
</html>"""


def render_sitemap(urls: List[str]) -> str:
    items = []
    today = date.today().isoformat()
    for url in urls:
        items.append(f"<url><loc>{html.escape(url)}</loc><lastmod>{today}</lastmod></url>")
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + "\n".join(items) + "\n</urlset>\n"


def render_robots() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"


def save_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_raw_game(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza campos alternativos para o formato esperado internamente."""
    raw = dict(raw)
    # Suporte a release_date → release (None vira string vazia)
    if "release_date" in raw and "release" not in raw:
        raw["release"] = raw.pop("release_date") or ""
    elif "release_date" in raw:
        raw.pop("release_date")
    # Garante que release nunca é None
    if raw.get("release") is None:
        raw["release"] = ""
    return raw


def build_site(input_path: Path) -> None:
    data = safe_json_load(input_path)
    # Aceita tanto lista pura quanto {"games": [...]}
    if isinstance(data, dict) and "games" in data:
        raw_games = data["games"]
    elif isinstance(data, list):
        raw_games = data
    else:
        raise ValueError("O arquivo de entrada precisa ser uma lista JSON ou um objeto com a chave 'games'.")
    raw_games = [normalize_raw_game(g) for g in raw_games]

    youtube = YouTubeClient(YOUTUBE_API_KEY) if YOUTUBE_API_KEY else None

    jogos_dir = OUTPUT_DIR / "jogos"
    api_games_dir = OUTPUT_DIR / "api" / "v1" / "games"
    ensure_dir(jogos_dir)
    ensure_dir(api_games_dir)

    built: List[GameRecord] = []
    for raw in raw_games:
        print(f"[INFO] Processando {raw.get('name')}")
        game = build_game_record(raw, youtube)

        game_dir = jogos_dir / game.slug
        ensure_dir(game_dir)

        # Auto-detecta bg.jpg / bg.png / bg.webp na pasta do jogo
        # Prioridade: campo do JSON > arquivo local
        if not game.background_image:
            for ext in ("bg.jpg", "bg.jpeg", "bg.png", "bg.webp"):
                if (game_dir / ext).exists():
                    game.background_image = ext  # path relativo à pasta do jogo
                    print(f"[INFO]   └─ background detectado: {ext}")
                    break

        built.append(game)

        try:
            page_html = html_page(game, all_games=built)
            (game_dir / "index.html").write_text(page_html, encoding="utf-8")
        except Exception as e:
            print(f"[ERRO] Falha ao gerar página para '{game.name}': {e}")
        save_json(api_games_dir / f"{game.slug}.json", asdict(game))

    built_sorted = sorted(built, key=lambda g: (parse_date_obj(g.release) or date.max, g.name.lower()))
    aggregated = {
        "meta": {
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(built_sorted),
            "base_url": BASE_URL,
        },
        "games": [asdict(g) for g in built_sorted],
    }
    save_json(OUTPUT_DIR / "api" / "v1" / "games.json", aggregated)
    save_json(OUTPUT_DIR / "api" / "v1" / "upcoming.json", {
        "games": [asdict(g) for g in built_sorted if g.status in ("confirmed", "window")]
    })

    (OUTPUT_DIR / "index.html").write_text(render_home(built_sorted), encoding="utf-8")

    urls = [f"{BASE_URL}/", f"{BASE_URL}/api/v1/games.json"]
    urls.extend(g.page_url for g in built_sorted)
    urls.extend(g.api_url for g in built_sorted)
    # ── Páginas de evento (events_input.json) ─────────────────────────────
    events_path = Path(os.getenv("FALTAPOCO_EVENTS", "events_input.json"))
    if events_path.exists():
        try:
            events_data = safe_json_load(events_path)
            events_list = events_data if isinstance(events_data, list) else events_data.get("events", [])
            for evt in events_list:
                evt_slug = evt.get("slug", "")
                if not evt_slug:
                    continue
                evt_dir = OUTPUT_DIR / evt_slug
                ensure_dir(evt_dir)
                evt_html = render_event_page(evt, built_sorted)
                (evt_dir / "index.html").write_text(evt_html, encoding="utf-8")
                urls.append(f"{BASE_URL}/{evt_slug}/")
                print(f"[INFO] Evento gerado: /{evt_slug}/")
        except Exception as e:
            print(f"[WARN] Erro ao gerar eventos: {e}")

    (OUTPUT_DIR / "sitemap.xml").write_text(render_sitemap(urls), encoding="utf-8")
    (OUTPUT_DIR / "robots.txt").write_text(render_robots(), encoding="utf-8")

    print(f"[OK] Build concluído em {OUTPUT_DIR.resolve()}")
    print("[OK] Arquivos gerados: index.html, /jogos/*, /api/v1/*, sitemap.xml, robots.txt")


EXAMPLE_INPUT = [
    {
        "name": "Grand Theft Auto VI",
        "release": "2026-11-19",
        "status": "confirmed",
        "confidence_date": "alta",
        "developer": "Rockstar Games",
        "publisher": "Rockstar Games",
        "platforms": ["PS5", "Xbox Series X|S", "PC"],
        "description": "Acompanhe a data de lançamento, trailer e atualizações de GTA VI.",
        "background_image": "",
        "video": "auto",
        "news": [],
        "reviews": [],
        "source": {"type": "manual_list"}
    },
    {
        "name": "Marvel's Wolverine",
        "release": "2026",
        "status": "window",
        "confidence_date": "média",
        "developer": "Insomniac Games",
        "publisher": "Sony Interactive Entertainment",
        "platforms": ["PS5"],
        "description": "Página automática de lançamento de Marvel's Wolverine.",
        "background_image": "",
        "video": "auto",
        "news": [],
        "reviews": [],
        "source": {"type": "manual_list"}
    }
]


def write_example_files() -> None:
    ensure_dir(OUTPUT_DIR)
    Path("games_input.example.json").write_text(json.dumps(EXAMPLE_INPUT, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--write-example":
        write_example_files()
        print("[OK] games_input.example.json criado.")
        raise SystemExit(0)

    input_path = Path(DEFAULT_INPUT)
    if not input_path.exists():
        Path(DEFAULT_INPUT).write_text(json.dumps(EXAMPLE_INPUT, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] '{DEFAULT_INPUT}' não existia. Criei um exemplo para você editar.")

    build_site(Path(DEFAULT_INPUT))
