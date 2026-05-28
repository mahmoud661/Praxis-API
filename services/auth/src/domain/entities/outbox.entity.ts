import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  PrimaryGeneratedColumn,
} from "typeorm";

// Transactional outbox row. Events are written here atomically with the
// business write; the OutboxPoller ships pending rows to Kafka and stamps
// `publishedAt`. Column names stay snake_case because the poller selects
// them with raw SQL (FOR UPDATE SKIP LOCKED).
@Entity("outbox")
export class OutboxEvent {
  @PrimaryGeneratedColumn("uuid")
  id!: string;

  @Column({ type: "varchar", name: "aggregate_id", length: 120 })
  aggregateId!: string;

  @Column({ type: "varchar", length: 120 })
  topic!: string;

  @Column({ type: "varchar", name: "event_name", length: 120 })
  eventName!: string;

  @Column({ type: "jsonb" })
  payload!: unknown;

  @Column({ type: "jsonb", default: () => "'{}'" })
  headers!: Record<string, string>;

  @Index({ where: "published_at IS NULL" })
  @CreateDateColumn({ name: "created_at", type: "timestamptz" })
  createdAt!: Date;

  @Column({ name: "published_at", type: "timestamptz", nullable: true })
  publishedAt!: Date | null;
}
