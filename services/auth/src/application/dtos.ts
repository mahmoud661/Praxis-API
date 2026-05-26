// Boundary types between the presentation and application layers.
// These are *not* domain types — they're framework-friendly shapes (plain
// objects, strings) so the controller can build them from HTTP bodies.

export interface SignUpInput {
  email: string;
  password: string;
}

export interface LogInInput {
  email: string;
  password: string;
}

export interface AuthOutput {
  userId: string;
  email: string;
  sessionId: string;
}

export interface UserView {
  userId: string;
  email: string;
}
