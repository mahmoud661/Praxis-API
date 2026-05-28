import { Result } from "../shared/Result";
import { DomainException } from "../shared/DomainException";
import {
  SignUpInput,
  LogInInput,
  AuthOutput,
  UserView,
  AuthContext,
} from "../dtos/auth.dto";

// DI token "IAuthService" (impl class `AuthService`). Controllers inject this
// interface, never the concrete class — the container binds it by convention.
//
// Every write method takes an optional `ctx` for per-request data the
// presentation layer extracts from the HTTP request (client IP, the cookie's
// current session id). The body DTO stays a pure payload shape.
export interface IAuthService {
  signUp(
    input: SignUpInput,
    ctx?: AuthContext,
  ): Promise<Result<AuthOutput, DomainException>>;
  logIn(
    input: LogInInput,
    ctx?: AuthContext,
  ): Promise<Result<AuthOutput, DomainException>>;
  logOut(
    sessionId: string,
    ctx?: AuthContext,
  ): Promise<Result<void, DomainException>>;
  getCurrentUser(
    sessionId: string,
  ): Promise<Result<UserView, DomainException>>;
}
