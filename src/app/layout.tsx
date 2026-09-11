import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CodeGraphix | Codebase RAG",
  description: "Multi-tenant code intelligence with AST-aware retrieval and dependency context.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
