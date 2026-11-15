import { isAllowedLink } from "../lib/safeLinks";
import { clientIpFromXff } from "../lib/clientIp";

describe("isAllowedLink", () => {
  it.each([
    "https://engineering.tamu.edu/electrical/index.html",
    "https://tamu.edu",
    "https://calendar.tamu.edu/ecen/",
    "mailto:ecen-advising@tamu.edu",
    "mailto:someone@ece.tamu.edu",
    "https://www.linkedin.com/in/aarohi-mohrir/",
  ])("allows %s", href => expect(isAllowedLink(href)).toBe(true));

  it.each([
    "https://evil.example.com",
    "http://engineering.tamu.edu/",            // not https
    "https://tamu.edu.evil.com/",              // suffix trick
    "https://nottamu.edu/",                    // no dot boundary
    "https://tamu.edu@evil.com/",              // userinfo trick
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "mailto:someone@gmail.com",
    "mailto:a@tamu.edu,b@evil.com",
    "https://www.linkedin.com/in/someone-else/",
    "/relative/path",
    "",
  ])("blocks %s", href => expect(isAllowedLink(href)).toBe(false));

  it("blocks missing hrefs", () => {
    expect(isAllowedLink(undefined)).toBe(false);
    expect(isAllowedLink(null)).toBe(false);
  });
});

describe("clientIpFromXff", () => {
  it("ignores a client-supplied leading entry", () => {
    expect(clientIpFromXff("1.2.3.4, 203.0.113.9")).toBe("203.0.113.9");
  });
  it("returns the only entry when there is one", () => {
    expect(clientIpFromXff("203.0.113.9")).toBe("203.0.113.9");
  });
  it("skips a Google load balancer hop without configuration", () => {
    expect(clientIpFromXff("spoofed, 203.0.113.9, 35.191.0.1")).toBe("203.0.113.9");
    expect(clientIpFromXff("203.0.113.9, 130.211.1.7")).toBe("203.0.113.9");
  });
  it("skips private and link-local hops", () => {
    expect(clientIpFromXff("203.0.113.9, 10.0.0.5, 169.254.1.1")).toBe("203.0.113.9");
  });
  it("never returns a spoofed entry when a real public one follows it", () => {
    expect(clientIpFromXff("8.8.8.8, 9.9.9.9, 203.0.113.9")).toBe("203.0.113.9");
  });
  it("falls back to the nearest hop when everything is internal", () => {
    expect(clientIpFromXff("192.168.1.4, 10.0.0.5")).toBe("10.0.0.5");
  });
  it("treats malformed entries as not-a-client", () => {
    expect(clientIpFromXff("203.0.113.9, not-an-ip")).toBe("203.0.113.9");
  });
  it("handles IPv6 clients", () => {
    expect(clientIpFromXff("2001:db8::1, 10.0.0.5")).toBe("2001:db8::1");
  });
  it("honours an explicit hop count", () => {
    expect(clientIpFromXff("spoofed, 203.0.113.9, 198.51.100.2", 2)).toBe("203.0.113.9");
    expect(clientIpFromXff("203.0.113.9", 2)).toBe("203.0.113.9");
  });
  it("falls back when the header is absent", () => {
    expect(clientIpFromXff(null)).toBe("unknown");
    expect(clientIpFromXff("")).toBe("unknown");
  });
});
