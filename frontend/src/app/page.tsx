"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    const trimmed = url.trim();
    if (!trimmed) return;

    let normalized = trimmed;
    if (!/^https?:\/\//i.test(normalized)) {
      normalized = "https://" + normalized;
    }

    try {
      new URL(normalized);
    } catch {
      setError("URL invalide. Essayez avec https://example.com");
      return;
    }

    setLoading(true);

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: normalized }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body.detail || `Erreur serveur (${res.status})`
        );
      }

      const data = await res.json();
      router.push(`/scan/${data.job_id}`);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Erreur inattendue";
      setError(message);
      setLoading(false);
    }
  }

  return (
    <>
      <div className="shell">
        <nav className="nav">
          <div className="nav-brand">
            <span>QC</span> French-First
          </div>
          <div className="nav-tag">Loi 96 · Beta</div>
        </nav>

        <section className="hero">
          <h1>
            Votre site est-il conforme à la <em>Loi 96</em>&nbsp;?
          </h1>
          <p className="hero-sub">
            Analysez votre site web en 30 secondes et obtenez un score de
            conformité linguistique pour le Québec.
          </p>

          <form className="scan-form" onSubmit={handleSubmit}>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://votre-site.com"
              disabled={loading}
              autoFocus
            />
            <button type="submit" disabled={loading || !url.trim()}>
              {loading ? "Analyse..." : "Scanner"}
            </button>
          </form>

          {error && <p className="form-error">{error}</p>}
        </section>

        <div className="stats-strip">
          <div className="stat">
            <div className="stat-value">250K+</div>
            <div className="stat-label">Entreprises visées</div>
          </div>
          <div className="stat">
            <div className="stat-value">7</div>
            <div className="stat-label">Critères analysés</div>
          </div>
          <div className="stat">
            <div className="stat-value">~30s</div>
            <div className="stat-label">Par analyse</div>
          </div>
          <div className="stat">
            <div className="stat-value">FR/EN</div>
            <div className="stat-label">Pairage complet</div>
          </div>
        </div>

        <section className="how">
          <h2>Comment ça fonctionne</h2>
          <div className="how-grid">
            <div className="how-card">
              <div className="how-step">01</div>
              <h3>Exploration</h3>
              <p>
                Notre scanner découvre les pages de votre site via le sitemap,
                les liens internes et le rendu JavaScript.
              </p>
            </div>
            <div className="how-card">
              <div className="how-step">02</div>
              <h3>Analyse linguistique</h3>
              <p>
                Chaque page est analysée : langue détectée, pairage FR/EN,
                présence d{"'"}un sélecteur de langue, balises hreflang.
              </p>
            </div>
            <div className="how-card">
              <div className="how-step">03</div>
              <h3>Rapport &amp; score</h3>
              <p>
                Un score de conformité 0–100 avec les problèmes priorisés et un
                rapport HTML détaillé téléchargeable.
              </p>
            </div>
          </div>
        </section>
      </div>

      <footer className="footer">
        <div className="shell">
          QC French-First · Outil d{"'"}auto-audit · Pas un avis juridique ·{" "}
          {new Date().getFullYear()}
        </div>
      </footer>
    </>
  );
}
