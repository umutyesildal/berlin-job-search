# Browser-driven job search recipes

> The user operates the browser. Portal URLs and filters change; verify visible filters and stop at login, CAPTCHA, 2FA or access denial.

<!-- linkedin-guest:start -->
## LinkedIn Jobs

Base URL: `https://www.linkedin.com/jobs/search/`.

| Parameter | Example / mapping | Confidence |
|---|---|---|
| `keywords`, `location` | `software engineer`; `Berlin, Germany` | Public job-page search fields. |
| `geoId` | Germany `101282230`; Berlin `103035651`; Munich `100495942`; Hamburg `101949806`; Frankfurt/Rhine-Main `90009714`; Cologne/Bonn `90009713` | Guest searches returned matching card locations; IDs represent regions/metros, not exact city boundaries. |
| `f_TPR` | `r86400`, `r604800`, `r1209600`, `r2592000` | Seconds for 1/7/14/30 days; seven-day returned cards, other values `(verify)` in the current UI. |
| `f_WT` | `1` on-site, `2` remote, `3` hybrid | `3` returned guest cards; verify UI labels. |
| `f_E` | `1` internship, `2` entry, `3` associate, `4` mid-senior, `5` director, `6` executive | Individual labels `(verify)` in current UI. |
| `f_JT` | `F` full-time, `P` part-time, `C` contract, `T` temporary, `I` internship | Individual labels `(verify)` in current UI. |
| `f_AL` | `true` | Combined guest request returned cards; logged-in UI behavior `(verify)`. |
| `sortBy` | `DD` date sort | Accepted by guest request; UI ordering `(verify)`. |
| `start` | Guest: `0`, `10`, `20`; web UI: 25-card pages `(verify)` | Guest offset is zero-based, 10 cards/page. |

Example: `https://www.linkedin.com/jobs/search/?keywords=data%20analyst&location=Berlin%2C%20Germany&geoId=103035651&f_TPR=r604800&sortBy=DD`. Use a logged-in browser only if the user chooses to sign in. Guest search is low-volume and subject to the [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement); never use internal/Voyager APIs, cookies or login-session scraping. Search-card/detail parameters are in [API sources](api-sources.md).
<!-- linkedin-guest:end -->
<!-- linkedin-links:start -->
<!--
## LinkedIn Jobs

Give the user a ready-made search link to open themselves, for example:
[Data analyst jobs in Berlin](https://www.linkedin.com/jobs/search/?keywords=data%20analyst&location=Berlin%2C%20Germany).
They can adjust filters and review postings directly on LinkedIn.
-->
<!-- linkedin-links:end -->

## XING, StepStone, Indeed and BA

- **XING Jobs:** open [XING Jobs](https://www.xing.com/jobs); filter title, city, workplace and salary in the UI. A live `keywords=software engineer&location=Berlin` URL redirected to `/jobs/search/ki` and retained those values. Radius/filter keys `(verify)`; see [XING's search guide](https://help.xing.com/hc/en-us/articles/31499161470237-Find-your-new-job-with-XING).
- **StepStone:** search at [StepStone](https://www.stepstone.de/). Common path: `/jobs/{role}/in-{city}`. `/jobs/software-engineer/in-berlin?radius=25` is illustrative, not guaranteed. Radius/freshness URL keys `(verify)`; confirm live filter chips.
- **Indeed Germany:** base `https://de.indeed.com/jobs/`; `q` query, `l` location, `radius` km, `fromage` days. Example: `https://de.indeed.com/jobs?q=Pflegefachkraft&l=Hamburg&radius=25&fromage=7`. Automated check returned HTTP 403; these keys are widely used but current behavior `(verify)` in a human browser; redirects may discard filters.
- **BA Jobsuche:** open [BA Jobsuche](https://www.arbeitsagentur.de/jobsuche/) and set title/city/date/radius/job type in the UI. Common path: `/jobsuche/suche?was=...&wo=...`; other stable browser parameter names `(verify)`. The script API is in [API sources](api-sources.md).

## Hiring.cafe

`hiring.cafe` redirects to [hiringcafe.com](https://hiringcafe.com/). On 2026-10-23, curl and Playwright Chromium reached a Cloudflare challenge (HTTP 403, title “Just a moment…”); no search UI or job API request was observed. The legacy `GET /api/search-jobs` returned 401, `POST /api/search-jobs` 405, and `/api/search-jobs/get-total-count` 404. No HTTP scraper or old Elasticsearch body is used.

If the user can open the site, enter a role and city, inspect visible filters/cards, and confirm each selected opening on the employer ATS. Current filters, field names and `data-testid` selectors are `(verify)`. The following in-page example extracts a small set of visible cards only:

```js
(() => {
  const text = (node) => node?.textContent?.replace(/\s+/g, " ").trim() || null;
  return [...document.querySelectorAll('a[href*="/job"], a[href*="/jobs"]')]
    .map((link) => {
      const card = link.closest('article, li, [data-job-id], [class*="job-card"]') || link.parentElement;
      const field = (name) => card?.querySelector(`[data-testid="${name}"]`);
      return {
        title: text(field("job-title")) || text(link),
        company: text(field("company")), location: text(field("location")),
        salary: text(field("salary")), seniority: text(field("seniority")),
        language: text(field("language")),
        url: new URL(link.getAttribute("href"), location.href).href
      };
    }).filter((job) => job.title && job.url);
})();
```

Do not use whole-page `get_page_text`; if an in-page JSON fetch appears after a human search, confirm its response shape and keep it in that page context. API details remain `(verify)`.

## Extraction and approval

- Extract only card fields: title, employer, location, date, remote/hybrid label and canonical URL. Prefer DOM attributes, JSON-LD or accessible text from one card.
<!-- linkedin-guest:start -->
- The LinkedIn public-card selectors are verified but may change; inspect one selected card and revise selectors rather than dumping the page.
- LinkedIn guest-card selector example (adapt only after checking the live DOM):

```js
(() => {
  const text = (node) => node?.textContent?.replace(/\s+/g, " ").trim() || null;
  return [...document.querySelectorAll("li.base-search-card")].slice(0, 25).map((card) => {
    const link = card.querySelector("a.base-card__full-link");
    return {
      title: text(card.querySelector("h3.base-search-card__title")),
      company: text(card.querySelector("h4.base-search-card__subtitle")),
      location: text(card.querySelector(".job-search-card__location")),
      date: card.querySelector("time")?.getAttribute("datetime") || null,
      url: link ? new URL(link.getAttribute("href"), location.href).href.split("?")[0] : null
    };
  }).filter((card) => card.title && card.url);
})();
```
<!-- linkedin-guest:end -->
- Keep result sets small: no more than three pages/query, 25 cards/page, and 2–4 seconds between page loads. Read details only for a user-selected shortlist.
- Confirm freshness, job state and application destination on the employer page. Ads can be stale or personalized.
- Never bypass challenges, evade blocks, bulk-message, or click Apply/Submit/Send. Saving a job, setting an alert, changing a profile or sharing a CV requires per-action user approval.

For each shortlisted role, record exact title/employer, city and work model, posting age, explicit German requirement or “not stated”, salary/pay period or “not stated”, contract if visible, canonical URL and next step. If a card omits a field, record `unknown`; don't infer it from employer reputation.
