import { ClerkProvider } from '@clerk/nextjs';
import '../styles/globals.css';
import React from 'react';
import Layout from '../components/Layout';
import { AppProvider } from '../context/AppContext';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClerkProvider>
      <AppProvider>
        <html lang="en">
          <body>
            <Layout>
              {children}
            </Layout>
          </body>
        </html>
      </AppProvider>
    </ClerkProvider>
  );
}
