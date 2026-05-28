import { Request, Response } from "express";
import { inject, injectable } from "tsyringe";
import { IAuthService } from "../../domain/IServices/IAuthService";
import { SignUpSchema, LogInSchema } from "../../domain/dtos/auth.dto";
import { respondWithResult } from "../http/resultMapper";
import { clearSessionCookie, setSessionCookie } from "../http/cookies";
import { ENV_TOKEN, Env } from "../../infrastructure/config/Env";

// Thin adapter. Each handler: parse the HTTP body → call IAuthService →
// translate the Result (or set cookies) back to HTTP. No business logic.
// A thrown ZodError becomes a 400 via the error middleware (routes wrap each
// handler with asyncHandler).
@injectable()
export class AuthController {
  constructor(
    @inject("IAuthService") private readonly auth: IAuthService,
    @inject(ENV_TOKEN) private readonly env: Env,
  ) {}

  async signUp(req: Request, res: Response): Promise<void> {
    const body = SignUpSchema.parse(req.body);
    const result = await this.auth.signUp(body, { ip: req.ip ?? null });
    if (result.isOk()) {
      const out = result.getValue();
      this.writeSessionCookie(res, out.sessionId);
      res.status(201).json({ userId: out.userId, email: out.email });
      return;
    }
    respondWithResult(res, result, 201);
  }

  async logIn(req: Request, res: Response): Promise<void> {
    const body = LogInSchema.parse(req.body);
    const result = await this.auth.logIn(body, {
      ip: req.ip ?? null,
      // If the client is re-logging-in over an existing session, revoke it.
      oldSessionId: this.readSessionId(req) ?? null,
    });
    if (result.isOk()) {
      const out = result.getValue();
      this.writeSessionCookie(res, out.sessionId);
      res.json({ userId: out.userId, email: out.email });
      return;
    }
    respondWithResult(res, result);
  }

  async logOut(req: Request, res: Response): Promise<void> {
    const sid = this.readSessionId(req);
    await this.auth.logOut(sid ?? "", { ip: req.ip ?? null });
    clearSessionCookie(res, this.env.SESSION_COOKIE_NAME);
    res.status(204).end();
  }

  async me(req: Request, res: Response): Promise<void> {
    const sid = this.readSessionId(req);
    const result = await this.auth.getCurrentUser(sid ?? "");
    respondWithResult(res, result);
  }

  private readSessionId(req: Request): string | undefined {
    return req.signedCookies?.[this.env.SESSION_COOKIE_NAME] as
      | string
      | undefined;
  }

  private writeSessionCookie(res: Response, value: string): void {
    setSessionCookie({
      res,
      name: this.env.SESSION_COOKIE_NAME,
      value,
      ttlSeconds: this.env.SESSION_TTL_SECONDS,
      secure: this.env.NODE_ENV === "production",
    });
  }
}
