# DataXAi React frontend

Eight interactive views for Chapter 4, Section 4.4.1. The default demonstration runs independently of Django, PostgreSQL, Redis, and Celery. The interface uses React, TypeScript, Vite, bundled Inter fonts, and Lucide icons.

## Run locally

Use Node.js 22.12 or later and npm. Run these commands in PowerShell.

```powershell
Set-Location D:\dataxai\frontend
npm.cmd ci
npm.cmd run dev
```

Open the local URL printed by Vite. Choose **Admin**, **Manager**, or **Seller**, then **Sign in**. Each role button fills in a fictional username and password. Sign out in the sidebar to switch roles. On this checkout the review preview was launched on port 5183.

## Explore the demonstration

| Role | What to try |
| --- | --- |
| Admin | Create an account, search the user directory, change a business assignment, or toggle the evidence reminder in System Configuration. The new account can sign in during the same browser session. |
| Seller | Submit the name of a CSV or XLSX file and inspect its status. Open the forecast details for historical and predicted weekly units, customer spending for 90-day estimates, or the explanation for a recorded repair. |
| Manager | Open an incoming submission, request processing, queue it, start the simulation, then complete it or simulate failure. Inspect quarantine evidence, choose one of the three decisions, and confirm or cancel. |

All three role presets use the fictional password `DataXAi-demo`. The Admin account has organisation scope. The primary Manager and Seller accounts share **Lahore Homewares**. Sign in as `bilal` or `noor` with the same password for an Indus Textiles Manager or Seller. Cedar Stationery has no seeded submissions. Reassigning a demo account takes effect on its next sign-in.

A tab holds one in-memory demonstration. Signing out preserves local workflow changes for the next role in that tab. Reloading the page resets the fixtures and the login. A demo session expires after 30 minutes. File selection reads the filename only. New submissions have an unknown row count. The processing controls advance a deterministic simulation clock and preserve receipt, execution, and completion as distinct states.

## Invoice and analytics

The Manager queue starts before a decision on `DEMO-INV-042`. The Seller explanation is an independent post-commit demonstration snapshot of the same invoice. The original line is three Ceramic Mugs at £40 each (£120). Order confirmation OC-1042 supports £4 per unit (£12). The historical difference is −£108. Both snapshots keep the original evidence. The direct weekly sales contribution remains three units. The 90-day spending prediction for this repair is marked **Not Yet Recomputed**.

The Seller dashboard predictions are separate, dated example outputs. Changing the optional Groq response on the explanation screen exercises loading, available, unavailable, and failed states while keeping the template and recorded facts visible.

## Django authentication mode

Copy `.env.example` to `.env.local` in **frontend**. Set `VITE_DATA_MODE=api` and set `DJANGO_ORIGIN` to a running local Django server, then restart Vite. The development and preview servers proxy same-origin `/api` requests. A separately hosted production build needs an equivalent same-origin route to Django. Vite reads the data mode at build time.

API mode accepts Django credentials, obtains a JWT pair, reads the authenticated identity, and renews the access token. The browser holds session material in memory. Reloading requires a new login. Current public routes supply identity and health. Missing management APIs and business assignments produce explicit restricted views. See the [integration inventory and gap register](docs/INTEGRATION.md).

## Verification and Chapter 4 figures

- [Eight figures and reproduction instructions](docs/FIGURES.md)
- [Verification evidence and changed-file inventory](docs/VERIFICATION.md)

```powershell
npm.cmd run build
npm.cmd run format:check
npm.cmd test
```

Playwright uses locally installed Chrome at its standard Windows location when available. Otherwise install its Chromium browser with `npx.cmd playwright install chromium`. Set `PLAYWRIGHT_CHANNEL` to select another installed supported channel. The default suite runs on loopback ports 4187 and 4188 and uses isolated browser contexts. Enable the optional live Django test and figure export as described in the verification report.
