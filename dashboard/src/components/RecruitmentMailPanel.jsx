export { default as RecruitmentMailPanel } from "./RecruitmentMailPanelRedesign.jsx";

// Read-only compatibility predicate for retained historical evidence. The old
// operator approval component is retired; the active panel is automated.
const IMPORTANT_STATUSES = [
  "SELECTED",
  "FINAL_SELECTION_CONFIRMED",
  "OFFER_INDICATION",
  "OFFER_IN_PROGRESS",
  "OFFER_APPROVED",
  "OFFER_LETTER_RECEIVED",
  "APPOINTMENT_LETTER_RECEIVED",
  "OFFER_ACCEPTED",
  "JOINING_CONFIRMED",
  "JOINED",
  "POST_SELECTION_ONBOARDING",
  "MANUAL_REVIEW_REQUIRED",
];
const HIDDEN_STATUSES = new Set([
  "IGNORED_NOT_OFFER_RELATED",
  "IGNORED_LOW_CONFIDENCE",
  "NO_RELEVANT_STATUS",
]);
const HIDDEN_REVIEWS = new Set(["IGNORED", "FALSE_POSITIVE", "DUPLICATE"]);
const IMPORTANT_EVIDENCE_MEANINGS = new Set(
  IMPORTANT_STATUSES.filter((status) => status !== "MANUAL_REVIEW_REQUIRED"),
);
export function shouldShowInSelectionOfferReview(event) {
  const status = String(event?.primary_status || "").toUpperCase();
  const review = String(event?.review_status || "").toUpperCase();
  const evidence = event?.structured_result?.evidence || [];
  if (!IMPORTANT_STATUSES.includes(status) || HIDDEN_STATUSES.has(status))
    return false;
  if (HIDDEN_REVIEWS.has(review) || event?.visible_in_offer_review === false)
    return false;
  if (Number(event?.confidence || 0) < 0.8 || evidence.length === 0)
    return false;
  if (status === "MANUAL_REVIEW_REQUIRED") {
    if (event?.structured_result?.is_selection_or_offer_related !== true)
      return false;
    if (
      !evidence.some((item) =>
        IMPORTANT_EVIDENCE_MEANINGS.has(
          String(item?.meaning || "").toUpperCase(),
        ),
      )
    )
      return false;
  }
  return true;
}
