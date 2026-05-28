import { RequestHandler } from "express";

// Express 4 does NOT forward rejected promises from async handlers to the
// error middleware — an uncaught async throw becomes an unhandledRejection
// and crashes the process. This wrapper routes any rejection to `next(err)`
// so it reaches `errorMiddleware` (which maps ZodError → 400, etc.).
export const asyncHandler =
  (fn: RequestHandler): RequestHandler =>
  (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
