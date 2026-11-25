# Chapter 4, Section 4.4.1 interface figures

All eight PNGs are 1440 × 900 viewport captures at device scale factor 1. Open a fresh page in demonstration mode for each figure. Select the role preset and press **Sign in**. The initial data, scenario clock and example estimates are deterministic. Figure numbering follows the supplied handoff.

| Figure | Screen | Reproduce the captured state |
| --- | --- | --- |
| [4.1](figures/figure-4.1-shared-login.png) | Shared Login | Fresh page, blank credentials. |
| [4.2](figures/figure-4.2-admin-dashboard.png) | Admin Dashboard | Sign in as Admin. |
| [4.3](figures/figure-4.3-create-account.png) | Admin Create Account | As Admin, open Create Account and leave the form empty. |
| [4.4](figures/figure-4.4-manager-dashboard.png) | Manager Dashboard | Sign in as Manager. |
| [4.5](figures/figure-4.5-seller-dashboard.png) | Seller Dashboard | Sign in as Seller. The unit forecast and 90-day spending estimate belong to Lahore Homewares. |
| [4.6](figures/figure-4.6-batch-processing.png) | Run Batch Processing, UC-04 | As Manager, open Batch Processing with SUB-1048 selected. Press Request processing, Queue execution, then Start simulation. Leave EX-1048 running. |
| [4.7](figures/figure-4.7-quarantine-review.png) | Resolve Quarantined Records, UC-05 | As Manager, open Quarantine Review. Keep DEMO-INV-042 selected in its initial Awaiting Review state. |
| [4.8](figures/figure-4.8-repair-explanations.png) | View Repair Explanations, UC-10 | As Seller, open Repair Explanations. Leave Demo response on Available and wait for the simulated narrative. |

Figure 4.7 presents the proposed correction. Figure 4.8 uses a separately seeded post-authorisation snapshot. Both retain the £120 source line and show the same £12 evidence-supported line. The Seller can select **Unavailable** or **Failed** to examine the fallback while retaining the repair facts and template.

Run the capture suite from the frontend directory to regenerate the files.

```powershell
$env:CAPTURE_FIGURES = '1'
npm.cmd test -- tests/figures.spec.ts
Remove-Item Env:CAPTURE_FIGURES
```

The capture suite checks viewport dimensions, essential text and actions, table bounds, card overlap and the locally bundled font. It writes each file only after the corresponding figure checks pass.
