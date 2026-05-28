import pino, { Logger as Pino } from "pino";
import { inject, injectable } from "tsyringe";
import { Logger } from "../../domain/ports/Logger";
import { ENV_TOKEN, Env } from "../config/Env";

@injectable()
export class PinoLoggerAdapter implements Logger {
  private readonly p: Pino;

  constructor(@inject(ENV_TOKEN) env: Env) {
    this.p = pino({
      level: env.NODE_ENV === "production" ? "info" : "debug",
      base: { service: env.serviceName },
      timestamp: pino.stdTimeFunctions.isoTime,
    });
  }

  debug(msg: string, ctx: Record<string, unknown> = {}): void {
    this.p.debug(ctx, msg);
  }
  info(msg: string, ctx: Record<string, unknown> = {}): void {
    this.p.info(ctx, msg);
  }
  warn(msg: string, ctx: Record<string, unknown> = {}): void {
    this.p.warn(ctx, msg);
  }
  error(msg: string, ctx: Record<string, unknown> = {}): void {
    this.p.error(ctx, msg);
  }

  raw(): Pino {
    return this.p;
  }
}
