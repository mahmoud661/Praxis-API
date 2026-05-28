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
      res.status(400).json({
        error: "VALIDATION",
        // First issue is the most actionable; clients show this verbatim.
        message: err.issues[0]?.message ?? "Invalid input",
        details: err.flatten(),
      });
      return;
    }
    // express.json() throws a SyntaxError tagged `entity.parse.failed` when
    // the body isn't valid JSON. That's a client mistake → 400, not 500.
    if (
      err instanceof SyntaxError &&
      (err as SyntaxError & { type?: string }).type === "entity.parse.failed"
    ) {
      res.status(400).json({
        error: "BAD_JSON",
        message: "Request body is not valid JSON.",
      });
      return;
    }
    logger.error("unhandled error", { err: (err as Error).message });
    res.status(500).json({ error: "INTERNAL" });
  };
}
