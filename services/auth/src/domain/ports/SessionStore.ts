export const SESSION_STORE = Symbol("SessionStore");

export interface SessionData {
  userId: string;
  email: string;
  roles: string[];
  createdAt: string;
}

export interface SessionStore {
  create(data: SessionData): Promise<string>; // returns sessionId
  read(sessionId: string): Promise<SessionData | null>;
  refresh(sessionId: string): Promise<void>;
  destroy(sessionId: string): Promise<void>;
}
