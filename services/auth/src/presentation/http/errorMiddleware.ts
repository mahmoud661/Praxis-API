import { NextFunction, Request, Response } from "express";
import { ZodError } from "zod";
import { Logger } from "../../domain/ports/Logger";

// Last-line error handler. Validation errors become 400; everything else
// becomes 500 with no internal details leaked. Real errors are logged.
export function makeErrorMiddleware(logger: Logger) {
  return function errorMiddleware(
    err: unknown,
    _req: Request,
    res: Response,
    _next: NextFunction,
  ): void {
    if (err instanceof ZodError) {
      res.status(400).json({ error: "VALIDATION", details: err.flatten() });
      return;
    }
    logger.error("unhandled error", { err: (err as Error).message });
    res.status(500).json({ error: "INTERNAL" });
  };
}
