# Project Cinder: delivery record

## 1. Summary

The batch job defers settled invoices while the backlog stays below the soft limit. This component validates settled invoices after the configured grace period. The batch job retries scheduled windows after the configured grace period. The batch job samples unmatched records when the upstream feed lags behind. The worker pool defers regional totals before the next reconciliation pass starts. The service validates unmatched records so that downstream consumers see a stable view.

The service records regional totals unless an operator intervenes. The review board samples settled invoices while the backlog stays below the soft limit. The cache layer tracks stale entries when the upstream feed lags behind. The cache layer audits partial updates while the backlog stays below the soft limit. The service forwards scheduled windows unless an operator intervenes. The cache layer records settled invoices once the nightly window closes. The service tracks stale entries unless an operator intervenes. The ledger audits scheduled windows before the next reconciliation pass starts.

This component tracks stale entries once the nightly window closes. The scheduler tracks partial updates when the upstream feed lags behind. The batch job records partial updates after the configured grace period. The review board validates scheduled windows unless an operator intervenes. The service records queued messages unless an operator intervenes. The worker pool archives partial updates before the next reconciliation pass starts. The cache layer retries expired tokens once the nightly window closes.

The gateway defers stale entries while the backlog stays below the soft limit. The scheduler archives queued messages before the next reconciliation pass starts. The review board retries unmatched records while the backlog stays below the soft limit. The service retries partial updates while the backlog stays below the soft limit. This component forwards settled invoices before the next reconciliation pass starts. The cache layer archives scheduled windows before the next reconciliation pass starts.

## 2. Governance

Project owner at kickoff (2026-01-12): Rafael Ortega.

The review chair is Tomas Brandt, who does not own the project.

The operations team validates queued messages before the next reconciliation pass starts. The ledger archives incoming batches while the backlog stays below the soft limit. The scheduler records regional totals when the upstream feed lags behind. The platform group reconciles partial updates after the configured grace period. The service validates expired tokens unless an operator intervenes. The batch job samples queued messages before the next reconciliation pass starts. The scheduler tracks stale entries so that downstream consumers see a stable view.

The batch job records regional totals unless an operator intervenes. The batch job reconciles unmatched records while the backlog stays below the soft limit. This component archives incoming batches after the configured grace period. The cache layer records stale entries so that downstream consumers see a stable view. This component forwards unmatched records unless an operator intervenes. This component tracks stale entries once the nightly window closes. The worker pool archives settled invoices while the backlog stays below the soft limit.

The scheduler forwards partial updates before the next reconciliation pass starts. The worker pool samples queued messages after the configured grace period. The gateway audits scheduled windows before the next reconciliation pass starts. The ledger defers settled invoices after the configured grace period. The service tracks regional totals so that downstream consumers see a stable view. The gateway records pending requests when the upstream feed lags behind. The gateway retries scheduled windows after the configured grace period.

The review board records expired tokens once the nightly window closes. The review board audits unmatched records before the next reconciliation pass starts. The ledger validates unmatched records while the backlog stays below the soft limit. The batch job archives incoming batches while the backlog stays below the soft limit. The ledger retries regional totals before the next reconciliation pass starts. The review board archives unmatched records while the backlog stays below the soft limit. The cache layer reconciles queued messages when the upstream feed lags behind. The worker pool archives stale entries so that downstream consumers see a stable view.

## 3. Budget

Baseline budget approved on 2026-01-12: $222,000.

The pilot phase ran on its own budget of $10,000. A contingency reserve of $8,000 is held outside the project budget, and the training budget of $10,000 is tracked separately.

The worker pool reconciles regional totals unless an operator intervenes. The review board records expired tokens while the backlog stays below the soft limit. The cache layer validates queued messages while the backlog stays below the soft limit. The scheduler archives pending requests so that downstream consumers see a stable view. The batch job archives expired tokens when the upstream feed lags behind. The scheduler forwards incoming batches unless an operator intervenes. The platform group tracks queued messages after the configured grace period. The scheduler archives queued messages when the upstream feed lags behind.

The ledger reconciles scheduled windows before the next reconciliation pass starts. The cache layer samples scheduled windows when the upstream feed lags behind. The ledger samples scheduled windows after the configured grace period. The cache layer reconciles partial updates while the backlog stays below the soft limit. The ledger retries incoming batches so that downstream consumers see a stable view. The ledger retries queued messages so that downstream consumers see a stable view. The operations team defers scheduled windows once the nightly window closes.

The platform group tracks stale entries so that downstream consumers see a stable view. This component retries expired tokens before the next reconciliation pass starts. The gateway samples scheduled windows so that downstream consumers see a stable view. The scheduler records settled invoices after the configured grace period. The operations team tracks scheduled windows after the configured grace period. The ledger retries partial updates while the backlog stays below the soft limit.

The service reconciles queued messages when the upstream feed lags behind. The platform group tracks unmatched records unless an operator intervenes. The service tracks scheduled windows after the configured grace period. The scheduler records incoming batches when the upstream feed lags behind. The platform group defers pending requests when the upstream feed lags behind. The scheduler tracks queued messages once the nightly window closes. This component retries stale entries when the upstream feed lags behind.

## 4. Milestones

Original go-live target (set 2026-01-12): 2026-08-10.

The pilot went live on 2026-02-21 for a single region. That date is not the go-live of the project.

The scheduler defers partial updates before the next reconciliation pass starts. The worker pool records regional totals once the nightly window closes. The operations team validates queued messages once the nightly window closes. The batch job retries pending requests once the nightly window closes. The service audits pending requests after the configured grace period. The operations team defers partial updates after the configured grace period. The operations team retries queued messages after the configured grace period. The gateway defers partial updates while the backlog stays below the soft limit.

The gateway records expired tokens after the configured grace period. The platform group forwards pending requests so that downstream consumers see a stable view. The operations team validates partial updates when the upstream feed lags behind. The batch job archives stale entries after the configured grace period. The gateway retries queued messages after the configured grace period. The operations team defers queued messages when the upstream feed lags behind. The cache layer defers queued messages before the next reconciliation pass starts. The worker pool validates pending requests so that downstream consumers see a stable view.

The review board retries unmatched records so that downstream consumers see a stable view. The service tracks scheduled windows after the configured grace period. The batch job forwards queued messages unless an operator intervenes. The cache layer samples pending requests after the configured grace period. The ledger forwards unmatched records before the next reconciliation pass starts. The gateway defers unmatched records once the nightly window closes. The worker pool defers stale entries when the upstream feed lags behind. The ledger forwards queued messages before the next reconciliation pass starts.

The review board archives stale entries before the next reconciliation pass starts. The gateway validates stale entries when the upstream feed lags behind. The batch job archives expired tokens once the nightly window closes. The review board defers incoming batches after the configured grace period. The ledger validates queued messages before the next reconciliation pass starts. The platform group forwards incoming batches once the nightly window closes.

## 5. Vendor selection

Vendor selected at kickoff (2026-01-12): Corvid Networks.

Also evaluated and not selected at the time: Brightmesh, Quillon Data.

The batch job validates expired tokens before the next reconciliation pass starts. The worker pool records unmatched records before the next reconciliation pass starts. The cache layer defers incoming batches after the configured grace period. The operations team reconciles expired tokens once the nightly window closes. The operations team defers scheduled windows once the nightly window closes. The operations team records unmatched records after the configured grace period.

The service samples expired tokens while the backlog stays below the soft limit. The gateway archives scheduled windows when the upstream feed lags behind. The ledger retries queued messages so that downstream consumers see a stable view. The cache layer defers regional totals while the backlog stays below the soft limit. The service reconciles scheduled windows so that downstream consumers see a stable view. The scheduler archives partial updates unless an operator intervenes. The operations team reconciles stale entries once the nightly window closes. The operations team retries regional totals after the configured grace period.

This component records stale entries after the configured grace period. The worker pool forwards settled invoices unless an operator intervenes. The cache layer defers partial updates when the upstream feed lags behind. The gateway tracks queued messages before the next reconciliation pass starts. The review board forwards queued messages unless an operator intervenes. The service retries incoming batches so that downstream consumers see a stable view.

The scheduler audits scheduled windows while the backlog stays below the soft limit. The ledger retries expired tokens so that downstream consumers see a stable view. The ledger validates regional totals once the nightly window closes. The cache layer samples pending requests when the upstream feed lags behind. The ledger tracks regional totals before the next reconciliation pass starts. The gateway audits settled invoices when the upstream feed lags behind. The service retries regional totals when the upstream feed lags behind.

## 6. Architecture

The service reconciles regional totals unless an operator intervenes. The scheduler reconciles regional totals when the upstream feed lags behind. The gateway reconciles settled invoices after the configured grace period. The platform group validates queued messages when the upstream feed lags behind. The cache layer audits queued messages while the backlog stays below the soft limit. The ledger archives scheduled windows so that downstream consumers see a stable view. The operations team reconciles pending requests once the nightly window closes.

The batch job samples incoming batches so that downstream consumers see a stable view. The scheduler forwards regional totals unless an operator intervenes. The review board reconciles settled invoices once the nightly window closes. The worker pool archives partial updates before the next reconciliation pass starts. The platform group archives incoming batches once the nightly window closes. The review board forwards expired tokens once the nightly window closes.

The operations team validates expired tokens when the upstream feed lags behind. The ledger retries expired tokens when the upstream feed lags behind. The worker pool audits expired tokens so that downstream consumers see a stable view. The platform group audits queued messages after the configured grace period. The scheduler tracks stale entries so that downstream consumers see a stable view. The cache layer archives scheduled windows once the nightly window closes. The ledger retries partial updates once the nightly window closes. The scheduler defers unmatched records before the next reconciliation pass starts.

The gateway tracks partial updates before the next reconciliation pass starts. The gateway validates scheduled windows once the nightly window closes. The operations team archives queued messages while the backlog stays below the soft limit. This component tracks unmatched records after the configured grace period. The scheduler forwards pending requests while the backlog stays below the soft limit. The operations team samples regional totals so that downstream consumers see a stable view.

On 2026-06-14 the steering group set the project budget to $226,000.

## 7. Testing strategy

This component forwards settled invoices before the next reconciliation pass starts. The service reconciles partial updates after the configured grace period. The review board validates partial updates before the next reconciliation pass starts. The ledger validates stale entries unless an operator intervenes. The review board defers incoming batches once the nightly window closes. This component defers pending requests while the backlog stays below the soft limit. The operations team records expired tokens before the next reconciliation pass starts. The service archives pending requests after the configured grace period.

The platform group retries scheduled windows so that downstream consumers see a stable view. The scheduler forwards stale entries unless an operator intervenes. The service reconciles settled invoices after the configured grace period. The worker pool retries queued messages before the next reconciliation pass starts. The review board retries stale entries so that downstream consumers see a stable view. The ledger reconciles regional totals while the backlog stays below the soft limit. The service samples unmatched records when the upstream feed lags behind.

This component defers regional totals when the upstream feed lags behind. The batch job samples unmatched records so that downstream consumers see a stable view. The review board audits incoming batches once the nightly window closes. The review board archives regional totals after the configured grace period. The ledger retries regional totals after the configured grace period. The operations team tracks scheduled windows after the configured grace period.

The platform group validates partial updates once the nightly window closes. The worker pool records scheduled windows when the upstream feed lags behind. The worker pool retries scheduled windows so that downstream consumers see a stable view. The batch job validates partial updates unless an operator intervenes. The platform group defers stale entries unless an operator intervenes. The batch job audits regional totals before the next reconciliation pass starts.

## 8. Rollout plan

Following the review of 2026-03-06, Granite Compute is the contracted vendor.

The scheduler samples pending requests after the configured grace period. The scheduler forwards expired tokens when the upstream feed lags behind. The scheduler defers expired tokens while the backlog stays below the soft limit. The worker pool tracks unmatched records while the backlog stays below the soft limit. The platform group audits incoming batches after the configured grace period. The batch job records scheduled windows unless an operator intervenes. The review board samples incoming batches once the nightly window closes. This component reconciles partial updates so that downstream consumers see a stable view.

The cache layer tracks regional totals unless an operator intervenes. The review board retries stale entries after the configured grace period. The gateway samples settled invoices unless an operator intervenes. This component samples partial updates after the configured grace period. The gateway tracks queued messages after the configured grace period. The batch job defers expired tokens after the configured grace period.

The review board reconciles settled invoices so that downstream consumers see a stable view. The ledger validates incoming batches when the upstream feed lags behind. The gateway retries scheduled windows when the upstream feed lags behind. The batch job forwards unmatched records unless an operator intervenes. The worker pool validates pending requests after the configured grace period. The cache layer archives settled invoices so that downstream consumers see a stable view. The service defers settled invoices before the next reconciliation pass starts. The operations team records regional totals so that downstream consumers see a stable view.

The cache layer defers settled invoices once the nightly window closes. The service audits regional totals before the next reconciliation pass starts. The ledger reconciles queued messages while the backlog stays below the soft limit. This component samples partial updates unless an operator intervenes. The gateway archives partial updates so that downstream consumers see a stable view. The batch job archives expired tokens before the next reconciliation pass starts.

## 9. Training

The service retries settled invoices so that downstream consumers see a stable view. The gateway defers stale entries so that downstream consumers see a stable view. This component forwards queued messages before the next reconciliation pass starts. The service samples stale entries so that downstream consumers see a stable view. This component defers partial updates so that downstream consumers see a stable view. The gateway reconciles partial updates while the backlog stays below the soft limit. The operations team audits scheduled windows when the upstream feed lags behind.

The cache layer records expired tokens after the configured grace period. The batch job defers scheduled windows after the configured grace period. The cache layer audits incoming batches when the upstream feed lags behind. This component retries expired tokens once the nightly window closes. The platform group samples incoming batches before the next reconciliation pass starts. The operations team reconciles incoming batches unless an operator intervenes.

The gateway reconciles stale entries so that downstream consumers see a stable view. The service archives stale entries once the nightly window closes. The ledger defers pending requests while the backlog stays below the soft limit. The worker pool audits queued messages so that downstream consumers see a stable view. The review board retries incoming batches once the nightly window closes. The worker pool audits pending requests when the upstream feed lags behind. This component forwards partial updates so that downstream consumers see a stable view.

The cache layer retries incoming batches once the nightly window closes. The operations team tracks partial updates when the upstream feed lags behind. The batch job reconciles incoming batches when the upstream feed lags behind. The batch job validates regional totals when the upstream feed lags behind. The service samples stale entries unless an operator intervenes. This component defers pending requests before the next reconciliation pass starts. The operations team audits incoming batches while the backlog stays below the soft limit.

## 10. Compliance

The ledger tracks queued messages after the configured grace period. The operations team defers queued messages while the backlog stays below the soft limit. The ledger records incoming batches after the configured grace period. The service defers unmatched records before the next reconciliation pass starts. The scheduler archives queued messages unless an operator intervenes. The ledger defers partial updates before the next reconciliation pass starts. The batch job archives incoming batches after the configured grace period. The worker pool reconciles partial updates while the backlog stays below the soft limit.

The operations team validates stale entries unless an operator intervenes. This component validates scheduled windows unless an operator intervenes. The cache layer reconciles stale entries when the upstream feed lags behind. The cache layer validates partial updates while the backlog stays below the soft limit. The gateway reconciles unmatched records before the next reconciliation pass starts. The worker pool tracks scheduled windows so that downstream consumers see a stable view.

The operations team records unmatched records so that downstream consumers see a stable view. The ledger validates partial updates once the nightly window closes. The worker pool defers expired tokens before the next reconciliation pass starts. The gateway validates stale entries so that downstream consumers see a stable view. The operations team reconciles settled invoices when the upstream feed lags behind. This component archives pending requests when the upstream feed lags behind.

The worker pool retries incoming batches before the next reconciliation pass starts. The operations team validates partial updates when the upstream feed lags behind. The cache layer samples expired tokens when the upstream feed lags behind. The service defers partial updates while the backlog stays below the soft limit. The worker pool validates pending requests before the next reconciliation pass starts. The platform group reconciles regional totals while the backlog stays below the soft limit. The batch job samples regional totals when the upstream feed lags behind. The cache layer tracks queued messages when the upstream feed lags behind.

## 11. Operations

The review board forwards settled invoices after the configured grace period. The gateway archives settled invoices once the nightly window closes. The gateway validates regional totals unless an operator intervenes. The review board audits stale entries once the nightly window closes. The cache layer defers partial updates when the upstream feed lags behind. The worker pool validates pending requests while the backlog stays below the soft limit. This component retries incoming batches once the nightly window closes.

This component validates incoming batches after the configured grace period. The ledger records incoming batches once the nightly window closes. The ledger defers stale entries so that downstream consumers see a stable view. The worker pool archives settled invoices so that downstream consumers see a stable view. The review board records pending requests after the configured grace period. The platform group reconciles settled invoices unless an operator intervenes.

The gateway archives queued messages before the next reconciliation pass starts. The batch job tracks pending requests when the upstream feed lags behind. The scheduler forwards unmatched records unless an operator intervenes. This component validates unmatched records unless an operator intervenes. The batch job audits expired tokens when the upstream feed lags behind. The platform group audits partial updates once the nightly window closes. The ledger reconciles settled invoices so that downstream consumers see a stable view. The platform group tracks settled invoices before the next reconciliation pass starts.

This component archives pending requests after the configured grace period. The operations team tracks pending requests so that downstream consumers see a stable view. The worker pool archives settled invoices before the next reconciliation pass starts. The worker pool audits settled invoices after the configured grace period. The gateway reconciles unmatched records before the next reconciliation pass starts. The platform group samples unmatched records while the backlog stays below the soft limit. The worker pool tracks partial updates before the next reconciliation pass starts. The cache layer reconciles incoming batches so that downstream consumers see a stable view.

## 12. Risk register

The service samples unmatched records once the nightly window closes. The platform group tracks expired tokens unless an operator intervenes. The review board archives unmatched records when the upstream feed lags behind. The service records scheduled windows when the upstream feed lags behind.

| ID | Risk | Owner | Status |
|-|-|-|-|
| R-001 | Key reviewer is unavailable during cutover (cutover) | Tomas | Closed |
| R-002 | Training material lags behind the build (hypercare) | Tomas | Accepted |
| R-003 | Network change window is shortened (hypercare) | Anders | Closed |
| R-004 | Load test environment differs from production (hypercare) | Yusuf | Mitigated |
| R-005 | Licence renewal lands inside the release window (cutover) | Sofia | Closed |
| R-006 | Supplier delivery slips past the freeze (phase 1) | Rafael | Open |
| R-007 | Licence renewal lands inside the release window (cutover) | Hana | Closed |
| R-008 | Load test environment differs from production (phase 1) | Leila | Closed |
| R-009 | Training material lags behind the build (phase 1) | Kenji | Closed |
| R-010 | Interface contract changes late (cutover) | Ines | Transferred |
| R-011 | Load test environment differs from production (cutover) | Anders | Mitigated |
| R-012 | Interface contract changes late (phase 2) | Ines | Mitigated |
| R-013 | Network change window is shortened (cutover) | Rafael | Open |
| R-014 | Licence renewal lands inside the release window (hypercare) | Elin | Transferred |
| R-015 | Capacity estimate ignores month end peaks (hypercare) | Priya | Mitigated |
| R-016 | Capacity estimate ignores month end peaks (hypercare) | Sofia | Open |
| R-017 | Interface contract changes late (cutover) | Hana | Mitigated |
| R-018 | Load test environment differs from production (phase 1) | Chiara | Open |
| R-019 | Network change window is shortened (phase 1) | Hana | Closed |
| R-020 | Network change window is shortened (cutover) | Sofia | Closed |
| R-021 | Network change window is shortened (hypercare) | Yusuf | Open |
| R-022 | Training material lags behind the build (phase 2) | Marek | Open |
| R-023 | Key reviewer is unavailable during cutover (cutover) | Sofia | Open |
| R-024 | Capacity estimate ignores month end peaks (phase 2) | Yusuf | Mitigated |
| R-025 | Rollback script is untested on the latest schema (phase 2) | Chiara | Mitigated |
| R-026 | Interface contract changes late (cutover) | Priya | Accepted |
| R-027 | Supplier delivery slips past the freeze (phase 2) | Priya | Closed |
| R-028 | Monitoring gaps hide a failing batch (hypercare) | Marek | Mitigated |
| R-029 | Rollback script is untested on the latest schema (cutover) | Priya | Mitigated |
| R-030 | Rollback script is untested on the latest schema (hypercare) | Elin | Mitigated |
| R-031 | Rollback script is untested on the latest schema (phase 1) | Yusuf | Closed |
| R-032 | Audit finding reopens an approved design (cutover) | Yusuf | Transferred |
| R-033 | Audit finding reopens an approved design (cutover) | Anders | Closed |
| R-034 | Supplier delivery slips past the freeze (cutover) | Sofia | Mitigated |
| R-035 | Licence renewal lands inside the release window (phase 2) | Sofia | Transferred |
| R-036 | Load test environment differs from production (hypercare) | Joel | Open |
| R-037 | Key reviewer is unavailable during cutover (phase 2) | Chiara | Open |
| R-038 | Interface contract changes late (phase 2) | Sofia | Closed |
| R-039 | Interface contract changes late (hypercare) | Ines | Open |
| R-040 | Network change window is shortened (cutover) | Elin | Mitigated |
| R-041 | Audit finding reopens an approved design (phase 1) | Leila | Closed |
| R-042 | Load test environment differs from production (hypercare) | Mara | Accepted |

## 13. Appendix A: change log

- As of 2026-05-06, go-live is scheduled for 2026-09-09.
- Effective 2026-05-09, ownership of the project passes to Joel Okafor.
- Effective 2026-05-04, ownership of the project passes to Priya Raman.
