import { Router } from "express";

// Every route module extends this. The AppServer globs the routes folder,
// instantiates each BaseRoute subclass, and mounts `router` at `path`.
export abstract class BaseRoute {
  public readonly router: Router;
  public abstract readonly path: string;

  constructor() {
    this.router = Router({ mergeParams: true });
    this.initRoutes();
  }

  protected abstract initRoutes(): void;
}
