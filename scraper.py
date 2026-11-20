import csv
import html
import json
import os
import re
import time
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import requests

# ==========================================
# KONFIGURÁCIA (Načítanie z GitHub Secrets)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if TELEGRAM_BOT_TOKEN.lower().startswith("bot") and ":" in TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = TELEGRAM_BOT_TOKEN[3:].strip()

WATCHLIST_FILE = "watchlist.txt"
CSV_OUTPUT = "najdene_pozicie.csv"
SEEN_FILE = "seen_jobs.txt"

if not GEMINI_API_KEY:
    raise ValueError("Chýba GEMINI_API_KEY! Nastav ho v GitHub Secrets.")

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=60000),
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,sk;q=0.8,de;q=0.7",
}


# ==========================================
# TELEGRAM DIAGNOSTIKA A ODOSIELANIE
# ==========================================
def send_telegram_message(text: str) -> bool:
    """Odošle správu na Telegram a vypíše presnú odpoveď servera pri chybe."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            f" -> [Telegram PRESKOČENÝ]: Token={bool(TELEGRAM_BOT_TOKEN)}, ChatID={bool(TELEGRAM_CHAT_ID)}",
            flush=True,
        )
        return False

    tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            tg_url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=12,
        )
        if r.status_code == 200:
            print(" -> [Telegram OK]: Notifikácia odoslaná.", flush=True)
            return True
        else:
            print(f" -> [Telegram CHYBA HTTP {r.status_code}]: {r.text}", flush=True)
            return False
    except Exception as e:
        print(f" -> [Telegram VÝNIMKA]: {e}", flush=True)
        return False


def check_telegram_setup():
    """Skontroluje nastavenie Telegram bota pri štarte skriptu."""
    print("=== KONTROLA TELEGRAM NASTAVENÍ ===", flush=True)
    if not TELEGRAM_BOT_TOKEN:
        print(" [!] TELEGRAM_BOT_TOKEN je prázdny! Skontroluj GitHub Secrets.", flush=True)
    else:
        masked = (
            TELEGRAM_BOT_TOKEN[:6] + "..." + TELEGRAM_BOT_TOKEN[-4:]
            if len(TELEGRAM_BOT_TOKEN) > 10
            else "***"
        )
        print(f" [OK] TELEGRAM_BOT_TOKEN načítaný ({masked})", flush=True)

    if not TELEGRAM_CHAT_ID:
        print(" [!] TELEGRAM_CHAT_ID je prázdny! Skontroluj GitHub Secrets.", flush=True)
    else:
        print(f" [OK] TELEGRAM_CHAT_ID načítané ({TELEGRAM_CHAT_ID})", flush=True)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10
            )
            if r.status_code == 200:
                bot_info = r.json().get("result", {})
                print(
                    f" [OK] Bot overený: @{bot_info.get('username')} ({bot_info.get('first_name')})",
                    flush=True,
                )
            else:
                print(f" [!] Neplatný TELEGRAM_BOT_TOKEN: {r.text}", flush=True)
        except Exception as e:
            print(f" [!] Chyba pri overovaní bota: {e}", flush=True)
    print("===================================\n", flush=True)


# ==========================================
# POMOCNÉ FUNKCIE PRE SÚBORY
# ==========================================
def load_watchlist() -> list[str]:
    if not os.path.exists(WATCHLIST_FILE):
        print(f"Upozornenie: Súbor {WATCHLIST_FILE} neexistuje!")
        return []
    items = []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.strip()
            if clean and not clean.startswith("#"):
                items.append(clean)
    return items


def load_seen_jobs() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_jobs(seen_set: set[str]):
    recent = list(seen_set)[-5000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for job_id in recent:
            f.write(f"{job_id}\n")


def write_job_to_csv(row_data: list):
    file_exists = os.path.isfile(CSV_OUTPUT)
    with open(CSV_OUTPUT, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "Dátum",
                "Portál",
                "Skóre zhody",
                "Jazyk",
                "Názov pozície",
                "Firma",
                "Lokalita",
                "Plat",
                "AI Hodnotenie",
                "URL",
            ])
        writer.writerow(row_data)


def get_available_gemini_models() -> list[str]:
    preferred = ["gemini-2.5-flash", "gemini-2.0-flash"]
    try:
        discovered = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            short_name = name.replace("models/", "")
            actions = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            if any(
                bad in short_name.lower()
                for bad in (
                    "embedding",
                    "imagen",
                    "veo",
                    "tts",
                    "audio",
                    "robotics",
                    "aqa",
                    "learnlm",
                )
            ):
                continue
            if "gemini" in short_name.lower() or "gemma" in short_name.lower():
                if not actions or "generateContent" in str(actions):
                    discovered.append(short_name)

        for d in discovered:
            if d not in preferred:
                preferred.append(d)
    except Exception as e:
        print(f"Nepodarilo sa načítať zoznam modelov: {e}", flush=True)

    return preferred


# ==========================================
# RÝCHLY PRED-FILTER PODĽA NÁZVU POZÍCIE
# ==========================================
# Vyradí zjavne nerelevantné pozície ešte pred sťahovaním detailu (šetrí čas aj AI limity)
HARD_EXCLUDE_TITLE_KEYWORDS = [
    r"\brisk\b",
    r"\btprm\b",
    r"\baudit",
    r"\bcompliance\b",
    r"\blegal\b",
    r"\bcounsel\b",
    r"\bsecurity\b",
    r"\binformationssicherheit\b",
    r"\bsoftware\s+engineer",
    r"\bdeveloper\b",
    r"\bdevops\b",
    r"\barchitect\b",
    r"\bstore\s+manager",
    r"\brestaurant\b",
    r"\bhotel\b",
    r"\bcleaning\b",
    r"\breinigung",
    r"\bwarehouse\b",
    r"\blager",
    r"\bforklift\b",
    r"\bdriver\b",
    r"\bfahrer\b",
    r"\bnurse\b",
    r"\bpflege",
    r"\bteacher\b",
    r"\bintern\b",
    r"\binternship\b",
    r"\bpraktikum\b",
    r"\bpraktikant",
    r"\bwerkstudent",
    r"\bworking\s+student",
]


def is_title_obviously_irrelevant(title: str) -> bool:
    t_low = title.lower()
    for pat in HARD_EXCLUDE_TITLE_KEYWORDS:
        if re.search(pat, t_low):
            return True
    return False


# ==========================================
# 1. PROFESIA.SK (Bratislava / Trnava / Remote)
# ==========================================
def fetch_profesia_jobs(query: str) -> list[dict]:
    jobs = []
    encoded_q = quote_plus(query)
    # Bratislavský kraj + možnosť práce z domu
    url = f"https://www.profesia.sk/praca/bratislavsky-kraj/?search_anywhere={encoded_q}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("li.list-row"):
            a_el = li.select_one("h2 a[href*='/praca/']")
            if not a_el:
                continue
            title_el = a_el.select_one(".title")
            title = (
                title_el.get_text(" ", strip=True)
                if title_el
                else a_el.get_text(" ", strip=True)
            )
            href = a_el.get("href", "").split("?")[0]
            if not href:
                continue

            match_id = re.search(r"/(O\d+)", href)
            job_id = f"profesia_{match_id.group(1)}" if match_id else f"profesia_{href}"
            full_url = f"https://www.profesia.sk{href}" if href.startswith("/") else href

            employer_el = li.select_one(".employer")
            company = employer_el.get_text(" ", strip=True) if employer_el else "Neuvedená firma"

            loc_el = li.select_one(".job-location")
            location = loc_el.get_text(" ", strip=True) if loc_el else "Bratislavský kraj"

            sal_el = li.select_one(".label-group .label")
            salary = sal_el.get_text(" ", strip=True) if sal_el else "Neuvedený"

            if is_title_obviously_irrelevant(title):
                continue

            jobs.append({
                "id": job_id,
                "portal": "Profesia.sk",
                "country": "SK",
                "searched_for": query,
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "url": full_url,
                "description": "",
            })
    except Exception as e:
        print(f" -> [Profesia.sk] Chyba pre '{query}': {e}", flush=True)
    return jobs[:10]


# ==========================================
# 2. KARRIERE.AT (Viedeň & Rakúsko)
# ==========================================
def fetch_karriere_at_jobs(query: str) -> list[dict]:
    jobs = []
    slug_q = re.sub(r"[^a-zA-Z0-9]+", "-", query.lower()).strip("-")
    # Hľadáme vo Viedni a okolí (prípadne celé Rakúsko pre daný kľúčový výraz)
    url = f"https://www.karriere.at/jobs/{slug_q}/wien"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []

        for m in re.finditer(
            r'href="(https://www\.karriere\.at/jobs/(\d+))"[^>]*>(.*?)</a>',
            r.text,
            re.S,
        ):
            full_url, jid, title_html = m.groups()
            title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
            if not title or is_title_obviously_irrelevant(title):
                continue

            after_block = r.text[m.end() : m.end() + 900]
            comp_match = re.search(
                r'm-jobsListItem__companyName[^>]*>(.*?)</', after_block, re.S
            )
            if comp_match:
                company = html.unescape(
                    re.sub(r"<[^>]+>", "", comp_match.group(1))
                ).strip()
            else:
                parts = [
                    p.strip()
                    for p in re.sub(r"<[^>]+>", "|", after_block).split("|")
                    if p.strip()
                ]
                company = parts[0] if parts else "Rakúsko"

            loc_match = re.search(r'data-location="([^"]+)"', after_block)
            location = html.unescape(loc_match.group(1)).strip() if loc_match else "Wien / Austria"

            sal_match = re.search(r"(€\s*[\d\.,]+[^<|]*)", after_block)
            salary = html.unescape(sal_match.group(1)).strip() if sal_match else "Neuvedený"

            jobs.append({
                "id": f"karriere_{jid}",
                "portal": "Karriere.at",
                "country": "AT",
                "searched_for": query,
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "url": full_url,
                "description": "",
            })
    except Exception as e:
        print(f" -> [Karriere.at] Chyba pre '{query}': {e}", flush=True)
    return jobs[:8]


# ==========================================
# 3. JOBS.AT (Viedeň & Rakúsko)
# ==========================================
def fetch_jobs_at_jobs(query: str) -> list[dict]:
    jobs = []
    slug_q = re.sub(r"[^a-zA-Z0-9]+", "-", query.lower()).strip("-")
    url = f"https://www.jobs.at/j/{slug_q}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []

        for m in re.finditer(
            r'data-c-id="(\d+)"[^>]*data-c-title="([^"]+)"', r.text
        ):
            jid, raw_title = m.groups()
            title = html.unescape(raw_title).strip()
            if not title or is_title_obviously_irrelevant(title):
                continue

            after_block = r.text[m.end() : m.end() + 2000]
            after_tokens = [
                t.strip()
                for t in re.sub(r"<[^>]+>", "|", after_block).split("|")
                if t.strip()
            ]
            company = "Rakúsko"
            location = "Austria"
            for idx, tok in enumerate(after_tokens):
                if tok == "Firma" and idx + 1 < len(after_tokens):
                    company = html.unescape(after_tokens[idx + 1])
                if tok == "Standort" and idx + 1 < len(after_tokens):
                    location = html.unescape(after_tokens[idx + 1])

            full_url = f"https://www.jobs.at/i/{jid}"
            jobs.append({
                "id": f"jobsat_{jid}",
                "portal": "Jobs.at",
                "country": "AT",
                "searched_for": query,
                "title": title,
                "company": company,
                "location": location,
                "salary": "Viď detail",
                "url": full_url,
                "description": "",
            })
    except Exception as e:
        print(f" -> [Jobs.at] Chyba pre '{query}': {e}", flush=True)
    return jobs[:6]


# ==========================================
# 4. LINKEDIN JOBS (Slovensko + Viedeň/Rakúsko)
# ==========================================
def fetch_linkedin_jobs(query: str, location_query: str, country_code: str) -> list[dict]:
    jobs = []
    encoded_q = quote_plus(query)
    encoded_loc = quote_plus(location_query)
    # f_TPR=r604800 = inzeráty za posledných 7 dní
    url = (
        f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={encoded_q}&location={encoded_loc}&f_TPR=r604800&start=0"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("li"):
            title_el = li.select_one(".base-search-card__title")
            comp_el = li.select_one(".base-search-card__subtitle")
            loc_el = li.select_one(".job-search-card__location")
            link_el = li.select_one("a.base-card__full-link")
            entity_div = li.select_one("div[data-entity-urn]")

            if not title_el or not link_el:
                continue

            title = title_el.get_text(" ", strip=True)
            if is_title_obviously_irrelevant(title):
                continue

            company = comp_el.get_text(" ", strip=True) if comp_el else "Neuvedená firma"
            location = loc_el.get_text(" ", strip=True) if loc_el else location_query
            raw_link = link_el.get("href", "").split("?")[0]

            li_id = None
            if entity_div and entity_div.get("data-entity-urn"):
                urn_match = re.search(r"(\d+)$", entity_div.get("data-entity-urn"))
                if urn_match:
                    li_id = urn_match.group(1)
            if not li_id:
                url_id_match = re.search(r"-(\d+)$", raw_link)
                li_id = url_id_match.group(1) if url_id_match else raw_link

            jobs.append({
                "id": f"linkedin_{li_id}",
                "raw_li_id": li_id if str(li_id).isdigit() else None,
                "portal": f"LinkedIn ({country_code})",
                "country": country_code,
                "searched_for": query,
                "title": title,
                "company": company,
                "location": location,
                "salary": "Neuvedený",
                "url": raw_link,
                "description": "",
            })
    except Exception as e:
        print(f" -> [LinkedIn {country_code}] Chyba pre '{query}': {e}", flush=True)
    return jobs[:8]


# ==========================================
# SŤAHOVANIE DETAILNÉHO POPISU POZÍCIE (PRE JAZYK A AI FILTER)
# ==========================================
def enrich_job_with_description(job: dict):
    """Stiahne text inzerátu, aby AI vedela spoľahlivo overiť jazyk (EN vs DE) a náplň práce."""
    try:
        if job["id"].startswith("linkedin_") and job.get("raw_li_id"):
            detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['raw_li_id']}"
            r = requests.get(detail_url, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                desc_el = soup.select_one(".show-more-less-html__markup")
                if desc_el:
                    job["description"] = desc_el.get_text(" ", strip=True)[:1800]
                    return

        r = requests.get(job["url"], headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return

        # 1. Skúsime JSON-LD (Karriere.at ho má vždy čisté)
        ld_blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S
        )
        for b in ld_blocks:
            if "JobPosting" in b:
                try:
                    data = json.loads(b.strip())
                    raw_desc = data.get("description", "")
                    if raw_desc:
                        clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(
                            " ", strip=True
                        )
                        job["description"] = clean_desc[:1800]
                        return
                except Exception:
                    pass

        # 2. Fallback cez <main> alebo <body> (Profesia.sk, Jobs.at)
        clean_html = re.sub(
            r"<(script|style|header|footer|nav)[^>]*>.*?</\1>",
            " ",
            r.text,
            flags=re.S | re.I,
        )
        soup = BeautifulSoup(clean_html, "html.parser")
        main_el = soup.select_one("main") or soup.body
        if main_el:
            txt = main_el.get_text(" ", strip=True)
            job["description"] = re.sub(r"\s+", " ", txt)[:1800]
    except Exception as e:
        print(f" -> Nepodarilo sa stiahnuť detail pre {job['id']}: {e}", flush=True)


# ==========================================
# HROMADNÁ AI ANALÝZA KARIÉRNEJ ZHODY (GEMINI)
# ==========================================
def evaluate_jobs_with_ai(jobs: list[dict]) -> dict[str, dict] | None:
    items_for_prompt = [
        {
            "id": j["id"],
            "portal": j["portal"],
            "country": j["country"],
            "title": j["title"],
            "company": j["company"],
            "location": j["location"],
            "salary": j["salary"],
            "description_excerpt": j["description"][:1400],
        }
        for j in jobs
    ]

    prompt = f"""
    Si osobný kariérny poradca a headhunter pre seniorného kandidáta (Kamil, 15+ rokov B2B skúseností v AT&T a Orange Business, býva v Malackách pri Bratislave/Viedni).

    PROFIL A POŽIADAVKY KANDIDÁTA:
    1. HLAVNÉ ZAMERANIE (ČO CHCE ROBIŤ):
       - Supplier Management / Vendor Management / Partner Management
       - Supplier Operational Performance Management (KPIs, SLAs, Service Improvement Plans - SIPs, eskalácie, QBRs, executive reviews)
       - Cost Optimization / OPEX Budget Management / Finančné analýzy / Commercial Benchmarking / FinOps / IT Financial Management
       - Vendor Strategy, Service Delivery / Transition Management, Product/Vendor Onboarding
       - Project Sourcing / Procurement / Tendering (RFP) - kandidát nerobil čistý priamy nákup celý život, ale úzko spolupracoval so Sourcingom na veľkých RFP, vyjednával ceny a zmluvy, takže roly na pomedzí Vendor Managementu, Sourcingu a Cost Optimization sú vhodné.
       - Odvetvie: Nemusí to byť Telco! Vítané sú IT, Tech, Finančný sektor, Energetika, Pharma, Manufacturing, Shared Services atď.

    2. ČO KANDIDÁT NECHCE ROBIŤ (STRIKTNE VYRADIŤ -> is_match: false):
       - Supplier Risk Management / Third-Party Risk Management (TPRM) / Compliance / Audit / InfoSec / DORA špecialista (kandidát nechce robiť risk management!).
       - Čisto juniorské/entry-level pozície, stáže, alebo manuálna logistika/sklad/kvalita vo výrobe (ak ide o čisto výrobný inžiniering kvality súčiastok v továrni mimo komerčného/operačného riadenia dodávateľov, vyraď).
       - Čisto softvérový vývoj (programátor) alebo predajca v obchode.

    3. JAZYKOVÁ PODMIENKA (EXTRÉMNE DÔLEŽITÉ PRE RAKÚSKO):
       - Kandidát HOVORÍ PLYNULE PO ANGLICKY, SLOVENSKY a ČESKY.
       - Kandidát NEVIE PO NEMECKY!
       - Ak je pozícia v Rakúsku (alebo na Slovensku) a celý inzerát je v nemčine ALEBO vyžaduje plynulú nemčinu ("Fließend Deutsch", "Verhandlungssicheres Deutsch", "Sehr gute Deutschkenntnisse"), MUSÍŠ ju vyradiť (`is_match: false`).
       - Ak je pozícia v Rakúsku písaná v ANGLIČTINE a firemný jazyk je angličtina (nemčina nie je povinná, resp. je len výhodou "German is a plus"), daj `is_match: true`!

    Zoznam pracovných ponúk na posúdenie:
    {json.dumps(items_for_prompt, ensure_ascii=False)}

    Pre každú ponuku vráť objekt v JSON poli (zozname) s týmito poľami:
    - "id": rovnaké ID ponuky ako na vstupe
    - "is_match": true ak pozícia spĺňa zameranie kandidáta A ZÁROVEŇ nevyžaduje nemčinu A ZÁROVEŇ nejde o Risk Management. Inak false.
    - "match_score": číslo od 1 do 100 (ako veľmi pozícia sedí na profil Kamila; pre is_match=true dávaj 70 až 100).
    - "language": napr. "EN (Bez nemčiny)", "SK/EN", "DE (Vyžaduje nemčinu)".
    - "category": krátka kategória (napr. "Vendor & Performance Mgmt", "Cost Optimization & FinOps", "Sourcing & Vendor Strategy").
    - "reason": Stručné zhrnutie v slovenčine (1-2 vety: prečo pozícia sedí alebo prečo bola vyradená, plus či spomína plat).
    """

    available_models = get_available_gemini_models()

    for model_name in available_models:
        for attempt in range(2):
            try:
                print(
                    f"Posielam {len(jobs)} pracovných ponúk do Gemini AI ({model_name}, pokus {attempt + 1}/2)...",
                    flush=True,
                )
                cfg = (
                    types.GenerateContentConfig(temperature=0.1)
                    if "gemma" in model_name.lower()
                    else types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    )
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=cfg,
                )
                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(
                        r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL
                    ).strip()
                parsed_list = json.loads(raw_text)
                return {str(item.get("id")): item for item in parsed_list}
            except Exception as e:
                err_str = str(e)
                print(f" -> [{model_name} pokus {attempt + 1}/2 zlyhal]: {e}", flush=True)
                if any(
                    code in err_str
                    for code in ("429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "400")
                ):
                    print(" -> Prepínam na ďalší dostupný model...", flush=True)
                    break
                time.sleep(10)

    return None


# ==========================================
# ULOŽENIE A TELEGRAM NOTIFIKÁCIA
# ==========================================
def save_and_notify(job: dict, ai_info: dict):
    score = ai_info.get("match_score", 80)
    lang = ai_info.get("language", "EN/SK")
    cat = ai_info.get("category", "Supplier & Cost Mgmt")
    reason = ai_info.get("reason", "")

    row_data = [
        time.strftime("%Y-%m-%d %H:%M"),
        job["portal"],
        f"{score}%",
        lang,
        job["title"],
        job["company"],
        job["location"],
        job["salary"],
        reason,
        job["url"],
    ]
    write_job_to_csv(row_data)

    msg = (
        f"💼 NOVÁ POZÍCIA ({job['portal']} | Zhoda: {score}%)\n"
        f"📌 Rola: {job['title']}\n"
        f"🏢 Firma: {job['company']}\n"
        f"📍 Lokalita: {job['location']}\n"
        f"🗣️ Jazyk: {lang}\n"
        f"💰 Plat: {job['salary']}\n"
        f"🏷️ Oblasť: {cat}\n"
        f"🤖 AI hodnotenie: {reason}\n"
        f"🔗 Link: {job['url']}"
    )
    print("\n" + msg, flush=True)
    send_telegram_message(msg)


# ==========================================
# HLAVNÝ PROGRAM
# ==========================================
def main():
    check_telegram_setup()

    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("Manuálne spustenie (workflow_dispatch) -> posielam testovaciu správu...", flush=True)
        send_telegram_message(
            "✅ Kariérny AI Scraper (SK & AT + LinkedIn) práve beží! Hľadám anglické a slovenské pozície v oblasti Supplier Performance & Cost Optimization."
        )

    watchlist = load_watchlist()
    if not watchlist:
        print(f"Zoznam {WATCHLIST_FILE} je prázdny.", flush=True)
        return

    print(f"Načítaných {len(watchlist)} hľadaných kľúčových fráz z {WATCHLIST_FILE}.", flush=True)

    seen_jobs = load_seen_jobs()
    collected_jobs: dict[str, dict] = {}

    for query in watchlist:
        print(f"\n🔍 Hľadám výraz: '{query}'", flush=True)

        # 1. Profesia.sk
        p_jobs = fetch_profesia_jobs(query)
        print(f" -> [Profesia.sk]: {len(p_jobs)} ponúk", flush=True)
        for j in p_jobs:
            if j["id"] not in seen_jobs and j["id"] not in collected_jobs:
                collected_jobs[j["id"]] = j

        # 2. LinkedIn Slovensko
        li_sk = fetch_linkedin_jobs(query, "Slovakia", "SK")
        print(f" -> [LinkedIn SK]: {len(li_sk)} ponúk", flush=True)
        for j in li_sk:
            if j["id"] not in seen_jobs and j["id"] not in collected_jobs:
                collected_jobs[j["id"]] = j

        # 3. LinkedIn Rakúsko (Viedeň & celé Rakúsko)
        li_at = fetch_linkedin_jobs(query, "Austria", "AT")
        print(f" -> [LinkedIn AT]: {len(li_at)} ponúk", flush=True)
        for j in li_at:
            if j["id"] not in seen_jobs and j["id"] not in collected_jobs:
                collected_jobs[j["id"]] = j

        # 4. Karriere.at (Viedeň)
        k_at = fetch_karriere_at_jobs(query)
        print(f" -> [Karriere.at]: {len(k_at)} ponúk", flush=True)
        for j in k_at:
            if j["id"] not in seen_jobs and j["id"] not in collected_jobs:
                collected_jobs[j["id"]] = j

        # 5. Jobs.at
        j_at = fetch_jobs_at_jobs(query)
        print(f" -> [Jobs.at]: {len(j_at)} ponúk", flush=True)
        for j in j_at:
            if j["id"] not in seen_jobs and j["id"] not in collected_jobs:
                collected_jobs[j["id"]] = j

        time.sleep(0.8)

    new_jobs = list(collected_jobs.values())
    print(f"\nCelkovo nájdených {len(new_jobs)} nových (ešte nevidených) pracovných ponúk.", flush=True)

    if not new_jobs:
        return

    # Aby sme pri prvom spustení neprekročili timeout, spracujeme max 35 najnovších ponúk v jednom behu
    batch_jobs = new_jobs[:35]
    print(f"Sťahujem detailné popisy pre {len(batch_jobs)} ponúk (kontrola angličtiny a náplne práce)...", flush=True)
    for idx, job in enumerate(batch_jobs, 1):
        enrich_job_with_description(job)
        if idx % 10 == 0:
            print(f" -> Stiahnutých {idx}/{len(batch_jobs)} detailov...", flush=True)
        time.sleep(0.4)

    ai_results = evaluate_jobs_with_ai(batch_jobs)
    if ai_results is None:
        print("AI analýza momentálne zlyhala, ponuky neoznačujem ako videné a skúsim pri ďalšom behu.", flush=True)
        return

    matches_count = 0
    for job in batch_jobs:
        seen_jobs.add(job["id"])
        ai_info = ai_results.get(str(job["id"]), {})

        if ai_info.get("is_match"):
            matches_count += 1
            save_and_notify(job, ai_info)
        else:
            print(
                f" [X] Vyradené AI ({job['portal']}): {job['title']} ({job['company']}) -> {ai_info.get('reason', 'Nezodpovedá zadaniu')}",
                flush=True,
            )

    save_seen_jobs(seen_jobs)
    print(f"\nHotovo! Uložených a odoslaných {matches_count} relevantných pracovných ponúk.", flush=True)


if __name__ == "__main__":
    main()
