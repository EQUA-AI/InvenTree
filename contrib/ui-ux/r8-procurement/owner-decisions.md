# R8 procurement: owner decisions (22 October 2026)

The owner reopened R8 after the R6 UI wrap-up and settled the build-shaping decisions from the gap analysis:

| Decision | Choice |
|---|---|
| Scenario | Cedar Creek Pump 08 / WO-003826 (the O23 showcase), with historical telemetry and an honest shortage until real spare details arrive. R8's isolated Pump 02 case is not used. |
| Purchase-order authority | A named approver approves each PO in the app; the AI prepares everything. No bounded service identity, no approval-free placement. |
| Supplier email | Recorded evidence only: the exact PO PDF and email are generated and stored; nothing is sent. |
| Environment | Build and seed only in the local database copy first. Azure migrations, deployment and seeding need a separate explicit approval later. |

Still open (not blocking the first build steps): Job Kit activation details for the target environment and authorized actors (local activation is fine for the build), and the video (representative versus real internal execution, recorder, 150/90/45-second exports).
