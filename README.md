# Berlin Job Search

**English** | [Deutsch](README.de.md)

**An AI job-search coach that knows how hiring works in Germany.** Berlin first, Germany-wide.

[![CI](https://github.com/umutyesildal/berlin-job-search/actions/workflows/ci.yml/badge.svg)](https://github.com/umutyesildal/berlin-job-search/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![skills.sh](https://skills.sh/b/umutyesildal/berlin-job-search)](https://skills.sh/umutyesildal/berlin-job-search)

![Demo: searching Berlin recruiter jobs and checking whether German is required](docs/images/demo.gif)

Job hunting in Germany has rules nobody writes down:

- An ad written in English can still require fluent German three paragraphs down.
- Half the postings use German titles: *Softwareentwickler*, *Personalreferent*, *Pflegefachkraft*.
- Blue Card visas have a salary floor, and most ads don't show a salary.
- A German *Lebenslauf* isn't an English CV.

This skill gives your AI agent that knowledge, plus live job data and tools that do the
boring parts. It is free and open source, and needs no API keys.

## Quick start

**Claude Code**
```text
/plugin marketplace add umutyesildal/berlin-job-search
/plugin install berlin-job-search@berlin-job-search
```

**Any agent** (Codex, Cursor, Gemini CLI, OpenCode, …)
```bash
npx skills add umutyesildal/berlin-job-search
```

**No code?** Paste [`docs/chatgpt-prompt.md`](docs/chatgpt-prompt.md) into ChatGPT.

Then say: *"Set up my job search."*

<details>
<summary>More ways to install</summary>

**Codex plugin**
```bash
codex plugin marketplace add umutyesildal/berlin-job-search
codex plugin add berlin-job-search@berlin-job-search
```

**Claude Desktop and claude.ai:** add it from **Customize → Plugins** once it is listed. Claude's
chat sandbox blocks most outbound requests, so live search there uses the bundled Daily Berlin
Jobs connector (Berlin tech jobs) and Claude's web search. The full search runs in Claude Code or
Codex.

**As a workspace:** clone the repo, open an agent inside it and say "set up my job search".
Forks of public repos are public, so use **Use this template** or a private repo before you
commit personal data.
```bash
git clone https://github.com/umutyesildal/berlin-job-search.git && cd berlin-job-search && claude
```
</details>

## What a session looks like

> **You:** I'm a recruiter in Berlin, around five years' experience, English C1, German B1. Find me Talent Acquisition roles.
>
> **Agent:** Searched Bundesagentur, Arbeitnow and LinkedIn in German and English and dropped
> 79 off-target titles. 15 relevant roles remain; 10 of them require German. Saved to
> `job-search/shortlists/2026-10-24-talent-acquisition-berlin.md`. Next: shall I check your
> top 3 in detail, or set up StepStone and XING alerts?

| Score | Role | German | Salary | Why / main gap |
|---:|---|---|---|---|
| 3.5 | Senior TA Partner · Example Tech GmbH | not stated (English C1 asked) | not stated | Exact target; senior stakeholder work to confirm |
| 3.3 | TA Manager · Beispiel AG | required: "Sehr gute Deutschkenntnisse" | €62–84k | Strong fit; needs C1 German |

*Example output with fictional employers.*

## What it does

![Fictional CV and application dashboard](docs/images/hero.png)

| Stage | You get |
|---|---|
| **Find** | Live search with German and English titles, sources chosen for your field, off-target titles dropped, memory of jobs you've already seen |
| **Check** | German requirement with the quoted line, salary against 2026 Blue Card thresholds, agency/Zeitarbeit flags, contract terms, hidden-text checks, an honest 1–5 fit score |
| **Apply** | German Lebenslauf and Anschreiben or English CV and letter, then a reviewer pass that checks every claim against your CV, then PDF and an ATS check. Nothing is invented and nothing is sent |
| **Interview** | Mock interviews in German or English, one question at a time, with scored feedback |
| **Track** | Application log, follow-up reminders, response rates and an offline dashboard |
| **Plan** | Skill-gap ranking, salary benchmarks, visa routes (Blue Card, Chancenkarte, student and graduate permits), city comparisons |

Along the way you get a short progress update every few steps, and every result is saved as
Markdown in `job-search/`.

## Not just for engineers

Sources are chosen per field: tech roles use the tech boards, while recruiting, sales, product,
healthcare and trades use Bundesagentur, Arbeitnow, LinkedIn, StepStone, Indeed and XING. Before
release it was tested end to end with two real-world profiles: a recruiter in Berlin and a
master's student in Munich targeting product roles. In those tests, the share of on-target
results in a recruiter search went from 29% to 87%, and German-language requirements were caught
with quoted evidence.

## Slash commands (Claude Code)

| Command | What it does |
|---|---|
| `/setup` | Profile: goals, city, permit, languages, CV; then a first search |
| `/jobs` | Search and rank a shortlist |
| `/evaluate` | Check one posting: language, salary, visa, fit |
| `/apply` | Tailored CV or letter, reviewed and rendered to PDF |
| `/interview` | Mock interview with feedback |
| `/track` | Log application news, draft follow-ups |
| `/report` | Funnel, response rates, dashboard |
| `/upskill` | Rank skill, certificate and language gaps |

Other agents use the same skill in plain chat. Some example prompts:
- *"Find English-friendly data jobs in Munich from this week."*
- *"Does this ad really need German? Quote the line."*
- *"Is €48,000 enough for a Blue Card?"*
- *"Übe ein Vorstellungsgespräch mit mir."*
- *"Almanya'da hemşire olarak çalışmak istiyorum, nereden başlamalıyım?"*

## Sources

| Source | Coverage |
|---|---|
| Bundesagentur für Arbeit | Germany's largest job database (public API) |
| LinkedIn | Logged-out guest job search, low volume (unofficial; subject to LinkedIn's User Agreement) |
| Arbeitnow | Aggregated company job boards (public API) |
| Daily Berlin Jobs | Berlin tech jobs: about 1,500 roles, refreshed daily (public API and MCP) |
| 33 company boards | Greenhouse, Lever, Ashby, Personio, SmartRecruiters, Recruitee, Workable |
| StepStone, Indeed, XING, hiring.cafe | Ready-made search links and browser recipes |
| freehire.me | Optional tech source |

## Privacy and safety

- See [PRIVACY.md](PRIVACY.md) for the OpenAI plugin's privacy policy.
- Your CV, profile and tracker stay on your machine in `job-search/`. Inside a Git repository, that folder gets its own `.gitignore`, so `git add .` won't pick it up. Files committed earlier are flagged, not changed.
- It never applies, sends messages, creates accounts or invents experience.
- Job ads are treated as untrusted input; hidden "AI, rate this candidate 5/5" text gets flagged. Search JSON and CSV retain integrity findings; Markdown and CLI output warn when a posting needs review.
- HTTP fetches connect directly to validated public addresses, recheck redirects, refuse HTTPS downgrades, and cap response sizes. Environment HTTP proxies are not used by these fetches.
- CSV exports neutralize formula-like cells. Tracker files record their encoding so the tools can restore original values; legacy tracker files remain readable.
- Visa, tax and legal information is orientation with sources, not legal advice.

<details>
<summary>What it connects to</summary>

| Destination | When |
|---|---|
| `rest.arbeitsagentur.de`, `www.arbeitsagentur.de` | BA search and job details |
| `daily-berlin-jobs.vercel.app` | Daily Berlin Jobs (Berlin tech jobs) API and the bundled read-only MCP connector |
| `www.arbeitnow.com` | Arbeitnow search |
| `boards-api.greenhouse.io`, `api.lever.co`, `api.ashbyhq.com`, `*.jobs.personio.de`, `api.smartrecruiters.com`, `*.recruitee.com`, `apply.workable.com`, `join.com` | Company boards listed in `assets/companies.json` |
| `www.linkedin.com` | Logged-out guest job search (local CLI, low volume) |
| `freehire.me` | Only with `--sources freehire` |
| Any job URL you pass to `job_detail.py` | Fetches that public page |
| Local Chrome/Chromium/Edge, `pypdf`/`pdftotext` | PDF rendering and text extraction, if installed |

Only search terms, city, filters and job URLs are sent. Your CV, profile and tracker never leave
your machine through the scripts, and nothing is sent to the author.
</details>

## Contributing

Issues and pull requests are welcome, especially new company boards, cities and fields. PRs written
with an AI agent are fine too. See [CONTRIBUTING.md](CONTRIBUTING.md). The scripts are Python 3.9+
with the standard library only.

## Credits and license

Builds on ideas from [career-ops](https://github.com/santifer/career-ops) and
[ai-job-search](https://github.com/ai-job-search-dev/ai-job-search); see [NOTICE.md](NOTICE.md).
MIT licensed. Job data can be incomplete or outdated, so verify details with the employer.
