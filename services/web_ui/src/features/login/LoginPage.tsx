import { Building2, LogIn, ShieldCheck } from "lucide-react";


import styles from "./LoginPage.module.css";
import { useAuthStore } from "../../stores/authStore";

export function LoginPage() {
  const loginForDevelopment = useAuthStore(
    (state) => state.loginForDevelopment,
  );

  const isDevelopment = true;

  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="login-title">
        <div className={styles.brandIcon} aria-hidden="true">
          <Building2 size={30} strokeWidth={1.9} />
        </div>

        <p className={styles.eyebrow}>Internal Knowledge Assistant</p>

        <h1 id="login-title" className={styles.title}>
          RAG Chatbot
        </h1>

        <p className={styles.description}>
          Sign in with your company account to access approved internal
          documents and chatbot features.
        </p>

        {isDevelopment ? (
          <button
            type="button"
            className={styles.loginButton}
            onClick={loginForDevelopment}
          >
            <LogIn size={20} aria-hidden="true" />
            Continue with development login
          </button>
        ) : (
          <div className={styles.productionNotice} role="status">
            Company sign-in is not configured yet. Configure Microsoft Entra ID
            before publishing this application.
          </div>
        )}

        <div className={styles.securityNote}>
          <ShieldCheck size={18} aria-hidden="true" />
          <span>
            Development login is a UI test mode only. It does not protect the
            backend API.
          </span>
        </div>
      </section>
    </main>
  );
}
