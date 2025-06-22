import type { Metadata } from "next";
import {
  ClerkProvider,
} from "@clerk/nextjs";
import "../styles/globals.css";
import React from "react";

export const metadata: Metadata = {
  title: "Order Management System",
  description: "Order Management System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body>
            {children}
        </body>
      </html>
    </ClerkProvider>
  );
}
