"""
import_partner_preview.py
Converte a tabela do Xbox Partner Preview (colada em .txt) para JSON
compatível com games_input.json do faltapoco.com.br

Uso:
    python import_partner_preview.py partner_preview.txt
    python import_partner_preview.py partner_preview.txt --out meus_jogos.json
    python import_partner_preview.py partner_preview.txt --merge games_input.json

Formato de entrada esperado (TSV ou espaço-separado, copiado da tabela):
    Game                    Announcement Type   Release Date    Platforms
    Alien Deathstorm        World Premiere      2027            PS5, Xbox Series X/S, PC
    Ascend To Zero          New Info            Jul 13th 2026   Xbox Series X/S, PC
    ...

O script aceita variações:
  - Separadores tab ou múltiplos espaços
  - Datas em vários formatos: "Jul 13th 2026", "Summer 2026", "2027", "TBD"
  - Plataformas com vírgula ou parênteses: "PS5, Xbox (Steam, Epic)"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Slugify ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    replacements = {
        "á":"a","à":"a","â":"a","ã":"a","é":"e","ê":"e","í":"i",
        "ó":"o","ô":"o","õ":"o","ú":"u","ü":"u","ç":"c",
        "'":"","'":"",":":"","/":"-","&":"and",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9\s\-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-") or "jogo"


# ── Normalização de plataformas ───────────────────────────────────────────────

PLATFORM_MAP = {
    # PlayStation
    "ps5": "PS5",
    "playstation 5": "PS5",
    "ps4": "PS4",
    # Xbox
    "xbox series x/s": "Xbox Series X|S",
    "xbox series x|s": "Xbox Series X|S",
    "xbox series": "Xbox Series X|S",
    "xbox": "Xbox Series X|S",
    "xsx": "Xbox Series X|S",
    # PC
    "pc": "PC",
    "steam": "PC",
    "epic": "PC",
    "epic games": "PC",
    "gog": "PC",
    "windows": "PC",
    # Nintendo
    "switch 2": "Switch 2",
    "nintendo switch 2": "Switch 2",
    "switch": "Switch 2",   # padrão: assume Switch 2 em anúncios de 2026
    "nintendo switch": "Switch 2",
}

def normalize_platforms(raw: str) -> List[str]:
    """
    Converte string de plataformas para lista normalizada.
    Remove duplicatas e ordena conforme padrão do projeto.
    """
    # Remove parênteses com lojas: "PC (Steam, Epic, Xbox)" → "PC"
    raw = re.sub(r"\(([^)]*)\)", lambda m: " " + m.group(1), raw)
    # Separa por vírgula ou ponto-e-vírgula
    parts = re.split(r"[,;]", raw)
    result = set()
    for part in parts:
        part = part.strip().lower()
        if not part:
            continue
        # Tenta match exato
        if part in PLATFORM_MAP:
            result.add(PLATFORM_MAP[part])
            continue
        # Tenta match parcial
        for key, val in PLATFORM_MAP.items():
            if key in part:
                result.add(val)
                break
    # Ordena conforme padrão do projeto
    ORDER = ["PS5", "PS4", "Xbox Series X|S", "PC", "Switch 2"]
    return sorted(result, key=lambda p: ORDER.index(p) if p in ORDER else 99)


# ── Normalização de datas ─────────────────────────────────────────────────────

MONTH_MAP = {
    "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
    "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12",
    "january":"01","february":"02","march":"03","april":"04","june":"06",
    "july":"07","august":"08","september":"09","october":"10",
    "november":"11","december":"12",
}

def parse_date(raw: str) -> tuple[Optional[str], str, str]:
    """
    Retorna (release_date_iso, release_window_raw, status)
    - release_date_iso: "YYYY-MM-DD" ou None
    - release_window_raw: valor original
    - status: "confirmed" | "window" | "unknown"
    """
    raw = raw.strip()
    raw_lower = raw.lower()

    if not raw or raw_lower in ("tbd", "tba", "n/a", "-", ""):
        return None, raw, "unknown"

    # Ano puro: "2026", "2027"
    if re.fullmatch(r"\d{4}", raw):
        return None, raw, "window"

    # Estações: "Summer 2026", "Fall 2026", "Spring 2027", "Winter 2026"
    season_map = {"spring":"Q2","summer":"Q3","fall":"Q4","autumn":"Q4","winter":"Q1"}
    for season, quarter in season_map.items():
        m = re.search(rf"\b{season}\s+(\d{{4}})\b", raw_lower)
        if m:
            return None, raw, "window"

    # "Q1 2026", "Q2 2026"
    m = re.match(r"Q([1-4])\s+(\d{4})", raw, re.IGNORECASE)
    if m:
        return None, raw, "window"

    # "Mar 31st 2026", "Jul 13th 2026", "Aug 14th 2026"
    m = re.match(
        r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})",
        raw, re.IGNORECASE
    )
    if m:
        month_str = m.group(1).lower()[:3]
        day = m.group(2).zfill(2)
        year = m.group(3)
        month = MONTH_MAP.get(month_str)
        if month:
            return f"{year}-{month}-{day}", raw, "confirmed"

    # "31 Mar 2026" (dia primeiro)
    m = re.match(
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        raw, re.IGNORECASE
    )
    if m:
        day = m.group(1).zfill(2)
        month_str = m.group(2).lower()[:3]
        year = m.group(3)
        month = MONTH_MAP.get(month_str)
        if month:
            return f"{year}-{month}-{day}", raw, "confirmed"

    # "2026-11-19" (já ISO)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return raw, raw, "confirmed"

    # "June 2026" (mês + ano sem dia)
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", raw, re.IGNORECASE)
    if m:
        month_str = m.group(1).lower()[:3]
        year = m.group(2)
        month = MONTH_MAP.get(month_str)
        if month:
            return None, raw, "window"

    return None, raw, "unknown"


# ── Classificação de prioridade ───────────────────────────────────────────────

def classify_priority(
    name: str,
    announcement_type: str,
    platforms: List[str],
) -> str:
    """
    Classifica prioridade do jogo: "high" | "medium" | "low"

    Critérios HIGH:
    - Franquias fortes com alto volume de busca orgânica
    - Ports de títulos já populares no PC/console
    - Multiplatforma com PS5 (maior base instalada)
    - World Premiere de IP conhecido

    Critérios MEDIUM:
    - New Info de jogos já anunciados
    - Indies com boa tração (Hades, Meat Boy, etc.)
    - DLC de franquia importante
    - Confirmado mas plataforma limitada

    Critérios LOW:
    - IPs desconhecidos sem histórico
    - TBD sem data ou janela
    - Update/DLC menor
    - Poucos dados disponíveis
    """

    name_lower = name.lower()
    atype_lower = announcement_type.lower()

    # Franquias de alto apelo (volume de busca garantido)
    HIGH_FRANCHISES = [
        "hades", "stalker", "s.t.a.l.k.e.r", "serious sam",
        "super meat boy", "wuthering waves", "the expanse",
        "eternal life", "bluey",
    ]

    # Tipos de anúncio que indicam relevância
    HIGH_ANNOUNCEMENT_TYPES = ["world premiere", "port"]
    MEDIUM_ANNOUNCEMENT_TYPES = ["new info", "update/dlc", "update-dlc", "dlc"]

    # Regra 1: franquia conhecida → high
    for franchise in HIGH_FRANCHISES:
        if franchise in name_lower:
            return "high"

    # Regra 2: World Premiere multiplatforma → high
    if "world premiere" in atype_lower and len(platforms) >= 3:
        return "high"

    # Regra 3: Port de jogo → normalmente high (audiência nova)
    if "port" in atype_lower:
        return "high"

    # Regra 4: World Premiere plataforma limitada → medium
    if "world premiere" in atype_lower:
        return "medium"

    # Regra 5: New Info com PS5 incluído → medium
    if "new info" in atype_lower and "PS5" in platforms:
        return "medium"

    # Regra 6: DLC/Update → low (exceto franquias já listadas acima)
    if any(t in atype_lower for t in ["update", "dlc"]):
        return "low"

    # Regra 7: New Info sem PS5, só Xbox/PC → medium
    if "new info" in atype_lower:
        return "medium"

    # Default
    return "low"


# ── Geração de descrição automática ──────────────────────────────────────────

def auto_description(name: str, announcement_type: str, platforms: List[str], release_window: str) -> str:
    plat_str = ", ".join(platforms) if platforms else "múltiplas plataformas"
    atype_lower = announcement_type.lower()

    if "world premiere" in atype_lower:
        return f"{name} foi revelado mundialmente. Acompanhe aqui a data de lançamento, trailer oficial e todas as novidades para {plat_str}."
    if "port" in atype_lower:
        return f"{name} chega a novas plataformas. Confira a data de lançamento e trailer para {plat_str}."
    if "new info" in atype_lower:
        return f"Novas informações sobre {name} foram reveladas. Acompanhe o countdown, trailer e data de lançamento para {plat_str}."
    if "dlc" in atype_lower or "update" in atype_lower:
        return f"Novo conteúdo de {name} a caminho. Veja a data de lançamento e novidades para {plat_str}."
    return f"Acompanhe a data de lançamento, trailer e atualizações de {name} para {plat_str}."


# ── Parser da tabela ──────────────────────────────────────────────────────────

def parse_table(text: str) -> List[Dict[str, Any]]:
    """
    Parseia tabela TSV/espaços do Xbox Partner Preview.
    Detecta automaticamente se a primeira linha é cabeçalho.
    """
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return []

    results = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Tenta split por tab primeiro
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        else:
            # Fallback: split por 2+ espaços
            parts = [p.strip() for p in re.split(r"  +", line)]

        # Filtra partes vazias
        parts = [p for p in parts if p]

        if len(parts) < 2:
            continue

        # Pula linha de cabeçalho
        first = parts[0].lower()
        if first in ("game", "title", "nome", "jogo"):
            continue

        # Extrai campos
        name = parts[0] if len(parts) > 0 else ""
        announcement_type = parts[1] if len(parts) > 1 else "Unknown"
        release_raw = parts[2] if len(parts) > 2 else "TBD"
        platforms_raw = parts[3] if len(parts) > 3 else ""

        # Normaliza
        platforms = normalize_platforms(platforms_raw)
        release_date, release_window_raw, status = parse_date(release_raw)
        slug = slugify(name)
        priority = classify_priority(name, announcement_type, platforms)
        description = auto_description(name, announcement_type, platforms, release_window_raw)

        entry = {
            "name": name,
            "slug": slug,
            "release_date": release_date,
            "release_window_raw": release_window_raw,
            "status": status,
            "platforms": platforms,
            "announcement_type": announcement_type,
            "event": "xbox-partner-preview-2026",
            "priority": priority,
            "background_image": "bg.jpg",
            "description": description,
            "video_id": "",
            "developer": "",
            "publisher": "",
            "news": [],
            "reviews": [],
        }
        results.append(entry)
        print(f"  [OK] {name:<35} | {announcement_type:<18} | {release_window_raw:<18} | {priority:<6} | {', '.join(platforms)}")

    return results


# ── Merge com games_input.json existente ─────────────────────────────────────

def merge_with_existing(new_games: List[Dict], existing_path: Path) -> List[Dict]:
    """
    Faz merge dos jogos novos com o JSON existente.
    - Jogos com slug já existente são IGNORADOS (não sobrescreve dados manuais)
    - Jogos novos são ADICIONADOS no final
    """
    with existing_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    existing = data["games"] if isinstance(data, dict) else data
    existing_slugs = {g.get("slug", "") for g in existing}

    added = 0
    skipped = 0
    for g in new_games:
        if g["slug"] in existing_slugs:
            print(f"  [SKIP] '{g['name']}' já existe (slug: {g['slug']})")
            skipped += 1
        else:
            existing.append(g)
            added += 1

    print(f"\n  Merge: {added} adicionados, {skipped} ignorados (já existiam)")

    if isinstance(data, dict):
        data["games"] = existing
        return data
    return existing


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Importa tabela Xbox Partner Preview para games_input.json"
    )
    parser.add_argument("input", help="Arquivo .txt com a tabela copiada")
    parser.add_argument("--out", default="partner_preview_import.json",
                        help="Arquivo de saída (default: partner_preview_import.json)")
    parser.add_argument("--merge", metavar="GAMES_JSON",
                        help="Faz merge direto com games_input.json existente")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: arquivo não encontrado: {input_path}", file=sys.stderr)
        sys.exit(1)

    text = input_path.read_text(encoding="utf-8", errors="replace")

    print(f"\nImportando: {input_path}")
    print("-" * 70)
    games = parse_table(text)
    print("-" * 70)
    print(f"Total importado: {len(games)} jogos\n")

    if not games:
        print("Nenhum jogo encontrado. Verifique o formato do arquivo.")
        sys.exit(1)

    if args.merge:
        merge_path = Path(args.merge)
        if not merge_path.exists():
            print(f"Erro: {merge_path} não encontrado", file=sys.stderr)
            sys.exit(1)
        output_data = merge_with_existing(games, merge_path)
        out_path = merge_path  # sobrescreve o original
        print(f"\nMerge salvo em: {out_path}")
    else:
        output_data = {"games": games}
        out_path = Path(args.out)
        print(f"Salvo em: {out_path}")

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print("\nPróximos passos:")
    print("  1. Revise o arquivo gerado — especialmente slugs e datas")
    print("  2. Adicione video_id e developer/publisher manualmente")
    print("  3. Rode: python build_faltapoco.py")


if __name__ == "__main__":
    main()
