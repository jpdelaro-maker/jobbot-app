# job_engine/engine.py
# Version "Cloud-friendly" — pas de Playwright, uniquement requests + BeautifulSoup

import re
import time
import random
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# Logging (collecte des logs en mémoire pour affichage dans Streamlit)
# -----------------------------------------------------------------------------
_LOGS: List[str] = []

def log(msg: str):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    _LOGS.append(line)
    print(line, flush=True)

# -----------------------------------------------------------------------------
# Outils regex & scoring
# -----------------------------------------------------------------------------
def build_regex_list(terms: List[str]) -> List[str]:
    """Construit une liste de patterns OR avec \b … \b à partir d'une liste."""
    safe = [re.escape(t) for t in terms if t.strip()]
    return [rf"\b({'|'.join(safe)})\b"] if safe else []

def regex_any(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text or "", flags=re.I) for p in patterns)

def within_days(date_iso: str, max_days: int) -> bool:
    if not date_iso:
        return True
    try:
        dt = datetime.fromisoformat(date_iso.replace("Z", ""))
        return (datetime.utcnow() - dt) <= timedelta(days=max_days)
    except Exception:
        return True

# -----------------------------------------------------------------------------
# Réseau
# -----------------------------------------------------------------------------
_UA_LIST = [
    # quelques UA récents
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

def fetch(url: str, timeout: int = 25) -> str:
    """GET simple avec headers ; renvoie html (ou '' en cas d'erreur)."""
    headers = {
        "User-Agent": random.choice(_UA_LIST),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log(f"[NET] {url} -> {e}")
        return ""

# -----------------------------------------------------------------------------
# Scrapers (requests + BS4)
# NB: certains sites rendent peu de HTML sans JS. On tente plusieurs sélecteurs.
# -----------------------------------------------------------------------------
def _looks_blocked(html: str) -> bool:
    bad_signs = ["captcha", "enable javascript", "are you human", "access denied"]
    h = (html or "").lower()
    return any(s in h for s in bad_signs) or len(h) < 2000  # heuristique simple

def scrape_apec(keywords: List[str], max_pages: int = 1) -> List[Dict]:
    rows = []
    base = "https://www.apec.fr/candidat/recherche-emploi.html/emploi"
    for kw in keywords:
        q = requests.utils.quote(kw)  # ⚠️ pas de guillemets stricts
        url = f"{base}?motsCles={q}&lieux=France&sortsType=DATE"
        log(f"[APEC] {kw} → {url}")
        html = fetch(url)
        if not html:
            continue
        if _looks_blocked(html):
            log("[APEC] ⚠️ page suspecte (JS/CAPTCHA/AccessDenied)")
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("[data-testid='search-results'] article") or soup.select("article")
        log(f"[APEC] BRUT pour '{kw}': {len(cards)} cartes")

        for c in cards:
            title_el = c.select_one("h3, h2, a")
            link = c.select_one("a[href]")
            comp = c.select_one("[data-testid='company-name'], .company")
            loc  = c.select_one("[data-testid='job-location'], .location")
            date_el = c.select_one("time[datetime]")

            title = (title_el.get_text(" ", strip=True) if title_el else "") or "Sans titre"
            href  = link["href"] if link and link.has_attr("href") else ""
            if href.startswith("/"):
                href = "https://www.apec.fr" + href

            rows.append({
                "title": title, "company": comp.get_text(strip=True) if comp else "",
                "location": loc.get_text(strip=True) if loc else "France",
                "url": href, "published_at": (date_el["datetime"] if date_el and date_el.has_attr("datetime") else None),
                "source": "apec",
            })
        time.sleep(0.6)
    return rows

def scrape_indeed(keywords: List[str]) -> List[Dict]:
    rows = []
    base = "https://fr.indeed.com/jobs"
    for kw in keywords:
        q = requests.utils.quote(kw)  # ⚠️ pas de guillemets
        url = f"{base}?q={q}&l=France&sort=date"
        log(f"[Indeed] {kw} → {url}")
        html = fetch(url)
        if not html:
            continue
        if _looks_blocked(html):
            log("[Indeed] ⚠️ page suspecte (JS/CAPTCHA/AccessDenied)")
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = (soup.select("a.jcs-JobTitle") or
                 soup.select("h2.jobTitle a") or
                 soup.select("a[href*='/rc/clk'], a[href*='/pagead/']"))
        log(f"[Indeed] BRUT pour '{kw}': {len(cards)} cartes")

        for a in cards:
            title = a.get_text(" ", strip=True) or "Sans titre"
            href  = a.get("href", "")
            if href.startswith("/"):
                href = "https://fr.indeed.com" + href
            parent = a.find_parent(["div", "article"]) or a
            comp = parent.select_one(".companyName, [data-testid='company-name']")
            loc  = parent.select_one(".companyLocation, [data-testid='text-location']")
            rows.append({
                "title": title, "company": comp.get_text(strip=True) if comp else "",
                "location": loc.get_text(strip=True) if loc else "France",
                "url": href, "published_at": None, "source": "indeed",
            })
        time.sleep(0.6)
    return rows

def scrape_wttj(keywords: List[str]) -> List[Dict]:
    rows = []
    base = "https://www.welcometothejungle.com/fr/jobs"
    for kw in keywords:
        q = requests.utils.quote(kw)
        url = f"{base}?query={q}&aroundQuery=France&sortBy=publication"
        log(f"[WTTJ] {kw} → {url}")
        html = fetch(url)
        if not html:
            continue
        if _looks_blocked(html):
            log("[WTTJ] ⚠️ page suspecte (JS/CAPTCHA/AccessDenied)")
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("article a[href*='/fr/offres-emploi/']")
        log(f"[WTTJ] BRUT pour '{kw}': {len(cards)} cartes")

        for a in cards:
            title = a.get_text(" ", strip=True) or "Sans titre"
            href  = a.get("href", "")
            if href.startswith("/"):
                href = "https://www.welcometothejungle.com" + href
            parent = a.find_parent("article") or a
            comp = parent.select_one("[data-testid='company-name']")
            loc  = parent.select_one("[data-testid='job-location']")
            rows.append({
                "title": title, "company": comp.get_text(strip=True) if comp else "",
                "location": loc.get_text(strip=True) if loc else "France",
                "url": href, "published_at": None, "source": "wttj",
            })
        time.sleep(0.6)
    return rows

def scrape_hellowork(keywords: List[str]) -> List[Dict]:
    rows = []
    base = "https://www.hellowork.com/fr-fr/emploi/recherche.html"
    for kw in keywords:
        q = requests.utils.quote(kw)
        url = f"{base}?k={q}&l=France&sort=DATE"
        log(f"[HelloWork] {kw} → {url}")
        html = fetch(url)
        if not html:
            continue
        if _looks_blocked(html):
            log("[HelloWork] ⚠️ page suspecte (JS/CAPTCHA/AccessDenied)")
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("article a[href*='/offres/'], article a[href*='/emploi/'], a[data-cy='offerLink']")
        log(f"[HelloWork] BRUT pour '{kw}': {len(cards)} cartes")

        for a in cards:
            title = a.get_text(" ", strip=True) or "Sans titre"
            href  = a.get("href", "")
            if href.startswith("/"):
                href = "https://www.hellowork.com" + href
            parent = a.find_parent("article") or a
            comp = parent.select_one("[data-cy='companyName'], [class*='company']")
            loc  = parent.select_one("[data-cy='jobLocation'], [class*='location']")
            date_el = parent.select_one("time[datetime]")
            rows.append({
                "title": title, "company": comp.get_text(strip=True) if comp else "",
                "location": loc.get_text(strip=True) if loc else "France",
                "url": href, "published_at": (date_el["datetime"] if date_el and date_el.has_attr("datetime") else None),
                "source": "hellowork",
            })
        time.sleep(0.6)
    return rows


# -----------------------------------------------------------------------------
# Scoring & filtre (inspiré de ta version précédente)
# -----------------------------------------------------------------------------
def score_row(row: Dict,
              MUST_HAVE: List[str],
              NICE_TO_HAVE: List[str],
              EXCLUSIONS: List[str],
              SENIORITY: List[str],
              CONTRACT_PREFER: List[str],
              CONTRACT_EXCLUDE: List[str],
              REMOTE_OK: List[str],
              CITIES_BONUS: List[str],
              KEYWORDS: List[str]) -> int:
    title = row.get("title", "") or ""
    company = row.get("company", "") or ""
    location = (row.get("location", "") or "").lower()
    desc = f"{title} {company} {location}"

    # exclusions
    if regex_any(EXCLUSIONS, desc) or regex_any(CONTRACT_EXCLUDE, desc):
        return 0

    s = 0
    # must-have
    if regex_any(MUST_HAVE, title):
        s += 40
    elif regex_any(MUST_HAVE, desc):
        s += 30
    else:
        s -= 10

    # filet sécurité : mot-clé brut dans le titre
    t_low = title.lower()
    if any(k.lower() in t_low for k in KEYWORDS):
        s += 20

    if regex_any(SENIORITY, desc):
        s += 15
    if regex_any(CONTRACT_PREFER, desc):
        s += 10
    if regex_any(NICE_TO_HAVE, desc):
        s += 15
    if regex_any(REMOTE_OK, desc):
        s += 5
    if regex_any(CITIES_BONUS, location):
        s += 10

    return max(0, min(100, s))

# -----------------------------------------------------------------------------
# Moteur principal
# -----------------------------------------------------------------------------
def run_search(cfg: Dict) -> Tuple[pd.DataFrame, str]:
    """
    cfg attend les clés :
      - KEYWORDS (List[str])
      - MIN_SCORE (int)
      - MUST_HAVE / NICE_TO_HAVE / EXCLUSIONS / SENIORITY / CONTRACT_PREFER / CONTRACT_EXCLUDE / REMOTE_OK (List[str] patterns)
      - CITIES_BONUS (List[str] patterns)
      - MAX_AGE_DAYS (int) optionnel
      - SOURCES (List[str]) optionnel parmi: apec, indeed, wttj, hellowork
    """
    _LOGS.clear()
    KEYWORDS = cfg.get("KEYWORDS", [])
    MIN_SCORE = int(cfg.get("MIN_SCORE", 40))
    MAX_AGE_DAYS = int(cfg.get("MAX_AGE_DAYS", 14))
    SOURCES = cfg.get("SOURCES", ["apec", "indeed", "wttj", "hellowork"])

    MUST_HAVE = cfg.get("MUST_HAVE", [])
    NICE_TO_HAVE = cfg.get("NICE_TO_HAVE", [])
    EXCLUSIONS = cfg.get("EXCLUSIONS", [])
    SENIORITY = cfg.get("SENIORITY", [])
    CONTRACT_PREFER = cfg.get("CONTRACT_PREFER", [])
    CONTRACT_EXCLUDE = cfg.get("CONTRACT_EXCLUDE", [])
    REMOTE_OK = cfg.get("REMOTE_OK", [])
    CITIES_BONUS = cfg.get("CITIES_BONUS", [])

    log("=== DÉMARRAGE (requests+BS4) ===")
    log(f"Keywords: {KEYWORDS}")
    all_rows: List[Dict] = []

    try:
        if "apec" in SOURCES:
            all_rows += scrape_apec(KEYWORDS)
    except Exception as e:
        log(f"[WARN] APEC: {e}")

    try:
        if "indeed" in SOURCES:
            all_rows += scrape_indeed(KEYWORDS)
    except Exception as e:
        log(f"[WARN] Indeed: {e}")

    try:
        if "wttj" in SOURCES:
            all_rows += scrape_wttj(KEYWORDS)
    except Exception as e:
        log(f"[WARN] WTTJ: {e}")

    try:
        if "hellowork" in SOURCES:
            all_rows += scrape_hellowork(KEYWORDS)
    except Exception as e:
        log(f"[WARN] HelloWork: {e}")

    # dédup par URL
    uniq = {}
    for r in all_rows:
        u = r.get("url", "")
        if u and u not in uniq:
            uniq[u] = r
    rows = list(uniq.values())
    log(f"Total brut (dédupliqué): {len(rows)}")

    # scoring + filtrage
    kept = []
    for r in rows:
        r["score"] = score_row(
            r, MUST_HAVE, NICE_TO_HAVE, EXCLUSIONS, SENIORITY,
            CONTRACT_PREFER, CONTRACT_EXCLUDE, REMOTE_OK, CITIES_BONUS, KEYWORDS
        )
        if r["score"] >= MIN_SCORE and within_days(r.get("published_at"), MAX_AGE_DAYS):
            kept.append(r)
    log(f"Conservés (score ≥ {MIN_SCORE}): {len(kept)}")

    if kept:
        kept.sort(key=lambda x: (-x.get("score", 0), x.get("source", ""), x.get("title", "")))
        df = pd.DataFrame(kept)
    else:
        df = pd.DataFrame(columns=["title", "company", "location", "url", "published_at", "source", "score"])

    # retourne DataFrame + logs texte
    return df, "\n".join(_LOGS)
