# Privacy policy

Last updated: 28 October 2026

Berlin Job Search is an open-source skill, a set of local scripts, and a connection to the Daily
Berlin Jobs server. Both are published by Umut Yesildal in Berlin, Germany. There are no user
accounts, ads, or tracking cookies.

## Data and purpose

You may share a CV, profile, job preferences, or application details with the AI assistant you use
(such as ChatGPT, Claude, or Codex) to search for jobs, assess postings, draft documents, or track
your search. That provider processes the information under its own privacy policy. When the scripts
run locally, personal files are stored on your machine in `job-search/`.

**Daily Berlin Jobs server.** To search Berlin tech jobs, the assistant calls the Daily Berlin Jobs
MCP server (`https://daily-berlin-jobs.vercel.app/api/mcp`), which the publisher runs. It receives
the search terms and filters the assistant sends (for example "backend", a seniority level or a work
mode) and job IDs. It does not receive your CV, profile, name, or conversation. It needs no key and
stores no profile. The server is hosted on Vercel, which processes technical request data such as
IP address, user agent, requested URL and time to deliver and secure the service. Details are in the
[Daily Berlin Jobs privacy policy](https://daily-berlin-jobs.vercel.app/privacy).

**Other job sources.** The local scripts send search terms, city, filters, and job URLs to the
relevant public job sources: Bundesagentur für Arbeit, Arbeitnow, Daily Berlin Jobs, and company
career boards such as Greenhouse, Lever, Ashby, Personio, SmartRecruiters, Recruitee, and Workable.
They do not send your CV or profile to those sources.

## Recipients and retention

- **AI providers** process your conversation under their own policies.
- **Vercel Inc.** (USA, certified under the EU–US Data Privacy Framework) hosts the Daily Berlin
  Jobs server and keeps request logs for a limited period under its own policy. The publisher does
  not combine them with other data or use them to identify people.
- **Job sources** process the requests the scripts send them under their own policies.
- **Local files** remain on your machine until you delete them.

## Legal basis and your rights

Processing of request data by the Daily Berlin Jobs server is based on legitimate interest
(Art. 6(1)(f) GDPR) in running a secure, working service. You have the right to access,
rectification, erasure, restriction of processing and objection, and the right to complain to a
data protection authority. In Berlin that is the
[Berliner Beauftragte für Datenschutz und Informationsfreiheit](https://www.datenschutz-berlin.de/).

## Your controls

Delete `job-search/` to remove local files, and uninstall the plugin to stop using it and the Daily
Berlin Jobs connection. For questions or requests, open an issue at
<https://github.com/umutyesildal/berlin-job-search/issues>.

Berlin Job Search does not ask for passwords, government ID numbers, or bank details.

## Changes

If this policy changes, the date at the top changes too.
