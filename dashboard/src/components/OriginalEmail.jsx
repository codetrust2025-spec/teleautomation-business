import React from "react";
import { InlineLoader } from "../Loader.jsx";

/**
 * The original email, read the way mail is read.
 *
 * The body arrives as whatever the recruiter's client sent, which is usually
 * HTML. It is never injected: `dangerouslySetInnerHTML` on a stranger's mail
 * would hand an attacker this screen, and one of these mails is a phishing
 * attempt often enough that it matters. Instead the markup is parsed and a
 * small allowlist is rebuilt as React nodes -- paragraphs, line breaks, bold,
 * italic, lists and links. Everything else contributes its text and nothing
 * else, so a <script>, an onclick or a tracking pixel cannot survive the trip.
 *
 * Nothing here changes what is stored. This is a reading view over the same
 * bytes the pipeline judged.
 */

const INLINE_BOLD = new Set(["B", "STRONG"]);
const INLINE_ITALIC = new Set(["I", "EM"]);
const BLOCK = new Set(["P", "DIV", "TR", "LI", "BLOCKQUOTE", "H1", "H2", "H3", "H4", "H5", "H6"]);
const DROP = new Set(["SCRIPT", "STYLE", "HEAD", "NOSCRIPT", "IFRAME", "OBJECT", "EMBED", "LINK", "META"]);

/** A meeting link shown as what it is, rather than 300 characters of token. */
export function meetingLabel(href) {
  const url = String(href || "");
  if (/teams\.microsoft\.com|teams\.live\.com/i.test(url)) return "Join Microsoft Teams meeting";
  if (/zoom\.us|zoom\.com/i.test(url)) return "Join Zoom meeting";
  if (/meet\.google\.com/i.test(url)) return "Join Google Meet";
  if (/webex\.com/i.test(url)) return "Join Webex meeting";
  return "";
}

/** What to print for a link: a meeting name, else a short, honest hostname. */
export function linkLabel(href, text) {
  const meeting = meetingLabel(href);
  if (meeting) return meeting;
  const shown = String(text || "").trim();
  // A label that is just the URL again helps nobody once it passes a line.
  if (shown && shown !== String(href) && shown.length <= 80) return shown;
  try {
    const url = new URL(String(href));
    const path = url.pathname.length > 1 ? url.pathname : "";
    const whole = `${url.hostname}${path}`;
    return whole.length <= 60 ? whole : `${whole.slice(0, 57)}…`;
  } catch {
    return shown || String(href);
  }
}

const URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+/gi;
const MAIL_RE = /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/gi;
// Indian mobile numbers as recruiters write them, and ordinary +country forms.
const PHONE_RE = /(?:\+\d{1,3}[\s-]?)?\b\d{5}[\s-]?\d{5}\b|\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,5}/g;

function anchor(href, label, key) {
  return <a key={key} href={href} target="_blank" rel="noopener noreferrer nofollow">{label}</a>;
}

/** Turn bare URLs, addresses and phone numbers in plain text into links. */
export function linkifyText(text, keyPrefix = "t") {
  const source = String(text ?? "");
  if (!source) return [];
  const marks = [];
  for (const [re, kind] of [[URL_RE, "url"], [MAIL_RE, "mail"], [PHONE_RE, "phone"]]) {
    re.lastIndex = 0;
    for (let m = re.exec(source); m; m = re.exec(source)) {
      // First match wins, so an address inside a URL is not linked twice.
      if (marks.some((mark) => m.index < mark.end && mark.start < m.index + m[0].length)) continue;
      marks.push({ start: m.index, end: m.index + m[0].length, value: m[0], kind });
    }
  }
  marks.sort((a, b) => a.start - b.start);
  const out = [];
  let at = 0;
  marks.forEach((mark, index) => {
    if (mark.start > at) out.push(source.slice(at, mark.start));
    const key = `${keyPrefix}-${index}`;
    if (mark.kind === "url") out.push(anchor(mark.value, linkLabel(mark.value, mark.value), key));
    else if (mark.kind === "mail") out.push(anchor(`mailto:${mark.value}`, mark.value, key));
    else out.push(anchor(`tel:${mark.value.replace(/[\s-]/g, "")}`, mark.value, key));
    at = mark.end;
  });
  if (at < source.length) out.push(source.slice(at));
  return out;
}

function inlineNodes(node, key) {
  if (node.nodeType === 3) return linkifyText(node.nodeValue, key);
  if (node.nodeType !== 1) return [];
  const tag = node.tagName;
  if (DROP.has(tag)) return [];
  if (tag === "BR") return [<br key={key} />];
  const children = [...node.childNodes].flatMap((child, index) => inlineNodes(child, `${key}-${index}`));
  if (!children.length) return [];
  if (INLINE_BOLD.has(tag)) return [<strong key={key}>{children}</strong>];
  if (INLINE_ITALIC.has(tag)) return [<em key={key}>{children}</em>];
  if (tag === "A") {
    const href = node.getAttribute("href") || "";
    // The href is passed through untouched; only what the reader sees changes.
    if (!/^(https?:|mailto:|tel:)/i.test(href)) return children;
    return [anchor(href, linkLabel(href, node.textContent), key)];
  }
  return children;
}

/** Split an HTML body into paragraph-level React children. */
function htmlParagraphs(html) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  parsed.querySelectorAll([...DROP].join(",")).forEach((node) => node.remove());
  const blocks = [];
  const walk = (node, key) => {
    [...node.childNodes].forEach((child, index) => {
      const childKey = `${key}-${index}`;
      if (child.nodeType === 1 && BLOCK.has(child.tagName)
          && [...child.childNodes].some((n) => n.nodeType === 1 && BLOCK.has(n.tagName))) {
        walk(child, childKey);
        return;
      }
      const nodes = inlineNodes(child, childKey);
      if (nodes.length && String(child.textContent || "").trim()) blocks.push({ key: childKey, nodes });
    });
  };
  walk(parsed.body, "b");
  return blocks;
}

/** Split a plain-text body on blank lines, keeping single breaks inside. */
function textParagraphs(text) {
  return String(text)
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block, index) => ({
      key: `p-${index}`,
      nodes: block.split("\n").flatMap((line, lineIndex, all) => [
        ...linkifyText(line, `p-${index}-${lineIndex}`),
        ...(lineIndex < all.length - 1 ? [<br key={`p-${index}-${lineIndex}-br`} />] : []),
      ]),
    }));
}

export function emailParagraphs(body) {
  const source = String(body || "");
  if (!source.trim()) return [];
  const isHtml = /<[a-z][\s\S]*>/i.test(source) && typeof DOMParser !== "undefined";
  return isHtml ? htmlParagraphs(source) : textParagraphs(source);
}

const initial = (value) => String(value || "?").trim().charAt(0).toUpperCase() || "?";

export function OriginalEmail({
  email, loading, error, fallbackSubject, fallbackRecipient, fallbackReceivedAt, formatWhen,
}) {
  if (loading) return <InlineLoader label="Loading original email…" />;
  if (!email) return <p className="gmail-view__empty">{error || "The original email body is unavailable."}</p>;

  const sender = email.sender_name || email.sender_email || "Unknown sender";
  const subject = email.subject || fallbackSubject || "(no subject)";
  const recipient = email.recipient_email || fallbackRecipient || "";
  const received = formatWhen(email.sent_at || fallbackReceivedAt);
  const paragraphs = emailParagraphs(email.body);

  return <article className="gmail-view" aria-label="Original email">
    <h4 className="gmail-view__subject">{subject}</h4>
    <header className="gmail-view__from">
      <span className="gmail-view__avatar" aria-hidden="true">{initial(sender)}</span>
      <span className="gmail-view__identity">
        <span className="gmail-view__sender">{sender}</span>
        <span className="gmail-view__recipient">to {recipient || "me"}</span>
      </span>
      <time className="gmail-view__received">{received}</time>
    </header>
    <div className="gmail-view__body">
      {paragraphs.length
        ? paragraphs.map(({ key, nodes }) => <p key={key}>{nodes}</p>)
        : <p className="gmail-view__empty">This email has no text body.</p>}
    </div>
  </article>;
}
