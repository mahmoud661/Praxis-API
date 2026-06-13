import { describe, expect, it } from "vitest";
import { Response, CookieOptions } from "express";
import {
  clearSessionCookie,
  setSessionCookie,
} from "../../src/presentation/http/cookies";

function fakeRes(): {
  res: Response;
  cookies: { name: string; value?: string; options: CookieOptions }[];
  cleared: { name: string; options: CookieOptions }[];
} {
  const cookies: { name: string; value?: string; options: CookieOptions }[] =
    [];
  const cleared: { name: string; options: CookieOptions }[] = [];
  const res = {
    cookie(name: string, value: string, options: CookieOptions) {
      cookies.push({ name, value, options });
      return res;
    },
    clearCookie(name: string, options: CookieOptions) {
      cleared.push({ name, options });
      return res;
    },
  };
  return { res: res as unknown as Response, cookies, cleared };
}

describe("setSessionCookie", () => {
  it("sets a hardened session cookie: httpOnly, signed, lax, scoped to /", () => {
    const { res, cookies } = fakeRes();
    setSessionCookie({
      res,
      name: "sid",
      value: "abc123",
      ttlSeconds: 3600,
      secure: false,
    });

    expect(cookies).toHaveLength(1);
    expect(cookies[0].name).toBe("sid");
    expect(cookies[0].value).toBe("abc123");
    expect(cookies[0].options).toMatchObject({
      httpOnly: true, // not readable from document.cookie (XSS containment)
      signed: true, // tamper-evident — forged sids fail signature check
      sameSite: "lax", // CSRF mitigation for cross-site POSTs
      path: "/",
    });
    expect(cookies[0].options.maxAge).toBe(3600 * 1000); // express wants ms
  });

  it("passes secure through (true in production, false in dev)", () => {
    for (const secure of [true, false]) {
      const { res, cookies } = fakeRes();
      setSessionCookie({
        res,
        name: "sid",
        value: "v",
        ttlSeconds: 60,
        secure,
      });
      expect(cookies[0].options.secure).toBe(secure);
    }
  });
});

describe("clearSessionCookie", () => {
  it("clears the cookie on the same path it was set on", () => {
    const { res, cleared } = fakeRes();
    clearSessionCookie(res, "sid");
    expect(cleared).toEqual([{ name: "sid", options: { path: "/" } }]);
  });
});
