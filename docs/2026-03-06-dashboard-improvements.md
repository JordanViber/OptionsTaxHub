# Dashboard Improvement Review — 2026-03-06

Date: March 6, 2026

This document records the latest dashboard review so it is clearly separated from earlier improvement notes.

## What was observed

- The current localhost experience can still overwhelm the user with warnings before the user understands what matters.
- The selected tax year is not yet driving enough of the analysis scope. A 2025 analysis can still surface wash-sale events from 2023 and 2024.
- Warning language still includes brokerage shorthand such as `OASGN` and `OCA`, which is not understandable for many users.
- Repeated event-level warnings create clutter instead of helping the user decide what to do next.
- The product still needs to do a better job separating:
  - actionable tax issues
  - data quality limitations
  - unsupported brokerage events

## Product goals

The dashboard should answer these questions quickly:

1. What matters for my selected tax year?
2. What can I act on right now?
3. Which numbers are reliable, and which ones need manual verification?
4. What details are hidden unless I ask for them?

## Improvement plan

### 1. Scope the analysis to the selected tax year

- Filter historical wash-sale results so the main warning panel only shows sale events affecting the selected tax year.
- Preserve valid cross-year wash-sale detection when a sale happens late in the selected year and the repurchase occurs in the following year.
- Keep realized summaries aligned with the same selected tax year.

### 2. Reduce warning clutter

- Consolidate repeated option assignment warnings into one message per symbol.
- Consolidate repeated corporate-action and stock-split warnings into one message per symbol.
- Group CSV fallback-price warnings into a smaller set of messages.
- Keep unmatched-sell warnings consolidated and visible because they materially affect accuracy.
- Hide or suppress zero-dollar wash-sale events.

### 3. Replace broker jargon with plain English

- `OASGN` → option assignment
- `OCA` → corporate action
- `SPR` → stock split

Each warning should say what happened and why the user should care.

### 4. Make the dashboard more action-first

- Emphasize tax-year-specific wash-sale issues and top suggestions above raw notes.
- Label general warnings as data quality notes instead of generic parsing notes.
- Add clearer copy that explains the wash-sale panel is scoped to the selected tax year.

### 5. Improve trust and freshness

- Make it more obvious when the user is seeing a fresh upload versus a restored/saved analysis.
- Revisit development caching behavior if stale UI continues appearing during local testing.

## Completed in code on 2026-03-06

- ✅ Tax-year-scoped wash-sale output now focuses on sale events from the selected tax year.
- ✅ Repeated option-assignment, corporate-action, stock-split, and fallback-price warnings are now summarized into fewer plain-English notes.
- ✅ Zero-dollar / sub-cent wash-sale noise is suppressed from the warning UI.
- ✅ Dashboard copy now labels warning output as data quality notes.
- ✅ Wash-sale messaging now explicitly says it is scoped to the selected tax year.
- ✅ The dashboard now shows whether the current result came from a fresh upload, saved history, or restored browser session.
- ✅ The dashboard now shows a confidence banner that explains whether the result is high confidence, moderate confidence, or partial confidence.
- ✅ Local development now avoids re-registering the service worker, which reduces stale cached dashboard results during localhost testing.
- ✅ The dashboard now shows a recommended next-steps panel so actionable items appear before lower-priority detail.

## Implementation status started on 2026-03-06

Started in this round:

- tax-year-scoped wash-sale detection ✅
- warning consolidation and plain-English warning summaries ✅
- clearer dashboard copy for data quality notes ✅
- clearer wash-sale panel wording tied to the selected tax year ✅
- fresh-upload vs saved-analysis indicator ✅
- confidence scoring / analysis quality banner ✅
- stronger prioritization of action items over low-value notes ✅

Planned next if needed:

- deeper action-first ranking inside the suggestions view
