/**
 * Adversarial tests for the browser-side controls: which links in a model
 * answer become clickable, and which X-Forwarded-For entry is trusted as the
 * rate-limit key. Every case is an attempt to get past one of them.
 */
import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { SafeLink } from "../components/SafeLink";
import { isAllowedLink } from "../lib/safeLinks";
import { clientIpFromXff } from "../lib/clientIp";

describe("link allowlist: bypass attempts", () => {
  it.each([
    // look-alike hosts
    ["suffix on attacker domain", "https://tamu.edu.evil.com/"],
    ["missing dot boundary", "https://eviltamu.edu/"],
    ["allowed name in path", "https://evil.com/tamu.edu"],
    ["allowed name in query", "https://evil.com/?next=https://tamu.edu"],
    ["allowed name in fragment", "https://evil.com/#@tamu.edu"],
    ["allowed name as subdomain label", "https://tamu.edu.evil.com./"],
    ["cyrillic homoglyph", "https://tаmu.edu/"],
    ["fullwidth dot", "https://tamu．edu.evil.com/"],
    // userinfo and parser confusion
    ["userinfo", "https://tamu.edu@evil.com/"],
    ["userinfo with port", "https://tamu.edu:443@evil.com/"],
    ["backslash host", "https://evil.com\\@tamu.edu/"],
    ["backslash path", "https:\\\\evil.com\\tamu.edu"],
    ["encoded at-sign", "https://tamu.edu%40evil.com/"],
    ["protocol-relative", "//evil.com/tamu.edu"],
    ["missing slashes", "https:evil.com"],
    // schemes
    ["http downgrade", "http://engineering.tamu.edu/"],
    ["javascript", "javascript:alert(1)"],
    ["javascript mixed case", "JaVaScRiPt:alert(1)"],
    ["javascript with tab", "java\tscript:alert(1)"],
    ["javascript with newline", "java\nscript:alert(1)"],
    ["leading control char", "\u0001javascript:alert(1)"],
    ["javascript pretending to be tamu", "javascript://tamu.edu/%0aalert(1)"],
    ["data", "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="],
    ["vbscript", "vbscript:msgbox(1)"],
    ["file", "file:///etc/passwd"],
    ["blob", "blob:https://tamu.edu/abc"],
    ["ftp", "ftp://tamu.edu/file"],
    ["tel", "tel:+19795551234"],
    // mailto
    ["mailto other domain", "mailto:someone@gmail.com"],
    ["mailto two recipients", "mailto:a@tamu.edu,b@evil.com"],
    ["mailto cc smuggled", "mailto:a@tamu.edu?cc=attacker@evil.com"],
    ["mailto prefilled body", "mailto:a@tamu.edu?subject=Reset&body=Send%20your%20password"],
    ["mailto encoded second address", "mailto:a@tamu.edu%2Cb@evil.com"],
    ["mailto double at", "mailto:a@evil.com@tamu.edu"],
    ["mailto look-alike domain", "mailto:a@tamu.edu.evil.com"],
    // the LinkedIn exception must not widen
    ["other linkedin profile", "https://www.linkedin.com/in/someone-else/"],
    ["profile name as prefix", "https://www.linkedin.com/in/aarohi-mohrir-fake/"],
    ["path traversal off the profile", "https://www.linkedin.com/in/aarohi-mohrir/../../redir/redirect?url=https://evil.com"],
    ["profile with redirect query", "https://www.linkedin.com/in/aarohi-mohrir/?trk=https://evil.com"],
    ["linkedin look-alike host", "https://www.linkedin.com.evil.com/in/aarohi-mohrir/"],
    ["linkedin as userinfo", "https://www.linkedin.com/in/aarohi-mohrir@evil.com/"],
    ["linkedin over http", "http://www.linkedin.com/in/aarohi-mohrir/"],
    // degenerate
    ["empty", ""],
    ["spaces", "   "],
    ["relative", "/admin"],
    ["bare word", "tamu.edu"],
    ["absurdly long", "https://tamu.edu/" + "a".repeat(5000)],
  ])("blocks: %s", (_name, href) => {
    expect(isAllowedLink(href)).toBe(false);
  });

  it.each([
    "https://engineering.tamu.edu/electrical/people/index.html",
    "https://tamu.edu/",
    "https://TAMU.EDU/Admissions",
    "https://calendar.tamu.edu/ecen/?date=2026-11-15#today",
    "https://engineering.tamu.edu./electrical/",
    "mailto:ecen-advising@tamu.edu",
    "mailto:first.last+tag@ece.tamu.edu",
    "https://www.linkedin.com/in/aarohi-mohrir/",
    "https://www.linkedin.com/in/aarohi-mohrir",
  ])("still allows the legitimate link %s", href => {
    expect(isAllowedLink(href)).toBe(true);
  });
});

describe("rendered answers: hostile markdown never becomes a live link", () => {
  const renderAnswer = (md: string) =>
    render(
      <ReactMarkdown components={{ a: ({ href, children }) => <SafeLink href={href}>{children}</SafeLink> }}>
        {md}
      </ReactMarkdown>,
    );

  it.each([
    "[Apply here](https://evil.example/apply)",
    "[Reset your NetID password](https://tamu.edu.evil.com/login)",
    "[click](javascript:alert(document.cookie))",
    "[click](data:text/html,<script>alert(1)</script>)",
    "<https://evil.example>",
    "[Email the advisor](mailto:advisor@tamu.edu?cc=attacker@evil.com)",
    "[x](https://tamu.edu@evil.com)",
  ])("no anchor for %s", md => {
    const { container } = renderAnswer(md);
    expect(container.querySelector("a")).toBeNull();
  });

  it("keeps the link text readable when the link is stripped", () => {
    const { container } = renderAnswer("See [the application portal](https://evil.example).");
    expect(container.textContent).toBe("See the application portal.");
  });

  it("raw HTML in an answer is shown as text, not executed", () => {
    const { container } = renderAnswer(
      'Hi <img src=x onerror="alert(1)"> <script>alert(1)</script> <a href="https://evil.example">x</a>',
    );
    expect(container.querySelector("img, script, a, iframe")).toBeNull();
  });

  it("allowed links open safely in a new tab", () => {
    const { container } = renderAnswer("[ECE](https://engineering.tamu.edu/electrical/)");
    const a = container.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://engineering.tamu.edu/electrical/");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });
});

describe("client IP: rate-limit key cannot be chosen by the caller", () => {
  const REAL = "203.0.113.9";

  it.each([
    ["single fake", `1.2.3.4, ${REAL}`],
    ["many fakes", `1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4, ${REAL}`],
    ["fake that looks internal", `10.0.0.1, ${REAL}`],
    ["fake loopback", `127.0.0.1, ${REAL}`],
    ["fake google proxy", `35.191.0.1, ${REAL}`],
    ["fake with port", `8.8.8.8:443, ${REAL}`],
    ["fake ipv6", `2001:db8::dead, ${REAL}`],
    ["garbage", `<script>, ';DROP TABLE, ${REAL}`],
    ["empty items", `,,, , ${REAL}`],
    ["the word unknown", `unknown, ${REAL}`],
    ["real ip then google load balancer", `9.9.9.9, ${REAL}, 35.191.12.34`],
    ["real ip then private hops", `9.9.9.9, ${REAL}, 10.4.0.2, 169.254.8.1`],
    ["1000 fakes", Array.from({ length: 1000 }, (_, i) => `11.0.${i % 255}.${i % 255}`).join(", ") + `, ${REAL}`],
  ])("%s", (_name, header) => {
    expect(clientIpFromXff(header)).toBe(REAL);
  });

  it("rotating the spoofed value does not rotate the key", () => {
    const keys = new Set(
      Array.from({ length: 50 }, (_, i) => clientIpFromXff(`77.77.${i}.${i}, ${REAL}`)),
    );
    expect(keys.size).toBe(1);
  });

  it("junk in the trusted position is skipped, not used as a key", () => {
    expect(clientIpFromXff(`${REAL}, [::1]:443`)).toBe(REAL);
    expect(clientIpFromXff(`${REAL}, host:8080`)).toBe(REAL);
    expect(clientIpFromXff(`${REAL}, 999.999.999.999`)).toBe(REAL);
  });

  it("a real IPv6 client is kept", () => {
    expect(clientIpFromXff("1.2.3.4, 2001:db8:85a3::8a2e:370:7334")).toBe("2001:db8:85a3::8a2e:370:7334");
    expect(clientIpFromXff("2001:0db8:0000:0000:0000:ff00:0042:8329, 10.0.0.1")).toBe("2001:0db8:0000:0000:0000:ff00:0042:8329");
  });
});
