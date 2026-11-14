# Design and verification

Luma 0.2 keeps the original true-black-flat direction (seed 242473852): neutral black, quiet text, one mint data hue, no gradient or glass. Segoe UI Variable and Microsoft YaHei UI are offline system choices.

The relationship is: selected conversation → estimated work + expected finish → current deliverable and latest execution note → reported evidence. Completion counts appear with the checklist; workload weights live in row tooltips rather than competing with dynamic state.

Four layouts use their own information composition. The focus view keeps a previous completed deliverable, the current deliverable and the next pending one. The full view keeps every checklist row and recent distinct events. Mini retains the current deliverable without the full list; ribbon retains only the core estimate and finish time.

The solid track shows estimated work. The translucent interval shows uncertainty. The moving trace is clipped to the already estimated length and adds no percentage. Numeric glyphs enter and leave over 390 ms; the track changes over 760 ms. Changes can move backward after rework. Elapsed seconds animate locally without increasing progress. OS reduced motion and the local toggle disable decorative motion.

The desktop bridge handles DPI conversion, size selection and window pinning. Header buttons are excluded from drag regions. Browser previews make no desktop changes. Settings are a scrollable sheet, with focus containment and inert background controls.

## Verification record

- Python and Node behavioral checks cover evidence-weighted units, legitimate large corrections, incomplete terminal states, running-tool forecast causality, API consent, reference validation and encrypted Windows key storage.
- The browser UI was reviewed across ribbon, mini, focus and full compositions, including settings, estimate details, conversation selection and reduced motion.
- Reduced motion was checked against computed activity animation: none. No horizontal overflow was observed.
- Public images come exclusively from the synthetic --demo source and visibly carry the demo label; benchmark figures are never invented for this source.
- Native and browser checks are documented accurately in release notes; browser-only checks do not prove native pointer behavior.
- The actual Windows WebView2 window was launched outside the sandbox desktop and visually checked. Coordinate input opened the native size menu and selected the focus layout; its width and content reflow changed correctly. UIA element clicks lacked geometry, so they were not counted as passing input tests.
- Source style lint completed with zero failures. Remaining warnings cover actual floating overlays, clock zero padding and missing-value dashes.

The 0.3 review opens from the ETA in every layout. The actual finish leads the completed view. Two aligned intervals place the first and last estimates on the same clock, with the error and issue time beneath each estimate. The AI section links each change to its advice and compares time estimates once an outcome is available.

The Windows panel was verified with an actual live AI assessment and a separate synthetic completed turn. The review screenshot contains the synthetic example.
