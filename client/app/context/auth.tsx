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
import { getSupabaseClient } from "@/lib/supabase";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    profile: {
      firstName: string;
      lastName: string;
      displayName: string;
      phone: string;
      providerType: string;
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
    profile: {
      firstName: string;
      lastName: string;
      displayName: string;
      phone: string;
      providerType: string;
    },
  ) => {
    const metadata = {
      first_name: profile.firstName,
      last_name: profile.lastName,
      full_name: `${profile.firstName} ${profile.lastName}`.trim(),
      display_name: profile.displayName,
      phone: profile.phone,
      provider_type: profile.providerType,
    };

    const signUpPayload =
      profile.providerType === "phone"
        ? {
            phone: profile.phone,
            password,
            options: { data: metadata },
          }
        : {
            email,
            password,
            options: { data: metadata },
          };

    const { error } = await getSupabaseClient().auth.signUp(signUpPayload);
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
