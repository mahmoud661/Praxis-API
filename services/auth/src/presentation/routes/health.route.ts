import { container } from "tsyringe";
import { BaseRoute } from "./base.route";
import { HealthController } from "../controllers/health.controller";
import { asyncHandler } from "../http/asyncHandler";

// Mounted at root: /healthz (liveness) and /readyz (readiness).
export class HealthRoutes extends BaseRoute {
  public readonly path = "";

  protected initRoutes(): void {
    const c = container.resolve(HealthController);
    this.router.get("/healthz", c.liveness.bind(c));
    this.router.get("/readyz", asyncHandler(c.readiness.bind(c)));
  }
}
