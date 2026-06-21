package com.tentoftrials.compliance;

import java.util.Arrays;
import java.util.Collections;

public class ComplianceAuditorJsonTest {
    public static void main(String[] args) {
        testPassReportHasDeterministicEmptyFindings();
        testFailReportIncludesFindings();
        testJsonEscaping();
        System.out.println("ComplianceAuditorJsonTest passed");
    }

    private static void testPassReportHasDeterministicEmptyFindings() {
        ComplianceAuditor.ComplianceResult result =
            new ComplianceAuditor.ComplianceResult(true, Collections.emptyList(), "KYC check passed");

        String json = ComplianceAuditor.formatJsonReport("KYC", result);

        assertContains(json, "\"check_type\":\"KYC\"");
        assertContains(json, "\"status\":\"pass\"");
        assertContains(json, "\"compliant\":true");
        assertContains(json, "\"findings\":[]");
    }

    private static void testFailReportIncludesFindings() {
        ComplianceAuditor.ComplianceResult result =
            new ComplianceAuditor.ComplianceResult(
                false,
                Arrays.asList("Missing KYC file", "Manual review required"),
                "KYC check failed"
            );

        String json = ComplianceAuditor.formatJsonReport("KYC", result);

        assertContains(json, "\"status\":\"fail\"");
        assertContains(json, "\"compliant\":false");
        assertContains(json, "\"rule_id\":\"compliance.kyc.1\"");
        assertContains(json, "\"rule_id\":\"compliance.kyc.2\"");
        assertContains(json, "\"severity\":\"error\"");
        assertContains(json, "\"path\":\"compliance/ComplianceAuditor.java\"");
        assertContains(json, "\"message\":\"Missing KYC file\"");
        assertContains(json, "\"remediation\":\"Complete required identity checks before approving the user.\"");
    }

    private static void testJsonEscaping() {
        ComplianceAuditor.ComplianceResult result =
            new ComplianceAuditor.ComplianceResult(
                false,
                Collections.singletonList("Quote \" and newline\n are escaped"),
                "Summary with tab\t"
            );

        String json = ComplianceAuditor.formatJsonReport("AML", result);

        assertContains(json, "Quote \\\" and newline\\n are escaped");
        assertContains(json, "Summary with tab\\t");
        assertContains(json, "\"remediation\":\"Escalate the transaction for AML review before settlement.\"");
    }

    private static void assertContains(String text, String expected) {
        if (!text.contains(expected)) {
            throw new AssertionError("Expected JSON to contain: " + expected + "\nActual: " + text);
        }
    }
}
