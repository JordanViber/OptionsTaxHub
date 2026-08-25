import {
  createClient,
  type EmailOtpType,
  type Session,
  type SupabaseClient,
  type User,
} from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

let supabaseClient: SupabaseClient | null = null;

export function getSupabaseClient() {
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error("Supabase environment variables are required.");
  }

  if (!supabaseClient) {
    supabaseClient = createClient(supabaseUrl, supabaseAnonKey);
  }

  return supabaseClient;
}

/**
 * Confirmation links must land on this origin's /auth/confirm-email,
 * never a hardcoded localhost URL (staging/prod use window.location.origin).
 */
export function getEmailConfirmRedirectTo(): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return `${window.location.origin}/auth/confirm-email`;
}

export function isEmailConfirmed(
  user: Pick<User, "email_confirmed_at"> | null | undefined,
): boolean {
  return Boolean(user?.email_confirmed_at);
}

export function isEmailNotConfirmedError(err: unknown): boolean {
  if (err == null) {
    return false;
  }

  let blob = "";
  if (typeof err === "string") {
    blob = err;
  } else if (typeof err === "object") {
    const maybe = err as {
      code?: unknown;
      message?: unknown;
      error_code?: unknown;
    };
    blob = [maybe.code, maybe.message, maybe.error_code]
      .filter((value) => value != null)
      .join(" ");
  } else {
    blob = String(err);
  }

  const text = blob.toLowerCase();
  return (
    text.includes("email_not_confirmed") || text.includes("email not confirmed")
  );
}

function confirmOtpType(raw: string | null): EmailOtpType {
  if (
    raw === "signup" ||
    raw === "email" ||
    raw === "invite" ||
    raw === "magiclink"
  ) {
    return raw;
  }
  return "signup";
}

/**
 * Sign up with email and password
 */
export async function signUp(email: string, password: string) {
  const emailRedirectTo = getEmailConfirmRedirectTo();
  const { data, error } = await getSupabaseClient().auth.signUp({
    email,
    password,
    options: emailRedirectTo ? { emailRedirectTo } : undefined,
  });

  if (error) throw error;
  return data;
}

/**
 * Sign in with email and password
 */
export async function signIn(email: string, password: string) {
  const { data, error } = await getSupabaseClient().auth.signInWithPassword({
    email,
    password,
  });

  if (error) throw error;
  return data;
}

/**
 * Sign out the current user
 */
export async function signOut() {
  const { error } = await getSupabaseClient().auth.signOut();
  if (error) throw error;
}

/**
 * Get the current session
 */
export async function getSession() {
  const { data, error } = await getSupabaseClient().auth.getSession();
  if (error) throw error;
  return data.session;
}

/**
 * Get the current user
 */
export async function getCurrentUser() {
  const { data, error } = await getSupabaseClient().auth.getUser();
  if (error) throw error;
  return data.user;
}

export function getPasswordResetRedirectTo(): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return `${window.location.origin}/auth/reset-password`;
}

/**
 * Send a password reset email via Supabase.
 * The recovery link lands on /auth/reset-password so the user can set a new password.
 */
export async function resetPasswordForEmail(email: string) {
  const redirectTo = getPasswordResetRedirectTo();
  const { error } = await getSupabaseClient().auth.resetPasswordForEmail(
    email,
    redirectTo ? { redirectTo } : undefined,
  );
  if (error) throw error;
}

/**
 * Send another signup confirmation email. Confirm-email stays ON in Supabase.
 */
export async function resendSignupConfirmation(email: string) {
  const emailRedirectTo = getEmailConfirmRedirectTo();
  const { error } = await getSupabaseClient().auth.resend({
    type: "signup",
    email,
    options: emailRedirectTo ? { emailRedirectTo } : undefined,
  });
  if (error) throw error;
}

/**
 * Set a new password for the current (recovery) session.
 */
export async function updatePassword(password: string) {
  const { data, error } = await getSupabaseClient().auth.updateUser({
    password,
  });
  if (error) throw error;
  return data;
}

/**
 * Establish a PASSWORD_RECOVERY session from the email-link URL
 * (PKCE `code`, `token_hash`, or hash tokens already consumed by the client).
 */
export async function establishRecoverySession() {
  const supabase = getSupabaseClient();

  if (typeof window !== "undefined") {
    const hash = window.location.hash.replace(/^#/, "");
    const hashParams = new URLSearchParams(hash);
    const hashError =
      hashParams.get("error_code") || hashParams.get("error");
    if (hashError) {
      const description = hashParams
        .get("error_description")
        ?.replace(/\+/g, " ");
      throw new Error(description || hashError);
    }

    const searchParams = new URLSearchParams(window.location.search);
    const code = searchParams.get("code");
    const tokenHash = searchParams.get("token_hash");
    if (code) {
      const { error } = await supabase.auth.exchangeCodeForSession(code);
      if (error) throw error;
    } else if (tokenHash) {
      const { error } = await supabase.auth.verifyOtp({
        token_hash: tokenHash,
        type: "recovery",
      });
      if (error) throw error;
    }
  }

  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  if (!data.session) {
    throw new Error("otp_expired");
  }
}

/**
 * Consume a signup confirmation link (PKCE `code`, `token_hash`/type, or hash
 * session) and return the resulting session. Visiting /auth/confirm-email
 * without tokens is not an error — the page can show a check-email state.
 */
export async function consumeEmailConfirmLink(): Promise<{
  session: Session | null;
  consumedLink: boolean;
}> {
  const supabase = getSupabaseClient();
  let consumedLink = false;

  if (typeof window !== "undefined") {
    const hash = window.location.hash.replace(/^#/, "");
    const hashParams = new URLSearchParams(hash);
    const hashError =
      hashParams.get("error_code") || hashParams.get("error");
    if (hashError) {
      const description = hashParams
        .get("error_description")
        ?.replace(/\+/g, " ");
      throw new Error(description || hashError);
    }

    const searchParams = new URLSearchParams(window.location.search);
    const code = searchParams.get("code");
    const tokenHash = searchParams.get("token_hash");
    const hashAccessToken = hashParams.get("access_token");
    consumedLink = Boolean(code || tokenHash || hashAccessToken);

    if (code) {
      const { error } = await supabase.auth.exchangeCodeForSession(code);
      if (error) throw error;
    } else if (tokenHash) {
      const { error } = await supabase.auth.verifyOtp({
        token_hash: tokenHash,
        type: confirmOtpType(searchParams.get("type")),
      });
      if (error) throw error;
    } else if (hashAccessToken) {
      const refreshToken = hashParams.get("refresh_token");
      if (refreshToken) {
        const { error } = await supabase.auth.setSession({
          access_token: hashAccessToken,
          refresh_token: refreshToken,
        });
        if (error) throw error;
      }
    }
  }

  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  if (consumedLink && !data.session) {
    throw new Error("otp_expired");
  }
  return { session: data.session, consumedLink };
}
