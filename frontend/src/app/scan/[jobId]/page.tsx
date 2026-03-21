"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

/* ─── types ──────────────────────────────────────────────────── */

interface Issue {
  code: string;
  title: string;
  detail: string;
  priority: string;
}

interface ScanResult {
  job_id: string;
  url: string;
  status: "queued" | "running" | "done" | "error";
  score?: number;
  label?: string;
  issues?: Issue[];
  report_url?: string;
  pdf_url?: string;
  error?: string;
  created_at?: string;
}

/* ─── helpers ────────────────────────────────────────────────── */

function scoreColor(score: number): string {
  if (score >= 85) return "var(--good)";
  if (score >= 70) return "var(--warn)";
  return "var(--bad)";
}

function labelClass(label: string): string {
  const l = label.toLowerCase();
  if (l === "good") return "label-good";
  if (l.includes("moderate")) return "label-moderate";
  if (l.includes("high")) return "label-high";
  return "label-critical";
}

function pillClass(priority: string): string {
  if (priority === "P0") return "issue-pill issue-pill-p0";
  if (priority === "P1") return "issue-pill issue-pill-p1";
  return "issue-pill issue-pill-p2";
}

/* ─── SVG score ring ─────────────────────────────────────────── */

function ScoreRing({ score }: { score: number }) {
  const r = 76;
  const circ = 2 * Math.PI * r;
  const offset = circ - (score / 100) * circ;
  const color = scoreColor(score);

  return (
    <div className="score-ring-wrap">
      <svg viewBox="0 0 180 180">
        <circle cx="90" cy="90" r={r} className="score-ring-bg" />
        <circle
          cx="90"
          cy="90"
          r={r}
          className="score-ring-fill"
          style={{
            stroke: color,
            strokeDasharray: circ,
            strokeDashoffset: offset,
          }}
        />
      </svg>
      <div className="score-number" style={{ color }}>
        {score}
      </div>
    </div>
  );
}

/* ─── page component ─────────────────────────────────────────── */

export default function ScanResultPage() {
  const params = useParams();
  const jobId = params.jobId as string;

  const [data, setData] = useState<ScanResult | null>(null);
  const [fetchError, setFetchError] = useState("");
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptsRef = useRef(0);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`/api/scan/${jobId}`);
      if (!res.ok) {
        if (res.status === 404) {
          setFetchError("Analyse introuvable. Vérifiez l'identifiant.");
          return;
        }
        throw new Error(`Erreur ${res.status}`);
      }
      const result: ScanResult = await res.json();
      setData(result);

      if (result.status === "done" || result.status === "error") {
        return; // stop polling
      }

      // exponential backoff: 2s → 3s → 4s → 5s → 6s (cap)
      attemptsRef.current += 1;
      const delay = Math.min(2000 + attemptsRef.current * 500, 6000);
      intervalRef.current = setTimeout(poll, delay);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Erreur réseau";
      setFetchError(msg);
    }
  }, [jobId]);

  useEffect(() => {
    poll();
    return () => {
      if (intervalRef.current) clearTimeout(intervalRef.current);
    };
  }, [poll]);

  const isLoading =
    !data || data.status === "queued" || data.status === "running";
  const isDone = data?.status === "done";
  const isError = data?.status === "error" || !!fetchError;

  return (
    <div className="shell">
      <nav className="nav">
        <Link href="/" className="nav-brand">
          <span>QC</span> French-First
        </Link>
        <div className="nav-tag">Résultats</div>
      </nav>

      <section className="results">
        <div className="results-header">
          <h1>Résultats de l{"'"}analyse</h1>
          {data?.url && <p className="results-url">{data.url}</p>}
        </div>

        {/* ── loading state ── */}
        {isLoading && !isError && (
          <div className="pulse-wrap">
            <div className="pulse-bar" />
            <p className="pulse-text">
              {data?.status === "running"
                ? "Analyse en cours — exploration des pages…"
                : "En file d'attente…"}
            </p>
          </div>
        )}

        {/* ── error state ── */}
        {isError && (
          <div className="error-box">
            <h2>Erreur</h2>
            <p>
              {fetchError ||
                data?.error?.slice(0, 300) ||
                "Une erreur est survenue pendant l'analyse."}
            </p>
          </div>
        )}

        {/* ── done state ── */}
        {isDone && data && (
          <>
            {/* score ring */}
            <div className="score-section">
              <ScoreRing score={data.score ?? 0} />
              {data.label && (
                <span className={`label-badge ${labelClass(data.label)}`}>
                  {data.label}
                </span>
              )}
            </div>

            {/* actions */}
            <div className="actions-row">
              {data.report_url && (
                <a
                  href={`/api${data.report_url}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-outline"
                >
                  📄 Rapport HTML
                </a>
              )}
              {data.pdf_url && (
                <span className="btn btn-pro-locked" title="Disponible avec le forfait Pro (79$/mois)">
                  🔒 Télécharger PDF <span className="pro-badge">PRO</span>
                </span>
              )}
              <Link href="/" className="btn btn-outline">
                ← Nouvelle analyse
              </Link>
            </div>

            {/* issues */}
            {data.issues && data.issues.length > 0 && (
              <div className="issues-section">
                <h2>Problèmes détectés ({data.issues.length})</h2>
                {data.issues.map((issue, i) => (
                  <div key={i} className="issue-row">
                    <span className={pillClass(issue.priority)}>
                      {issue.priority}
                    </span>
                    <div className="issue-body">
                      <h3>{issue.title}</h3>
                      <p className="issue-detail">{issue.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* CTA */}
            <div className="cta-card">
              <h3>Passer au Pro</h3>
              <p className="cta-price">79$ / mois</p>
              <div className="cta-features">
                <span>140 pages analysées</span>
                <span>Export PDF</span>
                <span>Re-scan mensuel</span>
                <span>Alertes par courriel</span>
              </div>
              <button className="btn btn-primary" disabled>
                Bientôt disponible
              </button>
            </div>
          </>
        )}
      </section>

      <footer className="footer">
        QC French-First · Outil d{"'"}auto-audit · Pas un avis juridique ·{" "}
        {new Date().getFullYear()}
      </footer>
    </div>
  );
}
