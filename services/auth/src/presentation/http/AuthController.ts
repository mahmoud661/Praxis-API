import { Request, Response, Router } from "express";
import { inject, injectable } from "tsyringe";
import { z } from "zod";
import { SignUpUseCase } from "../../application/use-cases/SignUpUseCase";
import { LogInUseCase } from "../../application/use-cases/LogInUseCase";
import { LogOutUseCase } from "../../application/use-cases/LogOutUseCase";
import { GetCurrentUserUseCase } from "../../application/use-cases/GetCurrentUserUseCase";
import { respondWithResult } from "./resultMapper";
import { clearSessionCookie, setSessionCookie } from "./cookies";
import { ENV_TOKEN, Env } from "../../infrastructure/config/Env";

const SignUpBody = z.object({
  email: z.string().email(),
  password: z.string().min(8).max(128),
});
const LogInBody = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

// Thin adapter. The controller's job is:
//   1. parse the HTTP payload into a DTO
//   2. call the use case
//   3. translate the Result (or set cookies) back to HTTP
// No business logic here.
@injectable()
export class AuthController {
  readonly router: Router;

  constructor(
    @inject(SignUpUseCase) private readonly signUp: SignUpUseCase,
    @inject(LogInUseCase) private readonly logIn: LogInUseCase,
    @inject(LogOutUseCase) private readonly logOut: LogOutUseCase,
    @inject(GetCurrentUserUseCase) private readonly me: GetCurrentUserUseCase,
    @inject(ENV_TOKEN) private readonly env: Env,
  ) {
    this.router = Router();
    this.router.post("/signup", this.handleSignUp);
    this.router.post("/login", this.handleLogIn);
    this.router.post("/logout", this.handleLogOut);
    this.router.get("/me", this.handleMe);
  }

  private handleSignUp = async (req: Request, res: Response): Promise<void> => {
    const body = SignUpBody.parse(req.body);
    const result = await this.signUp.execute(body);
    if (result.isOk()) {
      const out = result.getValue();
      setSessionCookie({
        res,
        name: this.env.SESSION_COOKIE_NAME,
        value: out.sessionId,
        ttlSeconds: this.env.SESSION_TTL_SECONDS,
        secure: this.env.NODE_ENV === "production",
      });
      res.status(201).json({ userId: out.userId, email: out.email });
      return;
    }
    respondWithResult(res, result, 201);
  };

  private handleLogIn = async (req: Request, res: Response): Promise<void> => {
    const body = LogInBody.parse(req.body);
    const result = await this.logIn.execute(body);
    if (result.isOk()) {
      const out = result.getValue();
      setSessionCookie({
        res,
        name: this.env.SESSION_COOKIE_NAME,
        value: out.sessionId,
        ttlSeconds: this.env.SESSION_TTL_SECONDS,
        secure: this.env.NODE_ENV === "production",
      });
      res.json({ userId: out.userId, email: out.email });
      return;
    }
    respondWithResult(res, result);
  };

  private handleLogOut = async (req: Request, res: Response): Promise<void> => {
    const sid = req.signedCookies?.[this.env.SESSION_COOKIE_NAME] as string | undefined;
    await this.logOut.execute(sid ?? "");
    clearSessionCookie(res, this.env.SESSION_COOKIE_NAME);
    res.status(204).end();
  };

  private handleMe = async (req: Request, res: Response): Promise<void> => {
    const sid = req.signedCookies?.[this.env.SESSION_COOKIE_NAME] as string | undefined;
    const result = await this.me.execute(sid ?? "");
    respondWithResult(res, result);
  };
}
