import "@copilotkit/react-core/v2/styles.css";
import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "Orders · Agent Hangar + CopilotKit" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
