"""
monitor_lancamentos.py — Radar automático do FaltaPouco.

Monitora canais oficiais no YouTube (Xbox, PlayStation, etc.) e feeds RSS de
sites de games procurando notícias sobre datas de lançamento. Quando encontra
algo relevante sobre um jogo que já está no games_input.json, propõe a
atualização; jogos desconhecidos entram num relatório para revisão humana
(ou são extraídos por IA, se ANTHROPIC_API_KEY estiver configurada).

O script NUNCA decide sozinho o que vai pro ar: ele escreve as mudanças em
games_input.json e gera monitor_report.md — o workflow do GitHub Actions abre
um Pull Request com isso, e você aprova (ou não) pelo celular.

Variáveis de ambiente:
  YOUTUBE_API_KEY     — mesma chave já usada pelo build (obrigatória p/ YouTube)
  ANTHROPIC_API_KEY   — opcional; liga a extração via IA p/ jogos novos
  FALTAPOCO_INPUT     — caminho do games_input.json (padrão: games_input.json)
  MONITOR_CONFIG      — caminho do monitor_config.json (padrão: monitor_config.json)
  MONITOR_LOOKBACK_H  — janela de novidade em horas (padrão: 26; cubra o intervalo do cron)
"""
from __future__ import annotations

import json
import os
import re
import sys
import html as html_mod
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ── Config ───────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
INPUT_PATH = Path(os.getenv("FALTAPOCO_INPUT", "games_input.json"))
CONFIG_PATH = Path(os.getenv("MONITOR_CONFIG", "monitor_config.json"))
REPORT_PATH = Path("monitor_report.md")
LOOKBACK_HOURS = int(os.getenv("MONITOR_LOOKBACK_H", "26"))

YT_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YT_PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

# Palavras que indicam notícia de data/lançamento (pt + en)
RELEASE_KEYWORDS = [
    "release date", "release window", "launch", "launches", "arrives",
    "coming to", "coming in", "out now", "available now", "hits ps5",
    "hits xbox", "hits pc", "announce", "announced", "revealed", "reveal",
    "data de lançamento", "janela de lançamento", "lançamento", "chega em",
    "chega ao", "chega no", "chega dia", "sai em", "sai dia",
    "disponível agora", "já disponível", "anunciado", "confirmado",
    "adiado", "delayed", "postponed",
]

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

DEFAULT_CONFIG = {
    "youtube_channels": ["@Xbox", "@PlayStation", "@RockstarGames", "@NintendoAmerica"],
    "rss_feeds": [
        "https://www.eurogamer.net/feed",
        "https://www.gamespot.com/feeds/game-news/",
        "https://www.pcgamer.com/rss/",
    ],
    "max_videos_per_channel": 15,
    "max_items_per_feed": 30,
}


# ── Utilidades ───────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(msg, flush=True)


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **cfg}
        except Exception as e:
            log(f"[WARN] monitor_config.json inválido ({e}); usando padrão.")
    return dict(DEFAULT_CONFIG)


def normalize_name(name: str) -> str:
    """Normaliza nome de jogo para matching: minúsculas, sem pontuação/números romanos convertidos."""
    n = name.lower()
    n = n.replace("'", "").replace("’", "")
    n = re.sub(r"[™®:\-–—]", " ", n)
    roman = {" vi": " 6", " v": " 5", " iv": " 4", " iii": " 3", " ii": " 2"}
    for r, d in roman.items():
        n = re.sub(rf"{r}\b", d, n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def extract_date(text: str) -> Optional[str]:
    """Extrai uma data completa (dia+mês+ano) de um texto pt/en. Retorna ISO ou None.

    Só aceita datas COMPLETAS de propósito: "March 2027" não vira atualização
    automática — janela de lançamento é decisão editorial, não do robô.
    """
    t = text.lower()

    # 19 de novembro de 2026
    m = re.search(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", t)
    if m and m.group(2) in MESES_PT:
        return f"{m.group(3)}-{MESES_PT[m.group(2)]:02d}-{int(m.group(1)):02d}"

    # November 19, 2026  /  November 19 2026
    m = re.search(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    if m and m.group(1) in MONTHS_EN:
        return f"{m.group(3)}-{MONTHS_EN[m.group(1)]:02d}-{int(m.group(2)):02d}"

    # 19 November 2026
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\.?,?\s+(\d{4})", t)
    if m and m.group(2) in MONTHS_EN:
        return f"{m.group(3)}-{MONTHS_EN[m.group(2)]:02d}-{int(m.group(1)):02d}"

    # 2026-11-19
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return m.group(0)

    # 19/11/2026 (formato BR)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    return None


def looks_like_release_news(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in RELEASE_KEYWORDS)


def parse_when(value: str) -> Optional[datetime]:
    """Parseia datas de publicação de RSS/Atom/YouTube nos formatos comuns."""
    value = (value or "").strip()
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",   # RSS 2.0
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",        # Atom / ISO
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(value, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── Coleta: YouTube ──────────────────────────────────────────────────────────
def youtube_recent_videos(session: requests.Session, handle: str, max_videos: int) -> List[Dict[str, str]]:
    """Vídeos recentes de um canal via handle (@Xbox). 2 chamadas baratas (1 unidade cada)."""
    if not YOUTUBE_API_KEY:
        return []
    try:
        r = session.get(YT_CHANNELS_URL, params={
            "part": "contentDetails", "forHandle": handle, "key": YOUTUBE_API_KEY,
        }, timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            log(f"[WARN] Canal não encontrado: {handle}")
            return []
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        r = session.get(YT_PLAYLIST_URL, params={
            "part": "snippet", "playlistId": uploads,
            "maxResults": min(max_videos, 50), "key": YOUTUBE_API_KEY,
        }, timeout=30)
        r.raise_for_status()
        out = []
        for it in r.json().get("items", []):
            sn = it.get("snippet", {})
            vid = sn.get("resourceId", {}).get("videoId", "")
            out.append({
                "source": f"YouTube {handle}",
                "title": sn.get("title", ""),
                "description": sn.get("description", "")[:500],
                "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                "published": sn.get("publishedAt", ""),
                "video_id": vid,
            })
        return out
    except Exception as e:
        log(f"[WARN] Falha no YouTube ({handle}): {e}")
        return []


# ── Coleta: RSS/Atom ─────────────────────────────────────────────────────────
def fetch_rss(session: requests.Session, url: str, max_items: int) -> List[Dict[str, str]]:
    try:
        r = session.get(url, timeout=30, headers={"User-Agent": "FaltaPoucoBot/1.0 (+https://faltapoco.com.br)"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        log(f"[WARN] Falha no feed {url}: {e}")
        return []

    out: List[Dict[str, str]] = []
    # RSS 2.0
    for item in root.iter("item"):
        out.append({
            "source": url,
            "title": (item.findtext("title") or "").strip(),
            "description": html_mod.unescape((item.findtext("description") or ""))[:500],
            "url": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
            "video_id": "",
        })
    # Atom
    if not out:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns):
            link_el = entry.find("a:link", ns)
            out.append({
                "source": url,
                "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
                "description": html_mod.unescape(entry.findtext("a:summary", default="", namespaces=ns) or "")[:500],
                "url": link_el.get("href", "") if link_el is not None else "",
                "published": (entry.findtext("a:updated", default="", namespaces=ns) or "").strip(),
                "video_id": "",
            })
    return out[:max_items]


# ── Matching contra a base atual ─────────────────────────────────────────────
def match_items_to_games(items: List[Dict[str, str]], games: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Separa itens em (propostas sobre jogos conhecidos, itens sobre jogos desconhecidos)."""
    known_index = {normalize_name(g["name"]): g for g in games}
    proposals: List[Dict[str, Any]] = []
    unknown: List[Dict[str, str]] = []

    for it in items:
        text = f"{it['title']} {it['description']}"
        if not looks_like_release_news(text):
            continue
        norm_text = normalize_name(text)
        matched = None
        for norm_name, g in known_index.items():
            if len(norm_name) >= 5 and norm_name in norm_text:
                matched = g
                break
        if matched is None:
            unknown.append(it)
            continue

        new_date = extract_date(text)
        current = (matched.get("release_date") or matched.get("release") or "").strip()
        if new_date and new_date != current:
            proposals.append({
                "slug": matched["slug"], "name": matched["name"],
                "field": "release_date", "old": current or "(sem data)",
                "new": new_date, "evidence": it,
            })
        elif not new_date:
            # notícia relevante sem data completa → vai pro relatório como "de olho"
            proposals.append({
                "slug": matched["slug"], "name": matched["name"],
                "field": None, "old": "", "new": "", "evidence": it,
            })
    return proposals, unknown


# ── IA opcional (jogos desconhecidos) ────────────────────────────────────────
def llm_extract_new_games(unknown: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Usa a API da Anthropic (se configurada) para extrair jogos novos de forma estruturada."""
    if not ANTHROPIC_API_KEY or not unknown:
        return []
    batch = [{"title": u["title"], "description": u["description"], "url": u["url"]} for u in unknown[:25]]
    prompt = (
        "Você extrai dados de lançamentos de videogames a partir de manchetes. "
        "Para cada item abaixo, responda APENAS um JSON array. Inclua um objeto somente se o item "
        "for claramente sobre UM jogo específico com anúncio/lançamento/data. Formato de cada objeto: "
        '{"name": str, "release_date": "YYYY-MM-DD" ou "", "status": "confirmed"|"window"|"rumor"|"unknown", '
        '"platforms": [subset de "PS5","Xbox Series X|S","PC","Switch 2"], "confidence": "alta"|"media"|"baixa", '
        '"source_url": str}. Não invente datas nem plataformas: se não estiver no texto, deixe vazio. '
        "Itens:\n" + json.dumps(batch, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        parsed = json.loads(text)
        return [p for p in parsed if isinstance(p, dict) and p.get("name")]
    except Exception as e:
        log(f"[WARN] Extração via IA falhou (seguindo sem ela): {e}")
        return []


def make_slug(name: str) -> str:
    n = normalize_name(name)
    n = re.sub(r"[^a-z0-9\s-]", "", n)
    return re.sub(r"[\s_-]+", "-", n).strip("-") or "jogo"


# ── Aplicação + relatório ────────────────────────────────────────────────────
def apply_and_report(games_doc: Dict[str, Any], proposals: List[Dict[str, Any]],
                     new_games: List[Dict[str, Any]], unknown: List[Dict[str, str]]) -> bool:
    games = games_doc["games"]
    by_slug = {g["slug"]: g for g in games}
    changed = False
    lines = [
        "# 🤖 Radar FaltaPouco — atualização automática",
        "",
        f"_Gerado em {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Revise antes de dar merge — o robô só propõe, você decide._",
        "",
    ]

    date_updates = [p for p in proposals if p["field"] == "release_date"]
    watch_items = [p for p in proposals if p["field"] is None]

    if date_updates:
        lines.append("## 📅 Datas atualizadas em `games_input.json`")
        for p in date_updates:
            g = by_slug.get(p["slug"])
            if g is not None:
                g["release_date"] = p["new"]
                if g.get("status") in ("unknown", "rumor", "window"):
                    g["status"] = "confirmed"
                changed = True
            ev = p["evidence"]
            lines.append(f"- **{p['name']}**: `{p['old']}` → `{p['new']}` — [{ev['title'][:80]}]({ev['url']}) _({ev['source']})_")
        lines.append("")

    if new_games:
        lines.append("## 🆕 Jogos novos adicionados (extraídos por IA — confira!)")
        existing_norm = {normalize_name(g["name"]) for g in games}
        for ng in new_games:
            if normalize_name(ng["name"]) in existing_norm:
                continue
            if ng.get("confidence") == "baixa":
                lines.append(f"- ⚠️ _Ignorado (confiança baixa)_: {ng['name']} — {ng.get('source_url','')}")
                continue
            entry = {
                "name": ng["name"],
                "slug": make_slug(ng["name"]),
                "release_date": ng.get("release_date") or "",
                "status": ng.get("status") or "unknown",
                "platforms": ng.get("platforms") or [],
                "video_id": "",
                "background_image": "",
            }
            games.append(entry)
            changed = True
            lines.append(f"- **{ng['name']}** — status `{entry['status']}`, data `{entry['release_date'] or '—'}` — fonte: {ng.get('source_url','')}")
        lines.append("")

    if watch_items:
        lines.append("## 👀 No radar (notícia relevante, sem data completa — decisão sua)")
        for p in watch_items[:15]:
            ev = p["evidence"]
            lines.append(f"- **{p['name']}** — [{ev['title'][:90]}]({ev['url']}) _({ev['source']})_")
        lines.append("")

    if unknown and not new_games:
        lines.append("## ❓ Possíveis jogos fora da base (sem IA configurada — revisar manualmente)")
        for u in unknown[:15]:
            lines.append(f"- [{u['title'][:90]}]({u['url']}) _({u['source']})_")
        lines.append("")

    if not (date_updates or new_games or watch_items or unknown):
        lines.append("Nenhuma novidade relevante encontrada nesta rodada. 😴")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if changed:
        INPUT_PATH.write_text(json.dumps(games_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    cfg = load_config()
    games_doc = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    if isinstance(games_doc, list):
        games_doc = {"games": games_doc}

    session = requests.Session()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    items: List[Dict[str, str]] = []
    for handle in cfg["youtube_channels"]:
        items.extend(youtube_recent_videos(session, handle, cfg["max_videos_per_channel"]))
    for feed in cfg["rss_feeds"]:
        items.extend(fetch_rss(session, feed, cfg["max_items_per_feed"]))

    # Filtra pela janela de novidade (itens sem data de publicação passam, por segurança)
    fresh = []
    for it in items:
        when = parse_when(it.get("published", ""))
        if when is None or when >= cutoff:
            fresh.append(it)
    log(f"[INFO] {len(items)} itens coletados, {len(fresh)} dentro da janela de {LOOKBACK_HOURS}h")

    proposals, unknown = match_items_to_games(fresh, games_doc["games"])
    log(f"[INFO] {len(proposals)} itens sobre jogos conhecidos, {len(unknown)} sobre desconhecidos")

    new_games = llm_extract_new_games(unknown)
    if ANTHROPIC_API_KEY:
        log(f"[INFO] IA extraiu {len(new_games)} candidatos a jogo novo")

    changed = apply_and_report(games_doc, proposals, new_games, unknown)
    log(f"[OK] Relatório em {REPORT_PATH}. games_input.json {'ATUALIZADO' if changed else 'sem mudanças'}.")
    # Exit code 0 sempre; o workflow decide se abre PR olhando o git diff
    return 0


if __name__ == "__main__":
    sys.exit(main())
