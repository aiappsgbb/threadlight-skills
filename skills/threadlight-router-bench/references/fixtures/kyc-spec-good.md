# KYC/AML Onboarding Spec
Retention: AML records kept 5-7 years; reconciled against GDPR right-to-erasure via legal-hold exemption.
Beneficial owners: identify all >=25% owners (10% threshold for high-risk EU states); nested entity ownership resolved recursively.
SAR: filed within 30 days; tipping-off prohibited - the customer is never notified of a SAR.
CTR: cash >= $10,000 filed; structuring detection aggregates split transactions under the threshold.
EDD: high-risk escalates to Enhanced Due Diligence requiring senior-approval gate.
Jurisdiction: US (BSA/FinCEN) and EU (AMLD) thresholds handled separately, not hard-coded.
