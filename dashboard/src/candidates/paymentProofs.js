const PROOF_COLLECTION_KEYS = [
  "payment_proofs",
  "paymentProofs",
  "paymentScreenshots",
  "payment_screenshots",
  "proofImages",
  "proof_images",
  "paymentAttachments",
  "payment_attachments",
  "screenshot_urls",
  "paymentEvidence",
  "payment_evidence",
  "transactionProofs",
  "transaction_proofs",
  "proofs",
  "screenshots",
];

const CONTAINER_KEYS = [
  "candidate",
  "data",
  "result",
  "response",
  "payment",
  "payments",
  "transaction",
  "transactions",
];

const URL_KEYS = [
  "url",
  "fileUrl",
  "file_url",
  "imageUrl",
  "image_url",
  "screenshotUrl",
  "screenshot_url",
  "path",
  "file_path",
  "storagePath",
  "storage_path",
  "attachmentUrl",
  "attachment_url",
  "signedUrl",
  "signed_url",
  "downloadUrl",
  "download_url",
];

function firstValue(source, keys) {
  for (const key of keys) {
    const value = source?.[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function isDeleted(proof) {
  const status = String(proof?.status || proof?.state || "").trim().toLowerCase();
  return Boolean(
    proof?.deleted ||
      proof?.is_deleted ||
      proof?.isDeleted ||
      proof?.deleted_at ||
      proof?.deletedAt ||
      ["deleted", "removed"].includes(status),
  );
}

function looksLikePaymentAttachment(proof) {
  const type = String(
    proof?.attachment_type || proof?.attachmentType || proof?.type || proof?.kind || "",
  )
    .trim()
    .toLowerCase();
  if (!type) return true;
  return [
    "payment_proof",
    "payment-proof",
    "payment proof",
    "payment_receipt",
    "payment-receipt",
    "receipt",
  ].includes(type);
}

function normalizeUrl(value) {
  const url = String(value || "").trim().replaceAll("\\", "/");
  if (!url) return "";
  return url;
}

function normalizeOne(raw, inherited = {}) {
  if (typeof raw === "string") raw = { url: raw };
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || isDeleted(raw)) {
    return null;
  }
  if (!looksLikePaymentAttachment(raw)) return null;

  const url = normalizeUrl(firstValue(raw, URL_KEYS));
  const id = String(
    firstValue(raw, ["id", "proof_id", "proofId", "attachment_id", "attachmentId"]) ||
      url,
  ).trim();
  if (!id && !url) return null;

  return {
    ...raw,
    id,
    candidateId: String(
      firstValue(raw, ["candidateId", "candidate_id"]) ||
        inherited.candidateId ||
        "",
    ),
    candidate_id: String(
      firstValue(raw, ["candidate_id", "candidateId"]) ||
        inherited.candidateId ||
        "",
    ),
    paymentId: String(
      firstValue(raw, ["paymentId", "payment_id", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    ),
    payment_id: String(
      firstValue(raw, ["payment_id", "paymentId", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    ),
    url,
    uploadedAt: firstValue(raw, [
      "uploadedAt",
      "uploaded_at",
      "createdAt",
      "created_at",
      "date",
    ]),
    uploaded_at: firstValue(raw, [
      "uploaded_at",
      "uploadedAt",
      "created_at",
      "createdAt",
      "date",
    ]),
    amount: Number(firstValue(raw, ["amount", "verified_amount", "payment_amount"])) || 0,
    utr: String(firstValue(raw, ["utr", "utr_number", "utrNumber"]) || ""),
    status: String(firstValue(raw, ["status", "payment_status"]) || ""),
  };
}

/**
 * Convert current and legacy candidate/payment response shapes into one
 * canonical payment-proof list. This is the only source for table counts and
 * modal previews.
 */
export function normalizePaymentProofs(source) {
  const candidates = [];
  const visited = new Set();

  function collect(value, inherited = {}, directArray = false) {
    if (value == null) return;
    if (Array.isArray(value)) {
      if (directArray) {
        for (const item of value) candidates.push([item, inherited]);
      } else {
        for (const item of value) collect(item, inherited, false);
      }
      return;
    }
    if (typeof value !== "object" || visited.has(value)) return;
    visited.add(value);

    const nextInherited = {
      candidateId:
        firstValue(value, ["candidateId", "candidate_id"]) ||
        inherited.candidateId ||
        "",
      paymentId:
        firstValue(value, ["paymentId", "payment_id", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    };

    if (
      !directArray &&
      URL_KEYS.some((key) => value[key]) &&
      looksLikePaymentAttachment(value)
    ) {
      candidates.push([value, nextInherited]);
    }
    for (const key of PROOF_COLLECTION_KEYS) {
      if (Array.isArray(value[key])) collect(value[key], nextInherited, true);
    }
    if (Array.isArray(value.attachments)) {
      for (const attachment of value.attachments) {
        if (looksLikePaymentAttachment(attachment)) {
          candidates.push([attachment, nextInherited]);
        }
      }
    }
    for (const key of CONTAINER_KEYS) {
      if (value[key] !== undefined) collect(value[key], nextInherited, false);
    }
  }

  if (Array.isArray(source)) collect(source, {}, true);
  else collect(source);

  const result = [];
  const seen = new Set();
  for (const [raw, inherited] of candidates) {
    const proof = normalizeOne(raw, inherited);
    if (!proof) continue;
    const key = proof.id ? `id:${proof.id}` : `url:${proof.url.toLowerCase()}`;
    const urlKey = proof.url ? `url:${proof.url.toLowerCase()}` : "";
    if (seen.has(key) || (urlKey && seen.has(urlKey))) continue;
    seen.add(key);
    if (urlKey) seen.add(urlKey);
    result.push(proof);
  }
  return result;
}
