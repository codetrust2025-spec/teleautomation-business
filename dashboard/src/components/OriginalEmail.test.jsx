import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { OriginalEmail, emailParagraphs, linkLabel, meetingLabel } from "./OriginalEmail.jsx";

const TEAMS = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_NzIxMGU1YWMtMGU2NC00ZDk1LTIjZTEtZDNiOTc2MmVkODI5%40thread.v2/0?context=%7b%22Tid%22%3a%22404b1967-6507-45ab-8a6d-7374a3f478be%22%2c%22Oid%22%3a%221b922d1c-ee13-4d54-81f2-31e c3c111a81%22%7d";

// The VHS Professional Services mail, as the pipeline stores it.
const BODY = [
  "<p>Dear Gangadhar,</p>",
  "<p>As discussed, your Virtual Interview is scheduled at <b>3.30 PM &#8211; 4.15 PM on 15<sup>th</sup> Sep 2026 (Tuesday)</b>.<br>Please join the link before 5 minutes.</p>",
  "<p><b>Interview Scheduled</b><br><b>15<sup>th</sup> Sep 2026 (Tuesday)</b> at 3.30 PM.</p>",
  "<p>Interview link<br>", `<a href="${TEAMS}">${TEAMS}</a></p>`,
  "<p>Thanks &amp; Regards<br>Prathima Kamisetti<br>HR Technical Recruiter || 9845303472<br>",
  "VHS Professional Services Pvt ltd<br>3/1, Langford Road, Shantinagar, Richmond Town, Bangalore 560025.</p>",
].join("");

const email = {
  subject: "Virtual Technical Interview Scheduled_TCS",
  sender_name: "prathima.kamisetti",
  sender_email: "prathima.kamisetti@vhsps.example",
  recipient_email: "gangadhar.nagaraje.it@gmail.com",
  sent_at: "2026-09-11T07:22:00Z",
  body: BODY,
};

function view(overrides = {}) {
  return render(<OriginalEmail
    email={{ ...email, ...overrides }}
    formatWhen={() => "11 Sept 2026, 12:52 pm"}
  />);
}

describe("original email", () => {
  afterEach(cleanup);

  describe("the header reads like an email", () => {
    it("leads with the subject", () => {
      view();
      expect(screen.getByRole("heading", { name: email.subject })).toBeInTheDocument();
    });

    it("shows the sender, who it was to, and when it arrived", () => {
      const { container } = view();
      expect(screen.getByText("prathima.kamisetti")).toBeInTheDocument();
      expect(screen.getByText(`to ${email.recipient_email}`)).toBeInTheDocument();
      expect(screen.getByText("11 Sept 2026, 12:52 pm")).toBeInTheDocument();
      expect(container.querySelector(".gmail-view__avatar").textContent).toBe("P");
    });

    it("says 'to me' when the recipient is unknown", () => {
      render(<OriginalEmail email={{ ...email, recipient_email: "" }} formatWhen={() => "now"} />);
      expect(screen.getByText("to me")).toBeInTheDocument();
    });
  });

  describe("the body keeps what the sender wrote", () => {
    it("keeps paragraphs apart instead of running them together", () => {
      const { container } = view();
      const paragraphs = [...container.querySelectorAll(".gmail-view__body p")];
      expect(paragraphs.length).toBeGreaterThanOrEqual(4);
      expect(paragraphs[0].textContent).toContain("Dear Gangadhar");
    });

    it("keeps the bold the recruiter used", () => {
      const { container } = view();
      const bold = [...container.querySelectorAll(".gmail-view__body strong")]
        .map((node) => node.textContent).join(" ");
      expect(bold).toContain("3.30 PM");
      expect(bold).toContain("Interview Scheduled");
    });

    it("keeps the signature on its own lines", () => {
      const { container } = view();
      const text = container.querySelector(".gmail-view__body").textContent;
      expect(text).toContain("Prathima Kamisetti");
      expect(text).toContain("VHS Professional Services Pvt ltd");
    });

    it("says so plainly when there is no body", () => {
      view({ body: "" });
      expect(screen.getByText("This email has no text body.")).toBeInTheDocument();
    });
  });

  describe("links are usable rather than raw", () => {
    it("shows the Teams meeting by name and keeps the href exactly", () => {
      view();
      const link = screen.getByRole("link", { name: "Join Microsoft Teams meeting" });
      expect(link).toHaveAttribute("href", TEAMS);
      // The point of the change: the tracking string is not on screen.
      expect(link.textContent).not.toContain("meetup-join");
      expect(link.textContent.length).toBeLessThan(40);
    });

    it("opens external links safely", () => {
      view();
      const link = screen.getByRole("link", { name: "Join Microsoft Teams meeting" });
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
    });

    it("makes a phone number dialable", () => {
      view();
      expect(screen.getByRole("link", { name: "9845303472" }))
        .toHaveAttribute("href", "tel:9845303472");
    });

    it("links a bare address in plain text", () => {
      view({ body: "Reply to prathima@vhsps.example when ready." });
      expect(screen.getByRole("link", { name: "prathima@vhsps.example" }))
        .toHaveAttribute("href", "mailto:prathima@vhsps.example");
    });

    it("names the other meeting providers too", () => {
      expect(meetingLabel("https://zoom.us/j/123")).toBe("Join Zoom meeting");
      expect(meetingLabel("https://meet.google.com/abc-defg-hij")).toBe("Join Google Meet");
      expect(meetingLabel("https://example.com/x")).toBe("");
    });

    it("shortens an ordinary long link to something readable", () => {
      const label = linkLabel(`https://careers.example.com/${"a".repeat(200)}`, "");
      expect(label.length).toBeLessThanOrEqual(60);
      expect(label.startsWith("careers.example.com")).toBe(true);
    });

    it("keeps a short human link text as written", () => {
      expect(linkLabel("https://example.com/apply", "Apply here")).toBe("Apply here");
    });
  });

  describe("a stranger's markup cannot run here", () => {
    it("drops scripts entirely", () => {
      const { container } = view({ body: "<p>Hello</p><script>window.pwned = 1;</script>" });
      expect(container.querySelector("script")).toBeNull();
      expect(container.textContent).not.toContain("window.pwned");
    });

    it("drops event handlers and images", () => {
      const { container } = view({
        body: '<p onclick="steal()">Hi</p><img src="https://tracker.example/p.gif">',
      });
      const paragraph = container.querySelector(".gmail-view__body p");
      expect(paragraph.getAttribute("onclick")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
    });

    it("refuses a javascript: link, keeping only its text", () => {
      const { container } = view({ body: '<p><a href="javascript:alert(1)">Click me</a></p>' });
      expect(container.querySelector("a")).toBeNull();
      expect(container.textContent).toContain("Click me");
    });

    it("drops styles and iframes", () => {
      const { container } = view({
        body: "<style>body{display:none}</style><iframe src='https://x.example'></iframe><p>Body</p>",
      });
      expect(container.querySelector("style")).toBeNull();
      expect(container.querySelector("iframe")).toBeNull();
      expect(container.textContent).toContain("Body");
    });
  });

  describe("plain-text mail", () => {
    it("splits on blank lines and keeps single breaks", () => {
      const blocks = emailParagraphs("Dear Sir,\nLine two.\n\nSecond paragraph.");
      expect(blocks).toHaveLength(2);
    });

    it("returns nothing for an empty body", () => {
      expect(emailParagraphs("   ")).toEqual([]);
    });
  });

  describe("states", () => {
    it("shows a loader while the body is being fetched", () => {
      render(<OriginalEmail loading formatWhen={() => ""} />);
      expect(screen.getByText("Loading original email…")).toBeInTheDocument();
    });

    it("explains itself when the body could not be loaded", () => {
      render(<OriginalEmail error="Gmail is disconnected." formatWhen={() => ""} />);
      expect(screen.getByText("Gmail is disconnected.")).toBeInTheDocument();
    });
  });
});
