"""Explicit block on confirmatory claims from defective protocols.

The v1 and v2 protocols, registrations and evidence are kept unchanged and
remain reproducible. Their outputs are annotated, never rewritten: the
computed `status` stays as history, and `confirmatory_status` says that it
cannot support a confirmatory or economic claim.

Defects (audits/temporal-discovery-v2/SCIENTIFIC-AUDIT.md, commit 01cef30):
the day sign test conditions on non-zero days and can reject a zero-median
null; day blocks assume independence; comparators are not defined at the
same instant; prediction, announced price and execution are conflated.
v1 uses the same tie-discarding day sign test (study.sign_p) with 15-minute
pairing, so it inherits the inferential defect.
"""

AUDIT = 'audits/temporal-discovery-v2/SCIENTIFIC-AUDIT.md'
AUDIT_COMMIT = '01cef30e27d507b3c9ca52696ed273e6747ccda2'
DEFECTS = (
    'sign_test_conditions_on_non_zero_days',
    'day_independence_assumed_without_cluster_inference',
    'comparator_not_defined_at_case_instant',
    'prediction_announced_and_executable_layers_conflated',
)
BLOCKED = {
    '659d7de3788657abfed2b6a07170f16381e3b7acca202084f67a6af18ed98f3a': 'temporal-day-contract-v2',
    '1c49b4d9a4d5bba54a1d7b05c2a4eec821dbb05bd834fcc1f6e2c87fde081041': 'temporal-v1',
}


class ConfirmatoryClaimBlocked(RuntimeError):
    pass


def blocked(protocol_sha256):
    return protocol_sha256 in BLOCKED


def require_confirmatory(protocol_sha256):
    if blocked(protocol_sha256):
        raise ConfirmatoryClaimBlocked(f'{BLOCKED[protocol_sha256]}: confirmatory claims blocked ({AUDIT})')


def notice(protocol_sha256):
    return dict(protocol=BLOCKED[protocol_sha256], protocol_sha256=protocol_sha256, confirmatory_claims='blocked',
                audit=AUDIT, audit_commit=AUDIT_COMMIT, defects=list(DEFECTS),
                history='preserved_unchanged', successor='temporal-risk-set-v3')


def annotate(report):
    """Return a copy with the block made explicit; the original fields remain."""
    digest = report.get('protocol_sha256')
    if not blocked(digest):
        return report
    rows = [dict(row, confirmatory_status='blocked_defective_protocol',
                 status_as_computed=row.get('status')) for row in report.get('investigations', [])]
    return dict(report, investigations=rows, quarantine=notice(digest), confirmatory_claims='blocked')
