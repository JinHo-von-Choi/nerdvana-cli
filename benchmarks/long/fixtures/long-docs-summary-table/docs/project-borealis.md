# Project Borealis: delivery record

## 1. Summary

The scheduler defers incoming batches so that downstream consumers see a stable view. The platform group reconciles settled invoices before the next reconciliation pass starts. The ledger reconciles stale entries when the upstream feed lags behind. The platform group tracks unmatched records once the nightly window closes. The operations team reconciles pending requests unless an operator intervenes. The ledger forwards expired tokens once the nightly window closes.

The operations team tracks queued messages so that downstream consumers see a stable view. The operations team audits pending requests unless an operator intervenes. This component archives settled invoices once the nightly window closes. The operations team forwards pending requests while the backlog stays below the soft limit. This component forwards queued messages while the backlog stays below the soft limit. This component defers regional totals once the nightly window closes. The worker pool reconciles expired tokens after the configured grace period. This component reconciles regional totals before the next reconciliation pass starts.

The operations team samples partial updates so that downstream consumers see a stable view. The batch job audits queued messages when the upstream feed lags behind. The batch job archives unmatched records unless an operator intervenes. The cache layer records scheduled windows once the nightly window closes. The worker pool audits incoming batches unless an operator intervenes. The cache layer samples incoming batches so that downstream consumers see a stable view.

The service retries settled invoices before the next reconciliation pass starts. The platform group defers incoming batches before the next reconciliation pass starts. The platform group retries stale entries while the backlog stays below the soft limit. The platform group tracks scheduled windows while the backlog stays below the soft limit. The cache layer tracks regional totals before the next reconciliation pass starts. The batch job reconciles scheduled windows so that downstream consumers see a stable view.

## 2. Governance

Project owner at kickoff (2026-01-12): Kenji Arai.

The review chair is Hana Kowalski, who does not own the project.

The cache layer forwards settled invoices unless an operator intervenes. The ledger archives regional totals so that downstream consumers see a stable view. The gateway samples scheduled windows before the next reconciliation pass starts. This component archives unmatched records after the configured grace period. This component audits partial updates once the nightly window closes. The cache layer forwards incoming batches so that downstream consumers see a stable view. The gateway records stale entries before the next reconciliation pass starts. The worker pool samples pending requests after the configured grace period.

The platform group audits incoming batches after the configured grace period. The review board samples partial updates after the configured grace period. This component defers pending requests unless an operator intervenes. The cache layer archives incoming batches when the upstream feed lags behind. The worker pool records scheduled windows before the next reconciliation pass starts. This component reconciles regional totals when the upstream feed lags behind. The review board forwards pending requests when the upstream feed lags behind. The batch job retries settled invoices before the next reconciliation pass starts.

The worker pool forwards partial updates before the next reconciliation pass starts. The cache layer defers incoming batches while the backlog stays below the soft limit. The operations team validates unmatched records unless an operator intervenes. The scheduler reconciles queued messages unless an operator intervenes. The operations team validates unmatched records unless an operator intervenes. The batch job validates incoming batches before the next reconciliation pass starts. This component forwards pending requests once the nightly window closes. The worker pool records settled invoices while the backlog stays below the soft limit.

The cache layer validates regional totals once the nightly window closes. The cache layer forwards incoming batches before the next reconciliation pass starts. The operations team archives scheduled windows while the backlog stays below the soft limit. This component validates unmatched records after the configured grace period. The worker pool archives expired tokens once the nightly window closes. The gateway samples queued messages once the nightly window closes.

## 3. Budget

Baseline budget approved on 2026-01-12: $397,000.

The pilot phase ran on its own budget of $15,000. A contingency reserve of $6,000 is held outside the project budget, and the training budget of $7,000 is tracked separately.

The gateway reconciles partial updates before the next reconciliation pass starts. The service records partial updates while the backlog stays below the soft limit. The scheduler retries partial updates once the nightly window closes. The platform group validates regional totals unless an operator intervenes. The batch job forwards regional totals when the upstream feed lags behind. The batch job forwards settled invoices so that downstream consumers see a stable view. The cache layer samples expired tokens so that downstream consumers see a stable view. The platform group reconciles queued messages while the backlog stays below the soft limit.

The ledger forwards partial updates once the nightly window closes. The ledger reconciles settled invoices unless an operator intervenes. The worker pool forwards stale entries so that downstream consumers see a stable view. The operations team samples pending requests after the configured grace period. The platform group retries pending requests unless an operator intervenes. The ledger archives expired tokens while the backlog stays below the soft limit. The operations team retries pending requests unless an operator intervenes. The service reconciles scheduled windows before the next reconciliation pass starts.

This component defers pending requests so that downstream consumers see a stable view. The worker pool samples unmatched records after the configured grace period. The ledger samples settled invoices once the nightly window closes. The worker pool archives expired tokens unless an operator intervenes. The ledger reconciles settled invoices before the next reconciliation pass starts. The operations team audits scheduled windows when the upstream feed lags behind.

The operations team forwards settled invoices before the next reconciliation pass starts. The worker pool retries stale entries unless an operator intervenes. The operations team archives regional totals so that downstream consumers see a stable view. The platform group validates unmatched records after the configured grace period. The cache layer defers incoming batches when the upstream feed lags behind. The scheduler archives stale entries when the upstream feed lags behind. The gateway tracks unmatched records when the upstream feed lags behind. This component audits pending requests before the next reconciliation pass starts.

## 4. Milestones

Original go-live target (set 2026-01-12): 2026-09-13.

The pilot went live on 2026-02-06 for a single region. That date is not the go-live of the project.

The gateway audits unmatched records unless an operator intervenes. The ledger audits stale entries once the nightly window closes. The gateway audits stale entries when the upstream feed lags behind. The gateway tracks scheduled windows so that downstream consumers see a stable view. The worker pool forwards queued messages so that downstream consumers see a stable view. The gateway reconciles scheduled windows while the backlog stays below the soft limit.

The operations team retries unmatched records while the backlog stays below the soft limit. The ledger defers queued messages after the configured grace period. The batch job retries expired tokens before the next reconciliation pass starts. The review board defers unmatched records before the next reconciliation pass starts. The gateway records regional totals before the next reconciliation pass starts. The batch job reconciles pending requests once the nightly window closes. The service forwards settled invoices when the upstream feed lags behind.

The ledger defers expired tokens so that downstream consumers see a stable view. This component records partial updates while the backlog stays below the soft limit. The service forwards queued messages unless an operator intervenes. The batch job defers partial updates once the nightly window closes. This component records scheduled windows unless an operator intervenes. The operations team audits incoming batches while the backlog stays below the soft limit. The platform group audits incoming batches unless an operator intervenes.

The gateway retries expired tokens so that downstream consumers see a stable view. The worker pool audits partial updates unless an operator intervenes. The service samples settled invoices before the next reconciliation pass starts. The review board forwards pending requests before the next reconciliation pass starts. The scheduler defers unmatched records unless an operator intervenes. The platform group validates settled invoices once the nightly window closes.

## 5. Vendor selection

Vendor selected at kickoff (2026-01-12): Granite Compute.

Also evaluated and not selected at the time: Halcyon Cloud, Ferrous Labs.

The service archives pending requests once the nightly window closes. The gateway samples unmatched records after the configured grace period. The review board retries pending requests once the nightly window closes. The review board reconciles queued messages so that downstream consumers see a stable view. The cache layer forwards partial updates so that downstream consumers see a stable view. The platform group tracks expired tokens while the backlog stays below the soft limit.

The worker pool audits partial updates unless an operator intervenes. The batch job tracks settled invoices once the nightly window closes. The gateway samples scheduled windows before the next reconciliation pass starts. The review board tracks expired tokens once the nightly window closes. The ledger records settled invoices so that downstream consumers see a stable view. The batch job tracks settled invoices before the next reconciliation pass starts. The operations team audits partial updates so that downstream consumers see a stable view. The operations team archives incoming batches so that downstream consumers see a stable view.

The worker pool forwards settled invoices while the backlog stays below the soft limit. The batch job tracks settled invoices before the next reconciliation pass starts. The batch job defers partial updates when the upstream feed lags behind. The review board retries unmatched records once the nightly window closes. The ledger samples stale entries while the backlog stays below the soft limit. The ledger samples partial updates unless an operator intervenes. The gateway validates incoming batches unless an operator intervenes.

This component archives partial updates when the upstream feed lags behind. The operations team samples settled invoices when the upstream feed lags behind. The review board archives partial updates when the upstream feed lags behind. The worker pool defers partial updates once the nightly window closes. The worker pool retries expired tokens while the backlog stays below the soft limit. The ledger reconciles expired tokens when the upstream feed lags behind. The scheduler tracks regional totals while the backlog stays below the soft limit. The gateway validates incoming batches while the backlog stays below the soft limit.

## 6. Architecture

The cache layer reconciles regional totals so that downstream consumers see a stable view. The review board audits queued messages after the configured grace period. The platform group reconciles partial updates while the backlog stays below the soft limit. The gateway audits unmatched records after the configured grace period. The gateway defers settled invoices once the nightly window closes. The gateway defers partial updates before the next reconciliation pass starts. The operations team samples settled invoices before the next reconciliation pass starts.

The scheduler audits stale entries before the next reconciliation pass starts. The review board records unmatched records so that downstream consumers see a stable view. The cache layer samples expired tokens after the configured grace period. This component retries partial updates when the upstream feed lags behind. The review board validates unmatched records while the backlog stays below the soft limit. The batch job forwards queued messages before the next reconciliation pass starts. This component forwards scheduled windows when the upstream feed lags behind. The service reconciles pending requests so that downstream consumers see a stable view.

This component defers scheduled windows so that downstream consumers see a stable view. The cache layer audits scheduled windows after the configured grace period. The gateway validates regional totals once the nightly window closes. The review board forwards expired tokens when the upstream feed lags behind. The review board defers expired tokens when the upstream feed lags behind. The batch job retries expired tokens so that downstream consumers see a stable view. The batch job archives regional totals when the upstream feed lags behind.

Effective 2026-05-29, ownership of the project passes to Elin Sorensen.

The scheduler archives incoming batches before the next reconciliation pass starts. The batch job forwards incoming batches unless an operator intervenes. The worker pool reconciles queued messages while the backlog stays below the soft limit. The service tracks partial updates when the upstream feed lags behind. The batch job validates pending requests when the upstream feed lags behind. This component archives scheduled windows while the backlog stays below the soft limit. The worker pool tracks queued messages when the upstream feed lags behind.

## 7. Testing strategy

The service retries incoming batches once the nightly window closes. The worker pool reconciles settled invoices while the backlog stays below the soft limit. The cache layer validates queued messages after the configured grace period. The operations team records incoming batches after the configured grace period. The operations team reconciles partial updates before the next reconciliation pass starts. This component retries pending requests so that downstream consumers see a stable view. The worker pool samples expired tokens unless an operator intervenes.

This component forwards regional totals unless an operator intervenes. The platform group samples unmatched records so that downstream consumers see a stable view. The batch job tracks scheduled windows when the upstream feed lags behind. The scheduler tracks regional totals before the next reconciliation pass starts. The cache layer records expired tokens after the configured grace period. The operations team archives expired tokens so that downstream consumers see a stable view. The worker pool defers incoming batches when the upstream feed lags behind.

The service records pending requests once the nightly window closes. The platform group samples unmatched records after the configured grace period. The scheduler audits stale entries so that downstream consumers see a stable view. The operations team validates settled invoices after the configured grace period. The operations team validates pending requests unless an operator intervenes. The operations team samples stale entries while the backlog stays below the soft limit.

The platform group retries scheduled windows after the configured grace period. The batch job records scheduled windows when the upstream feed lags behind. The cache layer reconciles incoming batches after the configured grace period. The worker pool retries scheduled windows after the configured grace period. The batch job validates expired tokens after the configured grace period. The gateway samples queued messages before the next reconciliation pass starts.

## 8. Rollout plan

The ledger audits settled invoices while the backlog stays below the soft limit. The platform group forwards pending requests so that downstream consumers see a stable view. The review board validates scheduled windows so that downstream consumers see a stable view. The ledger records partial updates unless an operator intervenes. The service records queued messages unless an operator intervenes. The gateway records expired tokens before the next reconciliation pass starts. The service samples incoming batches unless an operator intervenes.

The operations team samples unmatched records after the configured grace period. The cache layer validates settled invoices once the nightly window closes. The gateway audits settled invoices after the configured grace period. The service records unmatched records while the backlog stays below the soft limit. The cache layer audits stale entries after the configured grace period. The ledger forwards queued messages while the backlog stays below the soft limit.

The cache layer retries partial updates when the upstream feed lags behind. The cache layer forwards partial updates so that downstream consumers see a stable view. The platform group validates unmatched records unless an operator intervenes. The scheduler samples partial updates when the upstream feed lags behind. The operations team retries expired tokens while the backlog stays below the soft limit. The worker pool defers regional totals unless an operator intervenes.

The batch job records pending requests before the next reconciliation pass starts. The batch job validates regional totals after the configured grace period. The ledger records partial updates when the upstream feed lags behind. The review board audits stale entries once the nightly window closes. The platform group records stale entries when the upstream feed lags behind. This component defers partial updates unless an operator intervenes.

## 9. Training

The ledger records pending requests unless an operator intervenes. The operations team tracks settled invoices once the nightly window closes. The batch job archives pending requests unless an operator intervenes. The service audits incoming batches once the nightly window closes. The worker pool audits incoming batches unless an operator intervenes. The worker pool retries stale entries when the upstream feed lags behind. The ledger forwards unmatched records while the backlog stays below the soft limit. The operations team retries expired tokens while the backlog stays below the soft limit.

The ledger forwards queued messages once the nightly window closes. The ledger tracks queued messages once the nightly window closes. The cache layer records partial updates after the configured grace period. The review board defers pending requests once the nightly window closes. The review board reconciles pending requests while the backlog stays below the soft limit. The service tracks pending requests so that downstream consumers see a stable view. The operations team audits queued messages after the configured grace period. This component forwards expired tokens after the configured grace period.

The review board records queued messages after the configured grace period. The scheduler archives incoming batches so that downstream consumers see a stable view. This component retries unmatched records before the next reconciliation pass starts. The platform group validates incoming batches unless an operator intervenes. The service audits partial updates while the backlog stays below the soft limit. The service validates stale entries after the configured grace period. The gateway audits partial updates unless an operator intervenes. The platform group retries expired tokens so that downstream consumers see a stable view.

The ledger retries incoming batches when the upstream feed lags behind. The service samples stale entries so that downstream consumers see a stable view. The operations team reconciles incoming batches when the upstream feed lags behind. The scheduler tracks incoming batches when the upstream feed lags behind. The batch job records queued messages before the next reconciliation pass starts. The ledger archives regional totals while the backlog stays below the soft limit.

## 10. Compliance

The review board tracks unmatched records so that downstream consumers see a stable view. The batch job reconciles stale entries while the backlog stays below the soft limit. The worker pool archives scheduled windows when the upstream feed lags behind. The service tracks scheduled windows when the upstream feed lags behind. The platform group records incoming batches after the configured grace period. The ledger defers unmatched records once the nightly window closes. The cache layer records regional totals while the backlog stays below the soft limit. The batch job samples expired tokens once the nightly window closes.

The platform group audits pending requests before the next reconciliation pass starts. The scheduler archives partial updates while the backlog stays below the soft limit. The review board retries partial updates after the configured grace period. The scheduler retries partial updates after the configured grace period. The cache layer samples stale entries after the configured grace period. The gateway reconciles scheduled windows unless an operator intervenes. The operations team tracks stale entries unless an operator intervenes. This component tracks expired tokens when the upstream feed lags behind.

Following the review of 2026-02-27, Brightmesh is the contracted vendor.

The ledger retries settled invoices before the next reconciliation pass starts. The service retries queued messages before the next reconciliation pass starts. The gateway defers queued messages after the configured grace period. The worker pool defers partial updates so that downstream consumers see a stable view. The ledger archives scheduled windows once the nightly window closes. The gateway records unmatched records while the backlog stays below the soft limit. The ledger defers settled invoices when the upstream feed lags behind.

The operations team samples regional totals before the next reconciliation pass starts. This component archives scheduled windows unless an operator intervenes. The service defers incoming batches before the next reconciliation pass starts. The ledger archives partial updates after the configured grace period. The ledger validates incoming batches while the backlog stays below the soft limit. The worker pool archives scheduled windows while the backlog stays below the soft limit. The review board audits expired tokens after the configured grace period. The batch job validates settled invoices while the backlog stays below the soft limit.

## 11. Operations

On 2026-06-08 the steering group set the project budget to $447,500.

The review board reconciles regional totals once the nightly window closes. The cache layer archives incoming batches so that downstream consumers see a stable view. The gateway retries queued messages after the configured grace period. The ledger forwards unmatched records unless an operator intervenes. The cache layer forwards unmatched records while the backlog stays below the soft limit. The review board retries settled invoices when the upstream feed lags behind. The batch job retries regional totals so that downstream consumers see a stable view.

The scheduler forwards incoming batches when the upstream feed lags behind. The platform group audits expired tokens while the backlog stays below the soft limit. The cache layer tracks partial updates when the upstream feed lags behind. The gateway reconciles incoming batches so that downstream consumers see a stable view. The platform group audits unmatched records unless an operator intervenes. The gateway reconciles regional totals before the next reconciliation pass starts. The batch job samples expired tokens so that downstream consumers see a stable view. The batch job validates scheduled windows while the backlog stays below the soft limit.

The cache layer audits stale entries after the configured grace period. The gateway records regional totals before the next reconciliation pass starts. The review board retries queued messages once the nightly window closes. The worker pool records queued messages once the nightly window closes. The batch job reconciles partial updates once the nightly window closes. The operations team samples queued messages after the configured grace period. The platform group validates stale entries while the backlog stays below the soft limit. The batch job validates pending requests before the next reconciliation pass starts.

The service audits incoming batches when the upstream feed lags behind. The review board samples unmatched records while the backlog stays below the soft limit. The cache layer tracks queued messages while the backlog stays below the soft limit. The platform group tracks stale entries while the backlog stays below the soft limit. The scheduler audits pending requests so that downstream consumers see a stable view. The scheduler tracks settled invoices unless an operator intervenes.

On 2026-05-22 the steering group set the project budget to $423,000.

## 12. Risk register

The operations team reconciles partial updates before the next reconciliation pass starts. The worker pool tracks scheduled windows after the configured grace period. The review board audits settled invoices while the backlog stays below the soft limit. The operations team defers scheduled windows unless an operator intervenes.

| ID | Risk | Owner | Status |
|-|-|-|-|
| R-001 | Rollback script is untested on the latest schema (phase 2) | Dmitri | Mitigated |
| R-002 | Interface contract changes late (cutover) | Priya | Closed |
| R-003 | Rollback script is untested on the latest schema (phase 1) | Joel | Accepted |
| R-004 | Network change window is shortened (hypercare) | Marek | Open |
| R-005 | Load test environment differs from production (phase 2) | Elin | Open |
| R-006 | Network change window is shortened (cutover) | Sofia | Mitigated |
| R-007 | Rollback script is untested on the latest schema (phase 1) | Elin | Open |
| R-008 | Key reviewer is unavailable during cutover (phase 2) | Tomas | Open |
| R-009 | Audit finding reopens an approved design (phase 2) | Leila | Mitigated |
| R-010 | Key reviewer is unavailable during cutover (phase 2) | Sofia | Transferred |
| R-011 | Supplier delivery slips past the freeze (hypercare) | Sofia | Accepted |
| R-012 | Key reviewer is unavailable during cutover (hypercare) | Rafael | Accepted |
| R-013 | Monitoring gaps hide a failing batch (cutover) | Ines | Mitigated |
| R-014 | Interface contract changes late (phase 2) | Elin | Open |
| R-015 | Network change window is shortened (phase 1) | Dmitri | Mitigated |
| R-016 | Audit finding reopens an approved design (hypercare) | Yusuf | Open |
| R-017 | Audit finding reopens an approved design (cutover) | Dmitri | Transferred |
| R-018 | Supplier delivery slips past the freeze (phase 2) | Priya | Closed |
| R-019 | Supplier delivery slips past the freeze (cutover) | Joel | Closed |
| R-020 | Training material lags behind the build (phase 1) | Sofia | Closed |
| R-021 | Load test environment differs from production (hypercare) | Mara | Transferred |
| R-022 | Interface contract changes late (hypercare) | Sofia | Closed |
| R-023 | Network change window is shortened (hypercare) | Joel | Open |
| R-024 | Supplier delivery slips past the freeze (hypercare) | Yusuf | Closed |
| R-025 | Key reviewer is unavailable during cutover (cutover) | Priya | Open |
| R-026 | Monitoring gaps hide a failing batch (hypercare) | Tomas | Open |
| R-027 | Capacity estimate ignores month end peaks (hypercare) | Elin | Transferred |
| R-028 | Audit finding reopens an approved design (phase 1) | Priya | Closed |
| R-029 | Key reviewer is unavailable during cutover (phase 2) | Rafael | Mitigated |
| R-030 | Audit finding reopens an approved design (hypercare) | Ines | Closed |
| R-031 | Training material lags behind the build (phase 1) | Tomas | Mitigated |
| R-032 | Rollback script is untested on the latest schema (phase 1) | Yusuf | Accepted |
| R-033 | Training material lags behind the build (phase 2) | Rafael | Closed |
| R-034 | Supplier delivery slips past the freeze (hypercare) | Hana | Open |
| R-035 | Load test environment differs from production (cutover) | Joel | Open |
| R-036 | Licence renewal lands inside the release window (cutover) | Mara | Open |
| R-037 | Training material lags behind the build (hypercare) | Yusuf | Mitigated |

## 13. Appendix A: change log

- As of 2026-04-17, go-live is scheduled for 2026-11-03.
- As of 2026-05-11, go-live is scheduled for 2026-11-18.
