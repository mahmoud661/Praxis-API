import { container } from "tsyringe";
import { BaseRoute } from "./base.route";
import { AuthController } from "../controllers/auth.controller";
import { asyncHandler } from "../http/asyncHandler";
import {
  loginRateLimiter,
  signupRateLimiter,
} from "../http/rateLimiters";

// Mounted at /auth (the gateway adds the public /v1 prefix). Resolves the
// controller from the container so its IAuthService dependency is injected.
// Credential endpoints sit behind a per-IP limiter so a flood can't burn
// bcrypt CPU or brute-force passwords; /me + /logout are unmetered.
export class AuthRoutes extends BaseRoute {
  public readonly path = "/auth";

  protected initRoutes(): void {
    const c = container.resolve(AuthController);
    this.router.post(
      "/signup",
      signupRateLimiter,
      asyncHandler(c.signUp.bind(c)),
    );
    this.router.post(
      "/login",
      loginRateLimiter,
      asyncHandler(c.logIn.bind(c)),
    );
    this.router.post("/logout", asyncHandler(c.logOut.bind(c)));
    this.router.get("/me", asyncHandler(c.me.bind(c)));
  }
}
