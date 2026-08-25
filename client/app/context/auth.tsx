"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useMemo,
  ReactNode,
} from "react";
import { User } from "@supabase/supabase-js";
import {
  getEmailConfirmRedirectTo,
  getSupabaseClient,
} from "@/lib/supabase";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    profile?: {
      name?: string;
    },
  ) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check current session on mount
    const checkSession = async () => {
      try {
        const supabase = getSupabaseClient();
        const {
          data: { session },
        } = await supabase.auth.getSession();
        setUser(session?.user ?? null);
      } finally {
        setLoading(false);
      }
    };

    checkSession();

    // Listen for auth changes
    const supabase = getSupabaseClient();
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null);
      setLoading(false);
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  const signIn = async (email: string, password: string) => {
    const { error } = await getSupabaseClient().auth.signInWithPassword({
      email,
      password,
    });
    if (error) throw error;
  };

  const signUp = async (
    email: string,
    password: string,
    profile?: {
      name?: string;
    },
  ) => {
    const name = profile?.name?.trim() ?? "";
    const [firstName, ...rest] = name.split(/\s+/).filter(Boolean);
    const lastName = rest.join(" ");
    const metadata = {
      first_name: firstName ?? "",
      last_name: lastName,
      full_name: name,
      display_name: name,
    };
    const emailRedirectTo = getEmailConfirmRedirectTo();

    const { error } = await getSupabaseClient().auth.signUp({
      email,
      password,
      options: {
        data: metadata,
        ...(emailRedirectTo ? { emailRedirectTo } : {}),
      },
    });
    if (error) throw error;
  };

  const signOut = async () => {
    const { error } = await getSupabaseClient().auth.signOut();
    if (error) throw error;
  };

  const value = useMemo(
    () => ({ user, loading, signIn, signUp, signOut }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
