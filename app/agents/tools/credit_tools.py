"""
Credit / NBFC Tools — LangChain @tool wrappers for:
  - Credit eligibility check (vintage, consumption, GST turnover)
  - Credit application submission
  - Existing credit status & line availability
"""
from __future__ import annotations

import json
import logging
import random

from langchain_core.tools import tool

logger = logging.getLogger("credit_tools")


def make_credit_tools(db=None):
    """Returns NBFC/credit tools. db accepted for interface consistency."""

    @tool
    def check_credit_eligibility(
        company_name: str,
        business_vintage_years: float,
        monthly_consumption_mt: float,
        annual_turnover_lakh: float = 0.0,
        gst_registered: bool = True,
    ) -> str:
        """
        Check whether a buyer qualifies for BSB credit / NBFC financing.
        Criteria: business vintage ≥ 2 years, monthly consumption ≥ 5 MT,
        GST registered.

        Args:
            company_name:             Company name of the buyer.
            business_vintage_years:   How many years the business has been operating.
            monthly_consumption_mt:   Average monthly polymer consumption in MT.
            annual_turnover_lakh:     Annual revenue in Lakhs INR (0 if unknown).
            gst_registered:           Whether the company has a valid GSTIN.

        Returns:
            JSON with eligible (bool), credit_limit_est_lakh, reasons, next_steps.
        """
        reasons = []
        eligible = True

        if not gst_registered:
            eligible = False
            reasons.append("GST registration required for credit facility.")

        if business_vintage_years < 2:
            eligible = False
            reasons.append(f"Business vintage {business_vintage_years}yr is below minimum 2 years.")

        if monthly_consumption_mt < 5:
            eligible = False
            reasons.append(f"Monthly consumption {monthly_consumption_mt}MT is below minimum 5 MT.")

        # Estimate credit limit: 2x monthly consumption × avg price ₹120/kg
        credit_limit = 0.0
        if eligible:
            credit_limit = round((monthly_consumption_mt * 1000 * 120 * 2) / 100000, 2)  # in Lakhs
            reasons.append("Preliminary check passed. Full assessment pending document verification.")

        logger.info(f"[Credit] Eligibility for {company_name}: eligible={eligible}")
        return json.dumps({
            "company_name":        company_name,
            "eligible":            eligible,
            "credit_limit_est_lakh": credit_limit,
            "assessment_basis":    "Preliminary — subject to full NBFC underwriting.",
            "reasons":             reasons,
            "next_step": (
                "Submit credit application and KYC documents via BSB App."
                if eligible else
                "Improve eligibility criteria and reapply after 6 months."
            ),
        }, ensure_ascii=False)

    @tool
    def submit_credit_application(
        company_name: str,
        contact_name: str,
        phone: str,
        monthly_consumption_mt: float,
        polymer_type: str = "",
    ) -> str:
        """
        Submit a credit financing application for a buyer. Use this after
        the eligibility check passes and the caller wants to proceed.

        Args:
            company_name:          Buyer's company name.
            contact_name:          Name of the applicant/caller.
            phone:                 Contact phone number.
            monthly_consumption_mt: Monthly polymer requirement in MT.
            polymer_type:          Primary polymer type (PVC, HDPE, PP, etc.).

        Returns:
            JSON with application_id, status, and next steps.
        """
        app_id = f"BSB-CREDIT-{random.randint(100000, 999999)}"
        logger.info(f"[Credit] Application submitted: {app_id} for {company_name}")
        return json.dumps({
            "application_id":  app_id,
            "company_name":    company_name,
            "contact_name":    contact_name,
            "phone":           phone,
            "status":          "Submitted — Under Review",
            "expected_tat":    "3–5 Business Days",
            "next_step": (
                "Upload GST returns (last 2 years), bank statements (6 months), "
                "and PAN card via BSB App. Our NBFC team will call you within 24 hours."
            ),
            "note": f"Application {app_id} received. You will get WhatsApp confirmation shortly.",
        }, ensure_ascii=False)

    @tool
    def get_credit_status(application_id: str) -> str:
        """
        Check the status of an existing credit application or current credit line
        availability for a buyer.

        Args:
            application_id: Credit application ID, e.g. 'BSB-CREDIT-123456'.

        Returns:
            JSON with status, credit_limit_lakh, utilized_lakh, available_lakh.
        """
        # TODO: Replace with real NBFC API integration
        logger.info(f"[Credit] Status check for {application_id}")
        return json.dumps({
            "application_id":    application_id,
            "status":            "Approved",
            "credit_limit_lakh": 25.0,
            "utilized_lakh":     8.5,
            "available_lakh":    16.5,
            "validity":          "31-Mar-2027",
            "note":              "Credit line active. Contact your RM to utilise remaining limit.",
        }, ensure_ascii=False)

    return [check_credit_eligibility, submit_credit_application, get_credit_status]
