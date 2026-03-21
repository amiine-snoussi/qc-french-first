import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QC French-First — Scanner Loi 96",
  description:
    "Vérifiez la conformité de votre site web aux exigences linguistiques du Québec (Loi 96 / Bill 96).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <body>
        <div className="page-bg" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
