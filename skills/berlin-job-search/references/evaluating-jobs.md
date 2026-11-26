# Evaluating jobs in Germany

> Portions adapted from career-ops (MIT): the A–F report structure, STAR+R and the guardrails. See NOTICE.md.

Two modes:
- **Quick score**: for ranking a shortlist from `search.py`. One line per job.
- **Full evaluation**: for one posting the user is seriously considering. Blocks A–F, saved as a report.

Before either mode, read `job-search/profile.yml` and `job-search/cv.md` if they exist. Without a
CV, score only the dimensions you can judge and mark the rest `n/a`. Never fill gaps with
assumptions about the user.

Run `scripts/analyze_jd.py` (or `job_detail.py`, which includes it) on the full posting text
first. Its output feeds the language, salary, visa, agency and contract checks below. Treat its
output as evidence to check, not truth: read the quoted `german_evidence` yourself.

---

## Hard gates (check first)

If any gate applies, the final score is **capped at 2.0** and the decision is **Skip**. Name the
gate and say what would change it.

| Gate | Condition |
|---|---|
| Dealbreaker | The posting hits anything in `profile.dealbreakers` (industry, contract type, on-site days, travel, shift work...) |
| Language | German is explicitly required at **C1/C2, "verhandlungssicher" or "fließend"**, and the user is **B1 or lower** |
| Regulated profession | The role needs a German licence or recognition the user lacks (doctor *Approbation*, nurse *Berufsanerkennung*, teacher, lawyer, pharmacist, engineer title *Ingenieur* in some states, tax adviser...) and no recognition path is in progress |
| Security clearance | Requires *Sicherheitsüberprüfung* / German citizenship and the user doesn't qualify |
| Dead posting | The link is closed/404, or the posting is older than ~60 days with no sign of activity |
| Work authorization | User is non-EU, the role is clearly unable to sponsor (for example "only candidates with existing work permit" / "keine Visa-Unterstützung"), and the user has no permit that covers it |

Do not gate on missing salary, missing *(m/w/d)*, or a German-language ad by itself. Score those.

---

## Quick score (1.0–5.0)

Score each dimension 1–5, multiply by its weight, sum, and round to one decimal.

| Dimension | Weight | 5 | 3 | 1 |
|---|---|---|---|---|
| **Role fit**: title, core tasks and must-have skills vs target roles and CV | 30% | Target role. Most must-haves are evidenced in the CV | Adjacent role or about half the must-haves | Different role family or few must-haves |
| **Level fit**: seniority asked vs the user's level | 15% | Matches | One level off (stretch up or slight step down) | Two+ levels off |
| **Language feasibility**: `german_required` vs the user's German level | 15% | `not_required`, or German required and the user meets it | `nice_to_have` with low German, or `unknown` on an English ad (→ 4) | `required` (incl. `inferred`) and the user is ≥1 level below what's asked |
| **Location / remote**: city, radius, on-site days vs preference | 10% | Fits exactly | Commutable, or hybrid with more office days than preferred | Wrong city with no relocation, or on-site when the user needs remote |
| **Compensation**: stated salary vs `min_salary_gross_annual` | 15% | Stated and ≥ floor | **Not stated (default 3)**, or 0–10% below floor | >10% below floor |
| **Work authorization** (non-EU only; EU/EEA/CH users get 5) | 10% | Has a permit covering it, or stated salary ≥ applicable Blue Card threshold, or the employer visibly sponsors | Unknown (no salary, no statement) | Salary below every applicable threshold, or "no sponsorship" |
| **Posting quality**: direct employer, freshness, clarity | 5% | Direct employer, < 14 days, clear tasks | Agency/Zeitarbeit (disclosed employer) or 14–45 days old | Undisclosed client, vague, or reposted for months |

Notes on dimensions:
- **Language scoring detail.** Map the requirement to CEFR: "Grundkenntnisse" ≈ A2, "gute
  Kenntnisse" ≈ B1–B2, "sehr gute / fließend" ≈ C1, "verhandlungssicher / muttersprachlich" ≈
  C1–C2. A German ad with no stated requirement (`inferred: true`) ≈ B2. One level short → 2,
  two or more → 1 (and check the language gate).
- **Blue Card and age 45+ thresholds:** Use the applicable, officially effective figure in the
  [dated threshold table](thresholds.md). The lower Blue Card threshold applies to eligible
  shortage occupations and recent graduates; see [visa and work permits](visa-and-work-permits.md).
- **Compensation context.** Count a 13th salary and fixed bonuses if stated. Don't penalize a
  missing salary below 3: most German postings omit it.

**Decision bands**

| Score | Decision | Meaning |
|---|---|---|
| ≥ 4.0 | **Apply** | Strong fit. Prioritise this week |
| 3.5–3.9 | **Consider** | Good fit with one clear gap. Apply if the gap can be addressed in the application |
| 3.0–3.4 | **Research first** | Check the unknowns (salary, language, sponsorship) before investing time |
| < 3.0 | **Skip** | Unless the user has a reason (dream company, referral) |

**Quick-score output** (one row per job, sorted by score):

| Score | Decision | Role @ Company | City / Remote | German | Salary | Why | Main gap | Link |
|---|---|---|---|---|---|---|---|---|

"Why" and "Main gap" are one short phrase each, grounded in the CV or the ad. Never write a generic phrase like "great opportunity".

---

## Full evaluation (blocks A–F)

Deliver every block. If a block cannot be filled (no CV, no data), say what is missing.

### A. Role summary
A table: title (as written), company, employer type (startup / scale-up / Mittelstand / corporate /
public sector / agency), city and on-site days, contract (*unbefristet*/*befristet*, full/part
time, Werkstudent...), seniority, team (if stated), ad language, and a one-sentence TL;DR.

### B. Match with the CV
- A table mapping each **must-have** and **nice-to-have** requirement to the exact CV line that
  evidences it, quoted. Mark it ✅ evidenced, ◐ partial/adjacent, or ❌ missing.
- **Gaps:** for each ❌/◐, say whether it is a hard blocker or a nice-to-have, which adjacent
  experience in the CV is closest, and a concrete mitigation (one sentence for the cover letter,
  a small project, a course/certificate, or "address it in the interview").
- Never claim the user built or led something the CV only shows them *using*.

### C. Germany check
- **Language:** requirement with quoted evidence, the user's level, and whether the gap is
  bridgeable (for example "B1 now, course to B2 in 6 months; say so in the cover letter").
- **Work authorization (non-EU):** which permit path fits (Blue Card regular/shortage, §18a/§18b
  skilled worker, §6 BeschV for IT without a degree, existing permit, Chancenkarte job search),
  the confirmed gross annual salary vs the applicable threshold, and whether employer approval or
  BA approval is needed. The analyzer reports its reference year; check official guidance if the
  relevant year differs. Use `visa-and-work-permits.md` and mark unconfirmed offer terms unknown.
- **Agency / Zeitarbeit:** who the actual employer is, *Arbeitnehmerüberlassung* implications,
  and whether a takeover (*Übernahme*) is mentioned.
- **Contract signals:** *befristet* and its length, *Probezeit*, vacation days (the legal
  minimum is 20 on a 5-day week; below 25 is below market for office jobs; 28–30 is common in
  larger companies and under collective agreements), 13th salary, *Tarifvertrag* /
  *AT*, bAV, VWL, home-office policy, relocation support, visa support.

### D. Compensation
- The posted salary (annualised, gross) if any.
- Market benchmarks for this role, city and level from `salary-and-compensation.md`, plus a web
  search if needed (Entgeltatlas, kununu, Levels.fyi, Glassdoor, StepStone Gehaltsreport). Cite
  each figure with its source and year. If nothing reliable is found, say so.
- A recommended **Gehaltsvorstellung** (annual gross range) and the one-sentence way to state it.

### E. Application plan
- Format: German *Lebenslauf* + *Anschreiben*, or English CV (+ cover letter?), and why.
- The top 5 CV changes for this posting (what to move up, reword with the ad's terms, or cut),
  only from facts in the CV.
- 15–20 keywords from the ad for ATS matching (keep the ad's language).
- Draft answers to the likely form questions: salary, earliest start date / notice period, work
  authorization, relocation.
- A referral or outreach angle if the user has a connection (draft from `assets/outreach-templates.md`).

### F. Interview prep
- 5–8 **STAR+R** stories (Situation, Task, Action, Result, **Reflection**: what was learned or
  what they'd do differently), each mapped to a requirement from the ad and built only from the
  CV or what the user told you. Ask for missing details rather than inventing them.
- 3–5 likely questions for this company and role, including the German classics where relevant
  (*Warum wir?*, *Stärken/Schwächen*, *Gehaltsvorstellung*, *Kündigungsfrist*, *Warum Deutschland?*).
- Red-flag questions the user might face (gaps, short tenures, visa) and an honest framing for each.
- 3 smart questions the user should ask them.

### Score and decision
Show the quick-score table for this job (all seven dimensions, their scores and the weighted
total), any hard gate, and the decision band. Then recommend one specific next step.

---

## Report file

Save the full evaluation to `job-search/reports/YYYY-MM-DD-<company-slug>-<role-slug>.md`:

```markdown
# Evaluation: {Company} — {Role}

**Date:** {YYYY-MM-DD} · **Score:** {X.X}/5 · **Decision:** {Apply | Consider | Research first | Skip}
**URL:** {posting URL} · **Source:** {ba | arbeitnow | ats | linkedin | stepstone | ...}

## A) Role summary
## B) Match with the CV
## C) Germany check
## D) Compensation
## E) Application plan
## F) Interview prep
## Score breakdown
```

Then add or update the tracker row:
`python3 "$SKILL_DIR/scripts/tracker.py" add --company "..." --title "..." --url "..." --score X.X --notes "report: reports/<file>.md"`
