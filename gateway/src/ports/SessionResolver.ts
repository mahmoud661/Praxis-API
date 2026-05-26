// Port: "given a session id, who is the user?"
// RedisSessionResolver is the default adapter; tests can stub it.
export interface ResolvedUser {
  id: string;
  email: string;
  roles: string[];
}

export interface SessionResolver {
  resolve(sessionId: string): Promise<ResolvedUser | null>;
}
