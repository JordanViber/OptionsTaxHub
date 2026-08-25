import { createClient, type SupabaseClient } from "@supabase/supabase-js";

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
 * Sign up with email and password
 */
export async function signUp(email: string, password: string) {
  const { data, error } = await getSupabaseClient().auth.signUp({
    email,
    password,
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
