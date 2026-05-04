import type { PiiFinding } from "@workspace/db";

const PATTERNS: { type: string; regex: RegExp; mask: (v: string) => string }[] = [
  {
    type: "email",
    regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g,
    mask: () => "[REDACTED_EMAIL]",
  },
  {
    type: "phone",
    regex: /(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/g,
    mask: () => "[REDACTED_PHONE]",
  },
  {
    type: "ssn",
    regex: /\b\d{3}-\d{2}-\d{4}\b/g,
    mask: () => "[REDACTED_SSN]",
  },
  {
    type: "credit_card",
    regex: /\b(?:\d[ -]*?){13,16}\b/g,
    mask: () => "[REDACTED_CC]",
  },
  {
    type: "ip_address",
    regex: /\b(?:\d{1,3}\.){3}\d{1,3}\b/g,
    mask: () => "[REDACTED_IP]",
  },
];

export function detectAndMaskPii(text: string): {
  cleaned: string;
  findings: PiiFinding[];
} {
  let cleaned = text;
  const findings: PiiFinding[] = [];
  for (const { type, regex, mask } of PATTERNS) {
    cleaned = cleaned.replace(regex, (match) => {
      // Skip credit-card pattern when it's clearly not (e.g., long whitespace runs of digits)
      if (type === "credit_card") {
        const digits = match.replace(/[\s-]/g, "");
        if (digits.length < 13 || digits.length > 19) return match;
      }
      const replacement = mask(match);
      findings.push({ type, value: match, replacement });
      return replacement;
    });
  }
  return { cleaned, findings };
}
