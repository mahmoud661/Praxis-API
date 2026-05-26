import pino, { Logger as Pino } from "pino";
import { injectable } from "tsyringe";
import { Logger } from "../../domain/ports/Logger";

@injectable()
export class PinoLoggerAdapter implements Logger {
  private readonly p: Pino;

  constructor(serviceName: string, level: pino.Level = "info") {
    this.p = pino({
      level,
      base: { service: serviceName },
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
