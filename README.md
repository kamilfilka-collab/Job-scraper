# 💼 AI Kariérny Scraper (Slovensko & Rakúsko + LinkedIn)

Automatizovaný Python scraper bežiaci zadarmo na **GitHub Actions**, ktorý každé 3 hodiny prehľadáva najväčšie pracovné portály na Slovensku a v Rakúsku, sťahuje celé texty inzerátov, vyhodnocuje ich cez **Google Gemini AI** presne podľa tvojho životopisu a posiela relevantné ponuky na **Telegram**.

---

## 🌐 Sledované portály
1. **Profesia.sk** (Bratislavský kraj + Remote)
2. **LinkedIn Jobs Slovensko** (verejné Guest API bez rizika zablokovania účtu)
3. **LinkedIn Jobs Rakúsko** (Viedeň & celé Rakúsko)
4. **Karriere.at** (Viedeň & Rakúsko)
5. **Jobs.at** (Rakúsko)

---

## 🧠 Ako funguje AI filter (prispôsobený na tvoj profil)
Pre každý nový inzerát scraper stiahne aj jeho **celý detailný popis** a pošle ho do Gemini AI s týmito pravidlami:
* ✅ **Hľadá:** *Supplier / Vendor Performance Management (SLAs, KPIs, SIPs, QBRs), Cost Optimization, OPEX Budgeting, Finančné a komerčné analýzy, FinOps / IT Financial Management, Vendor Strategy & Governance, Service Delivery, Project Sourcing / RFP spolupráca* (naprieč všetkými odvetviami – IT, financie, energetika, výroba, pharma, telco).
* ❌ **Striktne vyraďuje:** *Supplier Risk Management / Third-Party Risk (TPRM) / Audit / Compliance / DORA* (keďže risk robiť nechceš), juniorské pozície a čisto továrenskú kontrolu kvality súčiastok.
* 🇬🇧 **Jazykový filter pre Rakúsko:** Keďže nevieš po nemecky, scraper pri rakúskych ponukách (Karriere.at, Jobs.at, LinkedIn AT) skontroluje text inzerátu. Ak inzerát vyžaduje nemčinu (*Fließend Deutsch / Verhandlungssicher*), automaticky ho zahodí. Na Telegram ti pošle **iba tie rakúske ponuky, kde je pracovným jazykom angličtina**.

---

## 🚀 Návod na nasadenie na GitHub (5 minút)

1. Vytvor si na GitHube nový repozitár (napr. `job-career-scraper` – môže byť **Private** alebo **Public**).
2. Nahraj doň všetky súbory z priečinka `c:\ANTIGRAVITY\job_career_scraper`:
   * `.github/workflows/job_scraper.yml`
   * `scraper.py`
   * `watchlist.txt`
   * `requirements.txt`
   * `seen_jobs.txt`
   * `README.md`
3. V repozitári na GitHube choď do **Settings** → **Secrets and variables** → **Actions** → **New repository secret** a pridaj tie isté 3 kľúče, ktoré už používaš pri Bazoš scraperi:
   * `GEMINI_API_KEY` – tvoj API kľúč z Google AI Studio
   * `TELEGRAM_BOT_TOKEN` – token tvojho Telegram bota (od `@BotFather`)
   * `TELEGRAM_CHAT_ID` – tvoje Chat ID na Telegrame
4. Choď do záložky **Actions**, klikni vľavo na **Career Job AI Scraper (SK & AT + LinkedIn)** a stlač tlačidlo **Run workflow**.
   * Hneď ti príde na Telegram testovacia správa a o pár sekúnd aj prvé vyfiltrované pracovné ponuky!
   * Všetky nájdené ponuky sa zároveň automaticky ukladajú do prehľadnej tabuľky `najdene_pozicie.csv` priamo v repozitári.
