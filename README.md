# Curation Hub

Single-page app that replaces the shared Google Sheet for technology watch:

paste links → automatic distribution → page captured as PDF → internal LLM pre-fills the
summary and AntBox metadata (only when certain) → analyst reviews in a side panel →
approval writes an immutable registry entry with permanent PDF + CSV → executive reports.

```
veille-etca/
├── backend/                  FastAPI (Python) — also serves the built dashboard
│   ├── app/
│   │   ├── main.py           REST API (/api/...) + dashboard route
│   │   ├── config.py         settings read from backend/.env
│   │   ├── db.py             SQLite schema (data/curation.db), audit log, settings store
│   │   ├── urls.py           smart-paste URL extraction, normalisation (UTM cleanup), SimHash
│   │   ├── pipeline.py       ingestion (anti-duplicates, distribution) and processing of a link
│   │   ├── worker.py         background job queue: rate limits, retries, daily digest
│   │   ├── distribution.py   capped round-robin among active profiles, global queue overflow
│   │   ├── capture.py        web page -> clean PDF + text (Playwright + Chrome)
│   │   ├── social.py         shared social account: session cookies + automatic re-login (2FA)
│   │   ├── social_login.py   manual login fallback (python -m app.social_login)
│   │   ├── llm.py            LLM calls, high-certainty filter, report synthesis
│   │   ├── referential.py    AntBox metadata and their allowed values (your CSV/TXT)
│   │   ├── exports.py        PDF / CSV / both (synchronized names), AntBox package
│   │   ├── registry.py       immutable, hash-chained registry + verification
│   │   ├── reports.py        executive reports (Markdown, HTML, PDF)
│   │   ├── stats.py          dashboard widgets
│   │   ├── live.py           live updates (Server-Sent Events) and presence
│   │   ├── notify.py         Google Chat digest
│   │   └── sanitize.py       cleans the WYSIWYG summary HTML
│   ├── dashboard/            built Angular app (served at http://localhost:8000)
│   ├── data/                 your AntBox referential + database + files (created automatically)
│   ├── tests/                automatic tests (pytest)
│   ├── .env.example          configuration template
│   └── requirements.txt
├── frontend/                 Angular 20 source of the dashboard
│   └── src/app/
│       ├── pages/            dashboard.ts, registry.ts, reports.ts, settings.ts
│       └── ui/               side panel, smart paste, WYSIWYG editor, field picker, profile switcher...
└── deploy/                   systemd service + Nginx config for the Linux VM
```

All commands below are typed in the **Miniforge Prompt**, one line at a time.

---

## 1. Install

```
cd C:\Tools\DEV\ETCA\veille-etca\backend
pip install -r requirements.txt
```

Certificate error? `pip install --use-feature=truststore -r requirements.txt`

## 2. Configure

1. Put your AntBox referential (CSV or TXT) in `backend\data\`. Any `.csv` / `.txt` whose name
   contains `antbox`, `referentiel` or `referential` is detected automatically (`;`, `,` or tab separated,
   UTF-8 or Excel encoding). Check it with: `python -m app.referential`
2. Create the configuration file:
   ```
   copy .env.example .env
   notepad .env
   ```
   Fill `LLM_URL`, `LLM_API_KEY`, `LLM_MODEL` (or set `LLM_FAKE=true` to test without the LLM).
   Save and close.

## 3. Test and run

```
python -m pytest -q
```

You should read `22 passed`. Then:

```
uvicorn app.main:app --reload
```

Open **http://localhost:8000**. API documentation: http://localhost:8000/docs

First steps in the app:
1. Top right → **Manage profiles…** → add the analysts → pick your profile.
2. **Add links**: paste any text (an email, chat messages…). URLs are detected live; green = new,
   orange = already in the database. Choose *Auto* (round-robin), *Me* or *Global queue*, then **Add**.
3. A few seconds later the links are **To review**. Click a row: the side panel shows the AI summary
   (editable) and the AntBox CSV columns with confidence badges (hover for the evidence quote).
4. **Approve** (or `A` / `Ctrl+Enter`). The link goes to the **Registry** with its permanent PDF and CSV.
5. **Reports** → period + granularity → **Generate report** → download the PDF.

Keyboard: `J`/`K` move, `Enter` open, `A` approve, `X` reject, `C` claim, `Ctrl+S` save, `Ctrl+K` command palette.

## 4. Features

| Area | What it does |
|---|---|
| Header | Profile switcher; add / rename / mark away / delete profiles. Delete is a soft delete (history kept); pending items go to the global queue or are redistributed. |
| Ingestion | Smart paste (regex extraction from free text), live duplicate check (normalised URL without `utm_*`, `fbclid`, `www.`, `m.`, trailing `/`, fragment), manual input with instant validation, CSV/TXT import, capped round-robin distribution. After capture, redirects / canonical URLs reveal hidden duplicates, and SimHash flags near-identical content. |
| Social | LinkedIn, X, Instagram, Facebook captured with the shared account: cookies kept in `data/secrets/social_session.json`, automatic re-login (with TOTP 2FA) from the credentials in `.env`, human-like pacing (20–40 s, daily cap). Turned on in **Settings**. |
| Dashboard | Widgets: my queue, global queue, progress today, 7-day velocity (me vs team), health (processing, errors, aging items, AI acceptance). Table with filters (Me/All/Global, To do/Done/All, document type, date range, search), sorting, bulk actions, aging badges (3 and 7 days), live updates, edit locks. |
| Validation | LLM auto-fill with a strict policy: each value needs confidence ≥ 80 % **and** a verbatim evidence quote found in the article **and** must exist in the referential, otherwise the field stays empty (uncertain suggestions are shown as one-click ghost chips). WYSIWYG summary (dashboard and reports). Export PDF, AntBox import CSV or both (same file name, zip). |
| Registry | Append-only list (date, title, metadata, validating operator, permanent PDF/CSV links). The database refuses edits/deletions; a SHA-256 hash chain + file hashes can be verified in one click. |
| Reports | Last 7 days / last month / custom; general, by document type or by user. The LLM writes the executive synthesis citing articles as [n]; the list of links with their summaries is appended by code (always complete and correct). Markdown, HTML, PDF. |
| Extras | Google Chat daily digest, job queue with retries (30 s / 2 min / 10 min), few-shot examples from validated records to improve tagging, command palette, undo toasts. |

## 5. Connect the internal LLM

In `backend\.env`:

```
LLM_URL=https://.../v1/chat/completions
LLM_API_KEY=your-key
LLM_MODEL=model-name
LLM_FAKE=false
```

Restart (`Ctrl+C`, then `uvicorn app.main:app --reload`) and click **Settings → Internal LLM → Test connection**.
If the LLM rejects `response_format`, set `LLM_JSON_MODE=false`. Strictness: `AI_CONFIDENCE_THRESHOLD`
(default 0.8) and `AI_REQUIRE_EVIDENCE`. The prompt is `SYSTEM_PROMPT` in `app\llm.py`.

## 6. Shared social account

1. Get approval (Security + each network's terms of use). Capture is OFF by default.
2. In `backend\.env`, for each network used:
   ```
   SOCIAL_LINKEDIN_USERNAME=account@company.com
   SOCIAL_LINKEDIN_PASSWORD=password
   SOCIAL_LINKEDIN_TOTP=SETUPKEYOFTHEAUTHENTICATORAPP
   ```
   `TOTP` is optional: it is the "setup key" shown when adding the account to an authenticator app;
   the app then computes the 6-digit code itself.
3. **Settings → Shared social account** → turn it ON → **Test login** for each network.
4. If a network asks for a check that cannot be automated (captcha, SMS), the link goes to *Error*
   with the explanation: run `python -m app.social_login`, log in by hand, then **Retry**.

`.env` and `data\secrets\` contain secrets: never on Git, never shared.

## 7. AntBox import CSV

Each approved link gives a PDF and one row of the AntBox connector CSV (`;` separated), in the exact template order:

| Column | Filled by |
|---|---|
| File Name | PDF file name (same name as the PDF in the package) |
| URL | source URL (tracking parameters removed) |
| Check | empty — filled by the connector |
| Attachment Clearance Level | constant `Public` (`ANTBOX_CLEARANCE_LEVEL`) |
| Attachment Co-redactor | constant `Group: Co-Writer` (`ANTBOX_CO_REDACTOR`) |
| Document type | LLM: `Press articles / Social Network` or `Commercial Brochure / Manufacturer website` (social links are always press) |
| Domain | analyst, optional (too complex for the AI) |
| Attachment redactor | automatic: AntBox redactor of the approving analyst (set it in the profile) |
| Meeting or Forum | not used (empty) |
| Year, Parts/Modules, Optionnal, OperationType, Vehicles | LLM, from the referential values (empty when not certain) |

The **AntBox package** (Registry → *AntBox package*, or bulk export) is a zip with one CSV per document type,
`connector_press_article.csv` and `connector_commercial_brochure.csv`, plus every PDF: import them into AntBox.

When the referential file uses other names than the CSV columns, map them in `.env`:
`FIELD_MAP=Parts/Modules=Components/Sub-System,OperationType=Mission,Vehicles=Aircrafts` (default).
Settings → *AntBox import CSV* shows how every column is filled. Other options: `AI_FIELDS`, `UNUSED_FIELDS`,
`REQUIRED_FIELDS`, `DOCUMENT_TYPES`, `CSV_MULTI_SEPARATOR`, `CSV_VALUES=label` or `id`.

## 8. Edit the dashboard (Node.js required)

```
cd C:\Tools\DEV\ETCA\veille-etca\frontend
npm install
npx ng serve
```

Open http://localhost:4200 (the API must be running; `proxy.conf.json` forwards `/api`).
After changes: `npx ng build`, then copy `frontend\dist\frontend\browser\*` into `backend\dashboard\`.

## 9. Deploy on the Linux VM

1. `git clone` into `/opt/curation-hub`, `python3 -m venv /opt/curation-hub/venv`,
   `/opt/curation-hub/venv/bin/pip install -r backend/requirements.txt`,
   `/opt/curation-hub/venv/bin/playwright install chromium` (or keep `BROWSER_CHANNEL=chrome` if Chrome is installed).
2. Create `backend/.env` (`chmod 600`), with `PUBLIC_URL=https://curation.internal`.
3. Copy `deploy/curation-api.service` (systemd) and `deploy/nginx-curation.conf` (Nginx). Run **one** process only.
