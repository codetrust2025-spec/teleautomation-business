"""Deep scan candidate emails and backfill genuine missed notifications."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from core.db.connection import get_connection
from core import recruitment_mail_store as store
from services.recruitment_semantics import (
    classify_context,
    validate_lifecycle_event,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scan_backfill")


def is_job_portal_or_newsletter_or_noise(subject: str, body: str, sender_email: str, sender_name: str) -> bool:
    """Extra strict check to exclude all portal digests, marketing, courses, etc."""
    combined = f"{subject}\n{body}".casefold()
    sender = f"{sender_name} {sender_email}".casefold()

    portal_domains = (
        "naukri.com", "foundit.in", "monsterindia.com", "indeed.com", "shine.com",
        "timesjobs.com", "ambitionbox.com", "glassdoor.com", "linkedin.com",
        "internshala.com", "unstop.com", "instahyre.com", "hirist.com",
        "topmate.io", "namastedev.com", "scaler.com", "upgrad.com", "simplilearn.com",
        "udemy.com", "coursera.com", "geeksforgeeks.org", "leetcode.com",
        "hackerrank.com", "hackerearth.com", "medium.com", "quora.com",
        "github.com", "gitlab.com", "atlassian.net", "substack.com",
        "mailer.daffodilsw.com", "notifications.freelancer.com", "upwork.com",
        "google.com", "googlemail.com", "e.google.com"
    )
    for domain in portal_domains:
        if domain in sender:
            # If it's a portal domain, only allow if it's an explicit offer letter or BGV directly addressed
            if not any(token in combined for token in ("offer letter attached", "we are pleased to offer you", "formal offer of employment", "appointment letter", "invitation - digital employment")):
                return True

    noise_patterns = (
        r"\bjob alert\b", r"\brecommended jobs?\b", r"\bjobs? for you\b",
        r"\bjobs? matching your profile\b", r"\bapply to\b", r"\bopenings? for\b",
        r"\bhiring alert\b", r"\bnewsletter\b", r"\bunsubscribe\b",
        r"\bview job\b", r"\bapply now\b", r"\bcheck out these jobs\b",
        r"\bcourse\b", r"\bwebinar\b", r"\bworkshop\b", r"\bmasterclass\b",
        r"\bbatch starting\b", r"\benroll now\b", r"\blearn react\b",
        r"\blearn python\b", r"\bsalary insights\b", r"\bcompany reviews\b",
        r"\binterview questions of\b", r"\bhow to crack\b",
        r"\bwork permit\b", r"\bpr visa\b", r"\bvisa assistance\b",
        r"\bgovernment tender\b", r"\bemd-free\b", r"\bcareer gap\b",
        r"\bexperience letters from\b", r"\bintroducing gemini\b",
        r"\bthanks for submitting your application\b",
        r"\bapplication has been physically reviewed\b",
        r"\btime to schedule your interview\b",
        r"\byour availability required\b",
        r"\bwe’re looking forward to your interview\b",
        r"\bwe're looking forward to your interview\b",
        r"\binterview has been successfully scheduled\b",
        r"\binterview confirmation mail\b",
        r"\binterview link\b",
        r"\btalview:\b",
        r"\bimportant update about your.*application\b",
        r"\bnext steps for your.*application\b",
    )
    # If the mail matches promotional patterns and lacks strong direct outcome phrases:
    strong_outcome = any(token in combined for token in (
        "we are pleased to offer", "pleased to extend an offer", "selected for the position",
        "selected for the role", "cleared the final round", "successfully cleared",
        "minimal documents required for offer", "digital employment bgv", "digiverifier",
        "joining date is confirmed", "your joining date is", "offer letter inside",
        "congratulations, you're in!", "capgemini documenation", "bgv_rh"
    ))
    if not strong_outcome:
        for pat in noise_patterns:
            if re.search(pat, combined, re.I):
                return True

    return False


def run_scan_and_backfill(dry_run: bool = True):
    with get_connection() as conn, conn.cursor() as cur:
        # 1. Fetch all mailboxes
        cur.execute("""
            SELECT m.id, m.candidate_id, m.email_address,
                   COALESCE(c.payload->>'name', l_c.payload->>'name', m.email_address) as candidate_name,
                   COALESCE(c.payload->>'phone', l_c.payload->>'phone') as candidate_phone
            FROM candidate_mailboxes m
            LEFT JOIN candidates_store c ON c.id = m.candidate_id
            LEFT JOIN candidate_identity_links l ON l.alias_candidate_id = m.candidate_id
            LEFT JOIN candidates_store l_c ON l_c.id = l.canonical_candidate_id
            WHERE m.connection_status = 'CONNECTED' OR m.monitoring_enabled = true
            ORDER BY m.updated_at DESC
        """)
        mailboxes = cur.fetchall()
        logger.info("Found %d active candidate mailboxes", len(mailboxes))

        summary_report = {
            "scanned_mailboxes": len(mailboxes),
            "total_messages": 0,
            "detected_outcomes": 0,
            "backfilled_notifications": 0,
            "candidates": {},
        }

        for m_id, cand_id, email, cand_name, cand_phone in mailboxes:
            cur.execute("""
                SELECT id, provider_message_id, provider_thread_id, subject, sender_name, sender_email,
                       recipient_email, sent_at, body_text, html_body_text
                FROM mailbox_messages
                WHERE mailbox_id = %s
                ORDER BY sent_at DESC
            """, (m_id,))
            messages = cur.fetchall()
            summary_report["total_messages"] += len(messages)
            
            cand_key = f"{cand_name or 'Unknown'} ({email})"
            summary_report["candidates"][cand_key] = {
                "candidate_id": cand_id,
                "email": email,
                "messages_count": len(messages),
                "outcomes": [],
            }

            for msg_id, p_msg_id, thread_id, subject, s_name, s_email, r_email, sent_at, b_text, h_text in messages:
                body = b_text or h_text or ""
                
                # Check for job portals / noise
                if is_job_portal_or_newsletter_or_noise(str(subject or ""), str(body or ""), str(s_email or ""), str(s_name or "")):
                    continue

                # Load attachments
                cur.execute("""
                    SELECT a.filename, a.attachment_type, c.extracted_text
                    FROM mailbox_attachments a
                    LEFT JOIN mailbox_attachment_cache c ON c.checksum = a.checksum
                    WHERE a.mailbox_message_id = %s
                """, (msg_id,))
                attachments = [{"filename": r[0], "attachment_type": r[1], "text": r[2] or ""} for r in cur.fetchall()]

                context = classify_context(
                    str(subject or ""),
                    str(body or ""),
                    sender_email=str(s_email or ""),
                    sent_at=sent_at,
                    attachments=attachments,
                )

                lifecycle = context.get("lifecycle_event")
                intent = context.get("email_intent")
                
                # Target outcome families:
                # 1. Selected (SELECTED, FINAL_SELECTION_CONFIRMED)
                # 2. Offer Received (OFFER_LETTER_RECEIVED, OFFER_RECEIVED, OFFER_INDICATION, OFFER_APPROVED, OFFER_IN_PROGRESS)
                # 3. Final Round Cleared (FINAL_ROUND_CLEARED)
                # 4. HR Confirmation (HR_CONFIRMATION, DOCUMENT_VERIFICATION, COMPENSATION_CONFIRMATION)
                # 5. Joining Confirmed (JOINING_CONFIRMED, BACKGROUND_VERIFICATION, POST_SELECTION_ONBOARDING, JOINED)
                target_lifecycles = {
                    "SELECTED", "FINAL_SELECTION_CONFIRMED",
                    "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED", "OFFER_INDICATION", "OFFER_APPROVED", "OFFER_IN_PROGRESS",
                    "FINAL_ROUND_CLEARED",
                    "HR_CONFIRMATION", "DOCUMENT_VERIFICATION", "COMPENSATION_CONFIRMATION",
                    "JOINING_CONFIRMED", "BACKGROUND_VERIFICATION", "POST_SELECTION_ONBOARDING", "JOINED",
                }

                if lifecycle not in target_lifecycles:
                    continue

                canonical_cls = store.canonical_classification(status=lifecycle)
                cand_status = store._CLASSIFICATION_STATUS.get(canonical_cls, "Selected")

                # Check if notification already exists
                cur.execute("""
                    SELECT id, classification, candidate_status, is_reviewed, is_false_detection, dismissed_at
                    FROM mail_monitoring_notifications
                    WHERE gmail_message_id = %s AND classification = %s
                """, (p_msg_id, canonical_cls))
                existing_notif = cur.fetchone()

                # Check existing event
                cur.execute("""
                    SELECT id, primary_status, classification, validation_status, review_status
                    FROM ai_recruitment_events
                    WHERE mailbox_message_id = %s
                """, (msg_id,))
                existing_event = cur.fetchone()

                summary_report["detected_outcomes"] += 1
                outcome_entry = {
                    "msg_id": msg_id,
                    "provider_msg_id": p_msg_id,
                    "subject": subject,
                    "sender": f"{s_name} <{s_email}>",
                    "sent_at": str(sent_at),
                    "lifecycle": lifecycle,
                    "canonical_classification": canonical_cls,
                    "candidate_status": cand_status,
                    "evidence_summary": context.get("evidence_summary"),
                    "has_existing_notif": existing_notif is not None,
                    "has_existing_event": existing_event is not None,
                }
                summary_report["candidates"][cand_key]["outcomes"].append(outcome_entry)

                logger.info(
                    "Detected outcome: [%s -> %s] for %s (%s) | %s | %s",
                    lifecycle, canonical_cls, cand_name, email, str(subject)[:50], str(sent_at)[:10]
                )

                if not dry_run:
                    # Construct structured result
                    event_id = existing_event[0] if existing_event else store._id()
                    notif_id = existing_notif[0] if existing_notif else store._id()
                    
                    structured = {
                        "schema_version": "selection_offer_event_v1",
                        "is_recruitment_related": True,
                        "is_selection_or_offer_related": True,
                        "status": lifecycle,
                        "primary_status": lifecycle,
                        "classification": canonical_cls,
                        "candidate_status": cand_status,
                        "confidence": 0.95,
                        "summary": context.get("evidence_summary") or subject,
                        "reason": f"Deterministic safety classifier verified genuine recruitment outcome {lifecycle}.",
                        "recommended_action": f"Track candidate status as {cand_status}.",
                        "classification_source": "DETERMINISTIC_SAFETY_SCAN",
                        "validation_status": "AUTO_VALIDATED",
                        "ai_status": "ANALYZED",
                        "email_intent": intent,
                        "document_type": context.get("document_type", "NONE"),
                        "lifecycle_event": lifecycle,
                        "evidence": [{"field": "status", "text": context.get("evidence_summary") or subject, "meaning": lifecycle}],
                    }

                    # Create or update event
                    if not existing_event:
                        cur.execute("""
                            INSERT INTO ai_recruitment_events(
                                id, candidate_id, canonical_candidate_id, mailbox_message_id, primary_status,
                                classification, candidate_status, confidence, structured_result, summary,
                                requires_manual_review, review_status, visible_in_offer_review,
                                validation_status, ai_status, email_intent, document_type,
                                evidence_summary, event_fingerprint, ai_model, prompt_name,
                                prompt_version, schema_version, created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s::jsonb, %s,
                                false, 'AUTO_VALIDATED', true,
                                'AUTO_VALIDATED', 'ANALYZED', %s, %s,
                                %s, %s, 'deterministic_v4', 'recruitment_email_status_extraction_v3',
                                'v4', 'selection_offer_event_v1', %s, now()
                            )
                            RETURNING id
                        """, (
                            event_id, cand_id, cand_id, msg_id, lifecycle,
                            canonical_cls, cand_status, 0.95, json.dumps(structured), structured["summary"],
                            intent, context.get("document_type", "NONE"),
                            structured["summary"], p_msg_id, sent_at or store.now()
                        ))
                    else:
                        cur.execute("""
                            UPDATE ai_recruitment_events SET
                                primary_status = %s,
                                classification = %s,
                                candidate_status = %s,
                                confidence = GREATEST(confidence, 0.95),
                                structured_result = %s::jsonb,
                                summary = %s,
                                review_status = 'AUTO_VALIDATED',
                                validation_status = 'AUTO_VALIDATED',
                                visible_in_offer_review = true,
                                ignored_at = NULL,
                                ignore_reason = NULL,
                                updated_at = now()
                            WHERE id = %s
                        """, (
                            lifecycle, canonical_cls, cand_status,
                            json.dumps(structured), structured["summary"],
                            event_id
                        ))

                    # Create analysis record
                    cur.execute("""
                        INSERT INTO mail_ai_analyses(
                            id, mailbox_message_id, candidate_id, model_name, model_version,
                            classification, candidate_status, confidence, summary, reason,
                            recommended_action, raw_ai_response, validated_response,
                            processing_status, ai_status, validation_status, email_intent,
                            document_type, evidence_summary, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, 'deterministic_v4', 'v4',
                            %s, %s, 0.95, %s, %s,
                            %s, %s::jsonb, %s::jsonb,
                            'CLASSIFIED', 'ANALYZED', 'AUTO_VALIDATED', %s,
                            %s, %s, %s, now()
                        )
                        ON CONFLICT(mailbox_message_id) DO UPDATE SET
                            classification = EXCLUDED.classification,
                            candidate_status = EXCLUDED.candidate_status,
                            confidence = GREATEST(mail_ai_analyses.confidence, EXCLUDED.confidence),
                            summary = EXCLUDED.summary,
                            validated_response = EXCLUDED.validated_response,
                            validation_status = 'AUTO_VALIDATED',
                            processing_status = 'CLASSIFIED',
                            updated_at = now()
                        RETURNING id
                    """, (
                        store._id(), msg_id, cand_id,
                        canonical_cls, cand_status, structured["summary"][:1000], structured["reason"][:1000],
                        structured["recommended_action"][:1000], json.dumps(structured), json.dumps(structured),
                        intent, context.get("document_type", "NONE"), structured["summary"], sent_at or store.now()
                    ))
                    analysis_row = cur.fetchone()
                    analysis_id = analysis_row[0] if analysis_row else store._id()

                    # Create notification if needed
                    priority = store.notification_priority(canonical_cls, confidence=0.95, requires_review=False)
                    cur.execute("""
                        INSERT INTO mail_monitoring_notifications(
                            id, candidate_id, candidate_name, candidate_email,
                            gmail_account_id, gmail_message_id, gmail_thread_id,
                            email_analysis_id, ai_recruitment_event_id,
                            classification, candidate_status, company_name, job_role,
                            email_subject, sender_name, sender_email, email_received_at,
                            ai_confidence, ai_summary, ai_reason, recommended_action,
                            priority, is_read, is_reviewed, is_false_detection, dismissed_at,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s, NULL, NULL,
                            %s, %s, %s, %s,
                            0.95, %s, %s, %s,
                            %s, false, false, false, NULL,
                            %s, now()
                        )
                        ON CONFLICT(gmail_message_id, classification) DO UPDATE SET
                            candidate_status = EXCLUDED.candidate_status,
                            ai_summary = EXCLUDED.ai_summary,
                            priority = EXCLUDED.priority,
                            dismissed_at = NULL,
                            is_false_detection = false,
                            updated_at = now()
                    """, (
                        notif_id, cand_id, cand_name, email or r_email,
                        m_id, p_msg_id, thread_id,
                        analysis_id, event_id,
                        canonical_cls, cand_status,
                        subject, s_name, s_email, sent_at,
                        structured["summary"][:1000], structured["reason"][:1000], structured["recommended_action"][:1000],
                        priority, sent_at or store.now()
                    ))

                    # Update candidate_job_status
                    rank = store._STATUS_RANK.get(cand_status, 40)
                    cur.execute("""
                        INSERT INTO candidate_job_status(
                            candidate_id, status, status_rank, source, source_id,
                            gmail_message_id, classification, confidence, validation_status, updated_at
                        ) VALUES (
                            %s, %s, %s, 'AI Mail Monitoring', %s,
                            %s, %s, 0.95, 'APPROVED', now()
                        )
                        ON CONFLICT(candidate_id) DO UPDATE SET
                            status = CASE WHEN EXCLUDED.status_rank >= candidate_job_status.status_rank THEN EXCLUDED.status ELSE candidate_job_status.status END,
                            status_rank = GREATEST(candidate_job_status.status_rank, EXCLUDED.status_rank),
                            classification = CASE WHEN EXCLUDED.status_rank >= candidate_job_status.status_rank THEN EXCLUDED.classification ELSE candidate_job_status.classification END,
                            confidence = GREATEST(candidate_job_status.confidence, EXCLUDED.confidence),
                            validation_status = 'APPROVED',
                            updated_at = now()
                    """, (
                        cand_id, cand_status, rank, event_id,
                        p_msg_id, canonical_cls
                    ))

                    summary_report["backfilled_notifications"] += 1

        return summary_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and backfill candidate recruitment mails.")
    parser.add_argument("--backfill", action="store_true", help="Execute backfill against the database (otherwise dry run).")
    args = parser.parse_args()

    report = run_scan_and_backfill(dry_run=not args.backfill)
    out_file = "backfill_report.json" if args.backfill else "scan_report.json"
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n================ SUMMARY ================")
    print(f"Scanned mailboxes:       {report['scanned_mailboxes']}")
    print(f"Total messages scanned:  {report['total_messages']}")
    print(f"Detected valid outcomes: {report['detected_outcomes']}")
    if args.backfill:
        print(f"Backfilled notifications: {report['backfilled_notifications']}")
    print(f"Saved full report to:    {out_file}")
