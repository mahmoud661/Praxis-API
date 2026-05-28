import { CreateDateColumn, UpdateDateColumn } from "typeorm";
import { AggregateRoot } from "../shared/AggregateRoot";

// Shared base for persistent aggregates. Extends AggregateRoot so entities
// keep their domain-event behavior (`addEvent`/`pullEvents`) — those fields
// are NOT TypeORM columns, so they're never persisted. TypeORM manages the
// timestamp columns below.
export abstract class BaseEntity extends AggregateRoot {
  @CreateDateColumn({ name: "created_at", type: "timestamptz" })
  createdAt!: Date;

  @UpdateDateColumn({ name: "updated_at", type: "timestamptz" })
  updatedAt!: Date;
}
