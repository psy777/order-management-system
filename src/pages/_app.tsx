import type { AppProps } from 'next/app';
import Head from 'next/head';
import Script from 'next/script';
import '../styles/globals.css';

function MyApp({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        <meta charSet="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Order Management System</title>
        <link rel="icon" href="/assets/favicon.ico" sizes="any" />
        <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png" />
        <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png" />
        <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png" />
        <link rel="manifest" href="/assets/site.webmanifest" />
      </Head>
      <Script src="https://unpkg.com/react@18/umd/react.development.js" strategy="beforeInteractive"></Script>
      <Script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js" strategy="beforeInteractive"></Script>
      <Script src="https://unpkg.com/@babel/standalone/babel.min.js" strategy="beforeInteractive"></Script>
      <Script src="https://cdn.tailwindcss.com" strategy="beforeInteractive"></Script>
      <Script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js" strategy="beforeInteractive"></Script>
      <Script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.5.23/jspdf.plugin.autotable.min.js" strategy="beforeInteractive"></Script>
      <Script src="https://cdn.jsdelivr.net/npm/chart.js" strategy="beforeInteractive"></Script>
      <Component {...pageProps} />
    </>
  );
}

export default MyApp;
