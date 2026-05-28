import express, { Express } from "express";
import helmet from "helmet";
import cookieParser from "cookie-parser";
import { Server } from "http";
import { glob } from "glob";
import path from "path";
import { inject, injectable } from "tsyringe";
import { ENV_TOKEN, Env } from "../../infrastructure/config/Env";
import { LOGGER, Logger } from "../../domain/ports/Logger";
import { BaseRoute } from "../routes/base.route";
import { makeErrorMiddleware } from "./errorMiddleware";

// The gateway adds the public "/v1" prefix; internally routes are mounted at
// their own path (/auth, root for health).
const API_PREFIX = "";

// Builds the Express app and auto-mounts every BaseRoute subclass found in the
// routes folder (glob + dynamic import). The only place that knows about the
// web framework — swap Express by replacing this file.
@injectable()
export class AppServer {
  private readonly app: Express;

  constructor(
    @inject(ENV_TOKEN) private readonly env: Env,
    @inject(LOGGER) private readonly logger: Logger,
  ) {
    this.app = express();
    this.app.disable("x-powered-by");
    this.app.set("trust proxy", true);
    this.app.use(helmet());
    this.app.use(express.json({ limit: "100kb" }));
    this.app.use(cookieParser(this.env.SESSION_SECRET));
  }

  async listen(): Promise<Server> {
    await this.mountRoutes();
    this.app.use((_req, res) => res.status(404).json({ error: "NOT_FOUND" }));
    this.app.use(makeErrorMiddleware(this.logger));
    return this.app.listen(this.env.PORT, () =>
      this.logger.info("auth-service listening", { port: this.env.PORT }),
    );
  }

  private async mountRoutes(): Promise<void> {
    const files = await glob(
      path.resolve(__dirname, "../routes/*.@(ts|js)").replace(/\\/g, "/"),
    );
    for (const file of files) {
      // Plain path: tsc compiles `import()` to `require()` (CommonJS), which
      // resolves absolute paths but not file:// URLs.
      const mod = await import(file);
      for (const exportedName in mod) {
        const RouteClass = mod[exportedName];
        if (
          typeof RouteClass === "function" &&
          Object.getPrototypeOf(RouteClass)?.name === "BaseRoute"
        ) {
          const route: BaseRoute = new RouteClass();
          const mount = `${API_PREFIX}${route.path}` || "/";
          this.app.use(mount, route.router);
          this.logger.info("route mounted", { path: mount });
        }
      }
    }
  }
}
