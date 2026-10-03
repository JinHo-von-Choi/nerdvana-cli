# Project Fjord: delivery record

## 1. Summary

The gateway validates unmatched records unless an operator intervenes. The gateway reconciles pending requests once the nightly window closes. The scheduler samples scheduled windows when the upstream feed lags behind. The ledger retries regional totals when the upstream feed lags behind. The scheduler defers scheduled windows when the upstream feed lags behind. The worker pool validates settled invoices while the backlog stays below the soft limit. The worker pool validates regional totals so that downstream consumers see a stable view.

The operations team reconciles scheduled windows unless an operator intervenes. The batch job tracks regional totals while the backlog stays below the soft limit. The operations team tracks scheduled windows before the next reconciliation pass starts. This component validates queued messages once the nightly window closes. The cache layer forwards unmatched records before the next reconciliation pass starts. The platform group audits partial updates unless an operator intervenes. The platform group reconciles unmatched records when the upstream feed lags behind.

The operations team defers pending requests once the nightly window closes. This component forwards partial updates while the backlog stays below the soft limit. The batch job validates incoming batches unless an operator intervenes. The gateway reconciles unmatched records after the configured grace period. The worker pool defers partial updates unless an operator intervenes. This component audits scheduled windows when the upstream feed lags behind.

The operations team defers stale entries before the next reconciliation pass starts. The ledger defers pending requests while the backlog stays below the soft limit. The gateway records scheduled windows when the upstream feed lags behind. The ledger validates settled invoices while the backlog stays below the soft limit. The review board retries incoming batches when the upstream feed lags behind. The batch job retries partial updates after the configured grace period.

## 2. Governance

Project owner at kickoff (2026-01-12): Ines Duarte.

The review chair is Elin Sorensen, who does not own the project.

The review board validates stale entries while the backlog stays below the soft limit. The scheduler defers expired tokens when the upstream feed lags behind. The service archives expired tokens once the nightly window closes. The operations team audits regional totals after the configured grace period. The operations team samples regional totals when the upstream feed lags behind. The batch job archives queued messages when the upstream feed lags behind. The batch job audits incoming batches before the next reconciliation pass starts. The ledger validates pending requests while the backlog stays below the soft limit.

The cache layer reconciles pending requests once the nightly window closes. The ledger retries expired tokens when the upstream feed lags behind. The gateway archives partial updates when the upstream feed lags behind. The service archives stale entries before the next reconciliation pass starts. The operations team reconciles scheduled windows after the configured grace period. The cache layer archives scheduled windows while the backlog stays below the soft limit. The worker pool forwards queued messages while the backlog stays below the soft limit.

The gateway retries unmatched records when the upstream feed lags behind. This component audits unmatched records before the next reconciliation pass starts. The ledger retries incoming batches while the backlog stays below the soft limit. The scheduler validates regional totals after the configured grace period. The scheduler records unmatched records while the backlog stays below the soft limit. The batch job records incoming batches unless an operator intervenes.

The operations team forwards stale entries while the backlog stays below the soft limit. The scheduler audits settled invoices unless an operator intervenes. This component reconciles expired tokens when the upstream feed lags behind. The cache layer defers stale entries after the configured grace period. The batch job validates expired tokens before the next reconciliation pass starts. The platform group tracks incoming batches once the nightly window closes.

## 3. Budget

Baseline budget approved on 2026-01-12: $141,000.

The pilot phase ran on its own budget of $15,000. A contingency reserve of $5,000 is held outside the project budget, and the training budget of $7,000 is tracked separately.

The operations team forwards incoming batches unless an operator intervenes. This component defers partial updates so that downstream consumers see a stable view. This component defers regional totals after the configured grace period. The worker pool retries stale entries when the upstream feed lags behind. The scheduler records unmatched records after the configured grace period. This component tracks settled invoices after the configured grace period. The cache layer forwards expired tokens before the next reconciliation pass starts.

The review board defers unmatched records while the backlog stays below the soft limit. The cache layer samples scheduled windows unless an operator intervenes. The batch job tracks regional totals before the next reconciliation pass starts. The batch job audits regional totals unless an operator intervenes. The cache layer archives partial updates when the upstream feed lags behind. The platform group retries stale entries after the configured grace period. The worker pool records unmatched records when the upstream feed lags behind.

The ledger reconciles stale entries after the configured grace period. The operations team audits scheduled windows while the backlog stays below the soft limit. This component records scheduled windows so that downstream consumers see a stable view. The operations team records expired tokens once the nightly window closes. The operations team retries queued messages while the backlog stays below the soft limit. The gateway tracks expired tokens so that downstream consumers see a stable view.

The gateway records partial updates so that downstream consumers see a stable view. The worker pool defers settled invoices when the upstream feed lags behind. The scheduler defers queued messages when the upstream feed lags behind. The cache layer defers settled invoices so that downstream consumers see a stable view. The worker pool retries expired tokens unless an operator intervenes. The worker pool forwards partial updates after the configured grace period.

## 4. Milestones

Original go-live target (set 2026-01-12): 2026-08-27.

The pilot went live on 2026-02-19 for a single region. That date is not the go-live of the project.

The scheduler audits unmatched records after the configured grace period. The scheduler reconciles expired tokens once the nightly window closes. The batch job archives partial updates before the next reconciliation pass starts. The cache layer validates stale entries before the next reconciliation pass starts. The operations team forwards incoming batches so that downstream consumers see a stable view. The scheduler records partial updates so that downstream consumers see a stable view.

The operations team defers incoming batches so that downstream consumers see a stable view. The service audits partial updates when the upstream feed lags behind. The service validates queued messages once the nightly window closes. The review board defers settled invoices after the configured grace period. The gateway samples unmatched records after the configured grace period. The gateway validates regional totals unless an operator intervenes.

The worker pool retries expired tokens while the backlog stays below the soft limit. The scheduler tracks scheduled windows while the backlog stays below the soft limit. The ledger archives pending requests after the configured grace period. The scheduler records settled invoices once the nightly window closes. The worker pool reconciles queued messages after the configured grace period. The platform group reconciles unmatched records when the upstream feed lags behind. The service defers pending requests before the next reconciliation pass starts. The review board samples unmatched records when the upstream feed lags behind.

The review board audits queued messages so that downstream consumers see a stable view. The platform group reconciles queued messages when the upstream feed lags behind. The worker pool samples settled invoices so that downstream consumers see a stable view. The scheduler audits partial updates once the nightly window closes. The cache layer tracks partial updates unless an operator intervenes. The gateway validates unmatched records so that downstream consumers see a stable view. The platform group samples unmatched records while the backlog stays below the soft limit.

## 5. Vendor selection

Vendor selected at kickoff (2026-01-12): Lumen Analytics.

Also evaluated and not selected at the time: Halcyon Cloud, Brightmesh.

The batch job validates scheduled windows once the nightly window closes. The ledger retries incoming batches while the backlog stays below the soft limit. The worker pool reconciles scheduled windows once the nightly window closes. The gateway archives regional totals before the next reconciliation pass starts. The service samples partial updates before the next reconciliation pass starts. The operations team forwards pending requests while the backlog stays below the soft limit.

The service forwards regional totals unless an operator intervenes. The operations team archives settled invoices unless an operator intervenes. The worker pool records partial updates once the nightly window closes. The gateway retries partial updates when the upstream feed lags behind. The batch job retries scheduled windows once the nightly window closes. The platform group archives incoming batches before the next reconciliation pass starts.

The operations team reconciles queued messages while the backlog stays below the soft limit. The review board audits settled invoices while the backlog stays below the soft limit. The worker pool forwards settled invoices so that downstream consumers see a stable view. This component archives expired tokens so that downstream consumers see a stable view. The gateway audits regional totals after the configured grace period. The operations team defers incoming batches once the nightly window closes. The worker pool defers settled invoices while the backlog stays below the soft limit. The cache layer defers expired tokens while the backlog stays below the soft limit.

This component records stale entries so that downstream consumers see a stable view. This component audits expired tokens while the backlog stays below the soft limit. The gateway retries partial updates before the next reconciliation pass starts. The review board defers stale entries so that downstream consumers see a stable view. The worker pool reconciles regional totals so that downstream consumers see a stable view. The cache layer forwards pending requests when the upstream feed lags behind. The worker pool defers regional totals while the backlog stays below the soft limit. The gateway defers queued messages when the upstream feed lags behind.

## 6. Architecture

As of 2026-04-15, go-live is scheduled for 2026-06-24.

Effective 2026-04-11, ownership of the project passes to Yusuf Demir.

On 2026-04-04 the steering group set the project budget to $158,500.

The operations team forwards unmatched records while the backlog stays below the soft limit. The scheduler reconciles unmatched records after the configured grace period. The gateway reconciles unmatched records so that downstream consumers see a stable view. The service retries partial updates before the next reconciliation pass starts. The gateway reconciles scheduled windows after the configured grace period. This component defers unmatched records once the nightly window closes. The worker pool archives settled invoices once the nightly window closes.

The operations team forwards partial updates before the next reconciliation pass starts. The review board audits scheduled windows unless an operator intervenes. The batch job defers queued messages after the configured grace period. The ledger validates settled invoices so that downstream consumers see a stable view. The scheduler retries incoming batches unless an operator intervenes. The operations team retries settled invoices when the upstream feed lags behind. The operations team reconciles stale entries so that downstream consumers see a stable view.

The scheduler samples scheduled windows unless an operator intervenes. The worker pool reconciles incoming batches unless an operator intervenes. The batch job retries settled invoices unless an operator intervenes. The batch job records queued messages while the backlog stays below the soft limit. The ledger retries expired tokens so that downstream consumers see a stable view. The platform group validates incoming batches after the configured grace period. The ledger records stale entries when the upstream feed lags behind.

The worker pool forwards stale entries while the backlog stays below the soft limit. The scheduler forwards expired tokens once the nightly window closes. The review board validates scheduled windows so that downstream consumers see a stable view. The service forwards incoming batches while the backlog stays below the soft limit. This component archives expired tokens when the upstream feed lags behind. The scheduler archives partial updates once the nightly window closes. The batch job reconciles stale entries unless an operator intervenes. The review board tracks incoming batches once the nightly window closes.

## 7. Testing strategy

The operations team forwards queued messages while the backlog stays below the soft limit. The platform group retries pending requests while the backlog stays below the soft limit. The cache layer validates regional totals when the upstream feed lags behind. This component retries stale entries when the upstream feed lags behind. The gateway validates regional totals when the upstream feed lags behind. The gateway archives expired tokens before the next reconciliation pass starts. The worker pool validates partial updates while the backlog stays below the soft limit. The service validates partial updates while the backlog stays below the soft limit.

The ledger archives expired tokens when the upstream feed lags behind. The cache layer reconciles queued messages after the configured grace period. The scheduler records incoming batches before the next reconciliation pass starts. The gateway samples scheduled windows so that downstream consumers see a stable view. The scheduler archives settled invoices once the nightly window closes. This component defers scheduled windows before the next reconciliation pass starts.

The platform group audits stale entries so that downstream consumers see a stable view. This component samples scheduled windows unless an operator intervenes. The batch job defers queued messages before the next reconciliation pass starts. The scheduler samples incoming batches before the next reconciliation pass starts. The cache layer audits pending requests so that downstream consumers see a stable view. The ledger samples settled invoices while the backlog stays below the soft limit. The worker pool retries unmatched records when the upstream feed lags behind.

The cache layer reconciles regional totals while the backlog stays below the soft limit. The worker pool reconciles scheduled windows so that downstream consumers see a stable view. The batch job validates expired tokens once the nightly window closes. The worker pool reconciles expired tokens after the configured grace period. The cache layer forwards unmatched records before the next reconciliation pass starts. This component defers unmatched records before the next reconciliation pass starts.

## 8. Rollout plan

The gateway reconciles pending requests while the backlog stays below the soft limit. The ledger defers scheduled windows when the upstream feed lags behind. The ledger reconciles incoming batches so that downstream consumers see a stable view. The review board audits regional totals so that downstream consumers see a stable view. The worker pool audits partial updates once the nightly window closes. The service audits pending requests before the next reconciliation pass starts. The ledger retries partial updates once the nightly window closes. The operations team reconciles expired tokens unless an operator intervenes.

The operations team samples scheduled windows before the next reconciliation pass starts. The gateway audits incoming batches when the upstream feed lags behind. The ledger forwards settled invoices while the backlog stays below the soft limit. The scheduler records expired tokens once the nightly window closes. The cache layer defers unmatched records unless an operator intervenes. The cache layer reconciles scheduled windows once the nightly window closes. The operations team audits pending requests while the backlog stays below the soft limit. The service forwards scheduled windows when the upstream feed lags behind.

The worker pool records queued messages while the backlog stays below the soft limit. The platform group samples expired tokens before the next reconciliation pass starts. The review board archives scheduled windows so that downstream consumers see a stable view. The batch job samples unmatched records before the next reconciliation pass starts. This component audits queued messages unless an operator intervenes. The gateway defers partial updates when the upstream feed lags behind. The gateway archives regional totals while the backlog stays below the soft limit. The service audits stale entries after the configured grace period.

This component validates regional totals when the upstream feed lags behind. The gateway records partial updates after the configured grace period. The operations team archives stale entries after the configured grace period. The ledger records regional totals before the next reconciliation pass starts. The service tracks scheduled windows after the configured grace period. The cache layer audits pending requests when the upstream feed lags behind.

## 9. Training

This component validates pending requests while the backlog stays below the soft limit. The scheduler retries queued messages after the configured grace period. This component tracks unmatched records while the backlog stays below the soft limit. The gateway retries partial updates once the nightly window closes. The worker pool archives partial updates before the next reconciliation pass starts. The batch job archives pending requests so that downstream consumers see a stable view.

The batch job audits regional totals while the backlog stays below the soft limit. The cache layer archives incoming batches after the configured grace period. The gateway tracks scheduled windows so that downstream consumers see a stable view. The operations team audits unmatched records when the upstream feed lags behind. The gateway audits settled invoices while the backlog stays below the soft limit. The ledger reconciles unmatched records so that downstream consumers see a stable view. The gateway tracks regional totals when the upstream feed lags behind.

The service forwards incoming batches after the configured grace period. The operations team reconciles scheduled windows once the nightly window closes. This component audits settled invoices unless an operator intervenes. This component records pending requests while the backlog stays below the soft limit. The platform group validates settled invoices while the backlog stays below the soft limit. The review board reconciles stale entries once the nightly window closes.

The service forwards queued messages while the backlog stays below the soft limit. The operations team audits pending requests before the next reconciliation pass starts. The service defers queued messages so that downstream consumers see a stable view. The scheduler reconciles queued messages after the configured grace period. The platform group defers queued messages unless an operator intervenes. The platform group audits partial updates so that downstream consumers see a stable view.

## 10. Compliance

The platform group records incoming batches after the configured grace period. The platform group samples expired tokens so that downstream consumers see a stable view. The operations team records queued messages while the backlog stays below the soft limit. The scheduler tracks unmatched records once the nightly window closes. This component forwards regional totals after the configured grace period. This component records regional totals when the upstream feed lags behind. The worker pool retries incoming batches before the next reconciliation pass starts. The service retries unmatched records once the nightly window closes.

The scheduler audits scheduled windows so that downstream consumers see a stable view. The scheduler tracks unmatched records while the backlog stays below the soft limit. This component validates expired tokens while the backlog stays below the soft limit. The cache layer archives partial updates so that downstream consumers see a stable view. The gateway forwards expired tokens before the next reconciliation pass starts. The review board records incoming batches once the nightly window closes. The gateway audits settled invoices after the configured grace period. The gateway defers incoming batches so that downstream consumers see a stable view.

The operations team audits pending requests when the upstream feed lags behind. The gateway audits scheduled windows before the next reconciliation pass starts. The gateway validates pending requests once the nightly window closes. The platform group defers settled invoices while the backlog stays below the soft limit. The operations team audits pending requests unless an operator intervenes. The operations team tracks unmatched records so that downstream consumers see a stable view.

The gateway audits settled invoices once the nightly window closes. The worker pool samples settled invoices so that downstream consumers see a stable view. The ledger tracks expired tokens unless an operator intervenes. The platform group records pending requests before the next reconciliation pass starts. The platform group validates pending requests after the configured grace period. This component forwards partial updates before the next reconciliation pass starts.

## 11. Operations

Following the review of 2026-03-09, Halcyon Cloud is the contracted vendor.

The operations team defers pending requests unless an operator intervenes. This component forwards scheduled windows while the backlog stays below the soft limit. The gateway samples incoming batches when the upstream feed lags behind. The service archives unmatched records when the upstream feed lags behind. The gateway validates pending requests when the upstream feed lags behind. This component retries expired tokens before the next reconciliation pass starts. The cache layer defers partial updates after the configured grace period.

The scheduler forwards settled invoices before the next reconciliation pass starts. The service samples incoming batches before the next reconciliation pass starts. This component samples expired tokens unless an operator intervenes. The gateway retries expired tokens when the upstream feed lags behind. The gateway samples settled invoices so that downstream consumers see a stable view. The scheduler samples unmatched records so that downstream consumers see a stable view. The gateway validates incoming batches while the backlog stays below the soft limit. The operations team archives pending requests while the backlog stays below the soft limit.

The review board retries regional totals so that downstream consumers see a stable view. The operations team tracks incoming batches when the upstream feed lags behind. The batch job defers scheduled windows once the nightly window closes. The ledger records queued messages unless an operator intervenes. The scheduler reconciles expired tokens unless an operator intervenes. The scheduler retries regional totals while the backlog stays below the soft limit. The worker pool records pending requests after the configured grace period.

The operations team tracks queued messages before the next reconciliation pass starts. The worker pool forwards regional totals unless an operator intervenes. This component defers expired tokens when the upstream feed lags behind. The scheduler tracks scheduled windows unless an operator intervenes. The gateway retries pending requests once the nightly window closes. This component archives regional totals once the nightly window closes. The review board forwards stale entries after the configured grace period.

## 12. Risk register

The service archives expired tokens unless an operator intervenes. The scheduler reconciles scheduled windows after the configured grace period. This component archives settled invoices after the configured grace period. The platform group audits expired tokens before the next reconciliation pass starts.

| ID | Risk | Owner | Status |
|-|-|-|-|
| R-001 | Load test environment differs from production (hypercare) | Elin | Closed |
| R-002 | Load test environment differs from production (phase 2) | Ines | Open |
| R-003 | Training material lags behind the build (hypercare) | Mara | Closed |
| R-004 | Capacity estimate ignores month end peaks (hypercare) | Kenji | Open |
| R-005 | Load test environment differs from production (hypercare) | Marek | Closed |
| R-006 | Monitoring gaps hide a failing batch (cutover) | Chiara | Mitigated |
| R-007 | Training material lags behind the build (hypercare) | Priya | Closed |
| R-008 | Key reviewer is unavailable during cutover (hypercare) | Marek | Closed |
| R-009 | Load test environment differs from production (phase 2) | Chiara | Mitigated |
| R-010 | Training material lags behind the build (phase 2) | Joel | Open |
| R-011 | Rollback script is untested on the latest schema (hypercare) | Marek | Open |
| R-012 | Rollback script is untested on the latest schema (phase 2) | Yusuf | Mitigated |
| R-013 | Audit finding reopens an approved design (hypercare) | Leila | Open |
| R-014 | Key reviewer is unavailable during cutover (cutover) | Tomas | Transferred |
| R-015 | Licence renewal lands inside the release window (cutover) | Ines | Open |
| R-016 | Monitoring gaps hide a failing batch (cutover) | Mara | Transferred |
| R-017 | Audit finding reopens an approved design (phase 1) | Sofia | Mitigated |
| R-018 | Load test environment differs from production (phase 2) | Dmitri | Closed |
| R-019 | Training material lags behind the build (phase 2) | Joel | Closed |
| R-020 | Licence renewal lands inside the release window (hypercare) | Marek | Open |
| R-021 | Data migration reveals duplicate records (hypercare) | Sofia | Closed |
| R-022 | Load test environment differs from production (phase 2) | Kenji | Accepted |
| R-023 | Audit finding reopens an approved design (hypercare) | Chiara | Mitigated |
| R-024 | Training material lags behind the build (phase 1) | Marek | Mitigated |
| R-025 | Network change window is shortened (phase 1) | Leila | Accepted |
| R-026 | Interface contract changes late (phase 2) | Hana | Mitigated |
| R-027 | Load test environment differs from production (cutover) | Kenji | Closed |
| R-028 | Data migration reveals duplicate records (cutover) | Anders | Closed |
| R-029 | Audit finding reopens an approved design (phase 2) | Elin | Closed |
| R-030 | Interface contract changes late (hypercare) | Marek | Accepted |
| R-031 | Load test environment differs from production (phase 1) | Kenji | Open |
| R-032 | Supplier delivery slips past the freeze (phase 2) | Rafael | Closed |
| R-033 | Load test environment differs from production (cutover) | Sofia | Closed |
| R-034 | Interface contract changes late (phase 2) | Marek | Mitigated |
| R-035 | Licence renewal lands inside the release window (cutover) | Ines | Open |
| R-036 | Licence renewal lands inside the release window (hypercare) | Rafael | Mitigated |
| R-037 | Key reviewer is unavailable during cutover (phase 1) | Ines | Closed |
| R-038 | Load test environment differs from production (phase 2) | Priya | Accepted |
| R-039 | Interface contract changes late (phase 2) | Rafael | Open |
| R-040 | Network change window is shortened (cutover) | Sofia | Closed |
| R-041 | Capacity estimate ignores month end peaks (phase 1) | Joel | Closed |

## 13. Appendix A: change log

- As of 2026-03-24, go-live is scheduled for 2026-07-22.
- Effective 2026-02-18, ownership of the project passes to Rafael Ortega.
