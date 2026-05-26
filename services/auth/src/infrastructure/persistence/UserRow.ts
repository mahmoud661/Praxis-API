// Row shape — separate from the domain entity. Stays in infrastructure.
export interface UserRow {
  id: string;
  email: string;
  password_hash: string;
  created_at: Date;
  roles: string[];
}
