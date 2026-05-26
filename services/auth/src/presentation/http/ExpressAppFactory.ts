import express from "express";
import helmet from "helmet";
import cookieParser from "cookie-parser";
import { inject, injectable } from "tsyringe";
import { AuthController } from "./AuthController";
import { HealthController } from "./HealthController";
import { makeErrorMiddleware } from "./errorMiddleware";
import { ENV_TOKEN, Env } from "../../infrastructure/config/Env";
import { LOGGER, Logger } from "../../domain/ports/Logger";

// Builds the wired-up Express app. This is the only place that knows about
// the framework. Swap Express for Fastify / Koa by replacing this factory.
@injectable()
export class ExpressAppFactory {
  constructor(
    @inject(AuthController) private readonly auth: AuthController,
    @inject(HealthController) private readonly health: HealthController,
    @inject(ENV_TOKEN) private readonly env: Env,
    @inject(LOGGER) private readonly logger: Logger,
  ) {}

  build(): express.Express {
    const app = express();
    app.disable("x-powered-by");
    app.set("trust proxy", true);

    app.use(helmet());
    app.use(express.json({ limit: "100kb" }));
    app.use(cookieParser(this.env.SESSION_SECRET));

    app.use(this.health.router);
    app.use("/auth", this.auth.router);

    app.use((_req, res) => res.status(404).json({ error: "NOT_FOUND" }));
    app.use(makeErrorMiddleware(this.logger));

    return app;
  }
}
