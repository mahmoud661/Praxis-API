import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  PrimaryGeneratedColumn,
} from "typeorm";

// Append-only record of security-relevant events (signup, login, logout…).
// Written inside the same transaction as the business write so the trail
// commits atomically.
@Entity("audit_log")
export class AuditLog {
  @PrimaryGeneratedColumn("uuid")
  id!: string;

  @Index()
  @CreateDateColumn({ name: "occurred_at", type: "timestamptz" })
  occurredAt!: Date;

  @Index()
  @Column({ name: "actor_id", type: "uuid", nullable: true })
  actorId!: string | null;

  @Index()
  @Column({ type: "varchar", length: 80 })
  action!: string;

  @Column({ type: "varchar", name: "target_id", length: 120, nullable: true })
  targetId!: string | null;

  @Column({ type: "jsonb", default: () => "'{}'" })
  details!: Record<string, unknown>;

  @Column({ type: "inet", nullable: true })
  ip!: string | null;
}
