import sys, asyncio
if sys.platform.startswith("win"):
    # Corrige l'erreur NotImplementedError sur Windows (Python 3.13)
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import re, pandas as pd
from datetime import datetime
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

# ---------- Helpers ----------
def build_regex_list(terms):
    if not terms: return []
    safe=[re.escape(t) for t in terms]
    return [rf"\b({'|'.join(safe)})\b"]

def regex_any(patterns, text):
    return any(re.search(p, text or "", flags=re.I) for p in patterns)

class Logger:
    def __init__(self): self.lines=[]
    def log(self, msg):
        line=f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True); self.lines.append(line)

def iter_keyword_queries(keywords):
    """Une requête par mot-clé (entre guillemets) pour éviter l'AND global."""
    for kw in keywords:
        kw=kw.strip()
        if kw:
            yield kw, quote_plus(f'"{kw}"')

def accept_cookies(page):
    sels = [
        "#onetrust-accept-btn-handler","button#onetrust-accept-btn-handler",
        "#didomi-notice-agree-button","[data-testid='uc-accept-all-button']",
        "button:has-text('Tout accepter')","button:has-text(\"J'accepte\")",
        ".qc-cmp2-summary-accept"
    ]
    for s in sels:
        try:
            page.locator(s).first.click(timeout=1000)
            return True
        except Exception:
            pass
    return False

# ---------- Scoring ----------
def score_row(row, cfg):
    title=row.get("title",""); company=row.get("company",""); location=row.get("location","") or ""
    desc=f"{title} {company} {location}"
    url=row.get("url","")

    # Exclusions bloquantes
    if regex_any(cfg["EXCLUSIONS"], title) or regex_any(cfg["EXCLUSIONS"], desc): return 0
    if regex_any(cfg["CONTRACT_EXCLUDE"], desc): return 0
    if any(u for u in cfg["ALREADY_APPLIED_URLS"] if u and u in url): return 0

    s=0
    # Must-have
    if regex_any(cfg["MUST_HAVE"], title): s+=40
    elif regex_any(cfg["MUST_HAVE"], desc): s+=30
    else: s-=10

    # filet sécurité : présence d’UN keyword dans le titre
    if any(k.lower() in title.lower() for k in cfg["KEYWORDS"]): s+=20

    # Autres critères
    if regex_any(cfg["SENIORITY"], desc): s+=15
    if regex_any(cfg["CONTRACT_PREFER"], desc): s+=10
    if regex_any(cfg["NICE_TO_HAVE"], desc): s+=15
    if regex_any(cfg["REMOTE_OK"], desc): s+=5
    if cfg["CITIES_BONUS"] and regex_any(cfg["CITIES_BONUS"], (location or "").lower()): s+=10
    if regex_any(cfg["COMPANY_WHITELIST"], company): s+=10
    if regex_any(cfg["COMPANY_BLACKLIST"], company): s-=20

    return max(0, min(100, s))

def matches_filters(row, cfg):
    s=score_row(row,cfg); row["score"]=s
    return s>=cfg["MIN_SCORE"]

# ---------- Scrapers ----------
def scrape_indeed(page, cfg, logger):
    rows=[]; seen=set()
    for kw_txt, kw_q in iter_keyword_queries(cfg["KEYWORDS"]):
        url=f"https://fr.indeed.com/jobs?q={kw_q}&l=France&sort=date"
        logger.log(f"[Indeed] {kw_txt}")
        page.goto(url, timeout=90000)
        accept_cookies(page)
        try: page.wait_for_selector("a[data-jk], div.job_seen_beacon a[href]", timeout=12000)
        except Exception: pass
        for _ in range(3): page.mouse.wheel(0,1800); page.wait_for_timeout(350)
        cards = page.locator("a[data-jk], div.job_seen_beacon a[href]")
        n = min(cards.count(), 60)
        for i in range(n):
            a=cards.nth(i)
            title=(a.inner_text(timeout=700) or "Sans titre").strip()
            href=a.get_attribute("href") or ""
            parent=a.locator("..")
            try: comp=parent.locator(".companyName, [data-testid='company-name']").first.inner_text(timeout=400).strip()
            except Exception: comp=""
            try: loc =parent.locator(".companyLocation, [data-testid='text-location']").first.inner_text(timeout=400).strip()
            except Exception: loc="France"
            url_abs=("https://fr.indeed.com"+href) if href.startswith("/") else href
            if not url_abs or url_abs in seen: continue
            row={"title":title,"company":comp,"location":loc,"url":url_abs,"published_at":None,"source":"indeed"}
            if matches_filters(row,cfg): seen.add(url_abs); rows.append(row)
    logger.log(f"Indeed: {len(rows)} retenues")
    return rows

def scrape_apec(page, cfg, logger):
    rows=[]; seen=set()
    for kw_txt, kw_q in iter_keyword_queries(cfg["KEYWORDS"]):
        url=f"https://www.apec.fr/candidat/recherche-emploi.html/emploi?motsCles={kw_q}&lieux=France&sortsType=DATE"
        logger.log(f"[APEC] {kw_txt}")
        page.goto(url, timeout=90000)
        accept_cookies(page)
        try: page.wait_for_selector("[data-testid='search-results'] article, article", timeout=12000)
        except Exception: pass
        for _ in range(3): page.mouse.wheel(0,1600); page.wait_for_timeout(350)
        cards = page.locator("[data-testid='search-results'] article")
        if cards.count()==0: cards=page.locator("article")
        n = min(cards.count(), 60)
        for i in range(n):
            c=cards.nth(i)
            title=(c.locator("h3").first.inner_text(timeout=600) if c.locator("h3").count() else "Sans titre").strip()
            link = c.locator("a[href]").first if c.locator("a[href]").count() else None
            href = link.get_attribute("href") if link else ""
            comp =(c.locator("[data-testid='company-name']").first.inner_text(timeout=400)
                   if c.locator("[data-testid='company-name']").count() else "").strip()
            loc  =(c.locator("[data-testid='job-location']").first.inner_text(timeout=400)
                   if c.locator("[data-testid='job-location']").count() else "France").strip()
            url_abs=("https://www.apec.fr"+href) if (href or "").startswith("/") else (href or "")
            if not url_abs or url_abs in seen: continue
            row={"title":title,"company":comp,"location":loc,"url":url_abs,"published_at":None,"source":"apec"}
            if matches_filters(row,cfg): seen.add(url_abs); rows.append(row)
    logger.log(f"APEC: {len(rows)} retenues")
    return rows

def scrape_wttj(page, cfg, logger):
    rows=[]; seen=set()
    for kw_txt, kw_q in iter_keyword_queries(cfg["KEYWORDS"]):
        url=f"https://www.welcometothejungle.com/fr/jobs?query={kw_q}&aroundQuery=France&sortBy=publication"
        logger.log(f"[WTTJ] {kw_txt}")
        page.goto(url, timeout=90000)
        accept_cookies(page)
        try: page.wait_for_selector("article a[href*='/fr/offres-emploi/']", timeout=12000)
        except Exception: pass
        for _ in range(3): page.mouse.wheel(0,1600); page.wait_for_timeout(350)
        cards = page.locator("article a[href*='/fr/offres-emploi/']")
        n = min(cards.count(), 60)
        for i in range(n):
            a=cards.nth(i)
            title=(a.inner_text(timeout=700) or "Sans titre").strip()
            href=a.get_attribute("href") or ""
            parent=a.locator("..").locator("..")
            try: comp=parent.locator("[data-testid='company-name'], [data-testid='job-card-company-name']").first.inner_text(timeout=400).strip()
            except Exception: comp=""
            try: loc =parent.locator("[data-testid='job-location'], [data-testid='job-card-location']").first.inner_text(timeout=400).strip()
            except Exception: loc="France"
            url_abs=("https://www.welcometothejungle.com"+href) if href.startswith("/") else href
            if not url_abs or url_abs in seen: continue
            row={"title":title,"company":comp,"location":loc,"url":url_abs,"published_at":None,"source":"wttj"}
            if matches_filters(row,cfg): seen.add(url_abs); rows.append(row)
    logger.log(f"WTTJ: {len(rows)} retenues")
    return rows

def scrape_hellowork(page, cfg, logger):
    rows=[]; seen=set()
    for kw_txt, kw_q in iter_keyword_queries(cfg["KEYWORDS"]):
        url=f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={kw_q}&l=France&sort=DATE"
        logger.log(f"[HelloWork] {kw_txt}")
        page.goto(url, timeout=90000)
        accept_cookies(page)
        try: page.wait_for_selector("article a[href*='/offres/'], a[data-cy='offerLink']", timeout=12000)
        except Exception: pass
        for _ in range(3): page.mouse.wheel(0,1600); page.wait_for_timeout(350)
        cards = page.locator("article a[href*='/offres/'], a[data-cy='offerLink']")
        n = min(cards.count(), 60)
        for i in range(n):
            a=cards.nth(i)
            title=(a.inner_text(timeout=700) or "Sans titre").strip()
            href=a.get_attribute("href") or ""
            parent=a.locator("..")
            try: comp=parent.locator("[data-cy='companyName'], [class*='company']").first.inner_text(timeout=400).strip()
            except Exception: comp=""
            try: loc =parent.locator("[data-cy='jobLocation'], [class*='location']").first.inner_text(timeout=400).strip()
            except Exception: loc="France"
            url_abs=("https://www.hellowork.com"+href) if (href or "").startswith("/") else (href or "")
            if not url_abs or url_abs in seen: continue
            row={"title":title,"company":comp,"location":loc,"url":url_abs,"published_at":None,"source":"hellowork"}
            if matches_filters(row,cfg): seen.add(url_abs); rows.append(row)
    logger.log(f"HelloWork: {len(rows)} retenues")
    return rows

# ---------- Entrée principale ----------
def run_search(config):
    """
    config = {
      KEYWORDS, SITES, CITIES_BONUS, MIN_SCORE, MUST_HAVE, NICE_TO_HAVE, EXCLUSIONS,
      SENIORITY, CONTRACT_PREFER, CONTRACT_EXCLUDE, REMOTE_OK,
      COMPANY_WHITELIST, COMPANY_BLACKLIST, ALREADY_APPLIED_URLS
    }
    """
    logger=Logger()
    logger.log(f"Keywords: {', '.join(config.get('KEYWORDS', []))}")
    logger.log(f"Sites: {', '.join(config.get('SITES', []))}")

    site_funcs = {
        "apec": scrape_apec,
        "indeed": scrape_indeed,
        "wttj": scrape_wttj,
        "hellowork": scrape_hellowork,
    }
    selected = [site_funcs[s] for s in config.get("SITES", []) if s in site_funcs]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage","--no-sandbox"])
        context = browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width":1366,"height":900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36")
        )
        page = context.new_page()

        all_rows=[]
        for func in selected:
            try:
                all_rows.extend(func(page, config, logger))
            except Exception as e:
                logger.log(f"[WARN] {func.__name__}: {e}")

        browser.close()

    # dédup & tri
    uniq={}
    for r in all_rows:
        u=r.get("url","")
        if u and u not in uniq: uniq[u]=r
    df=pd.DataFrame(list(uniq.values()))
    if not df.empty:
        df=df.sort_values(by=["score","source","title"], ascending=[False,True,True])
    return df, "\n".join(logger.lines)
