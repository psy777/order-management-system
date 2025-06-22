import App, { AppContext, AppInitialProps } from 'next/app';
import type { AppProps } from 'next/app';
import Head from 'next/head';
import Script from 'next/script';
import '../styles/globals.css';
import Layout from '../components/Layout';
import React, { useState, useEffect } from 'react';
import { OrderFormData } from '../components/views';

function MyApp({ Component, pageProps, appSettings: initialAppSettings }: AppProps & { appSettings: any }) {
    const [orders, setOrders] = useState<OrderFormData[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [allVendors, setAllVendors] = useState<any[]>([]);
    const [allSelectableItems, setAllSelectableItems] = useState<any>({});
    const [itemData, setItemData] = useState<any>({});
    const [packageData, setPackageData] = useState<any>({});
    const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
    const [appSettings, setAppSettings] = useState(initialAppSettings);

    const handleOrderSent = (updatedOrder: OrderFormData) => {
        saveOrder(updatedOrder);
        setOrderForEmailModal(null);
    };

    const fetchAndUpdateVendors = async () => {
        try {
            const vendorsRes = await fetch('/api/vendors');
            const vendorsData = await vendorsRes.json();
            setAllVendors(vendorsData);
        } catch (error) {
            console.error("Failed to re-fetch vendors:", error);
        }
    };

    useEffect(() => {
        const fetchData = async () => {
            setIsLoading(true);
            try {
                const [ordersRes, vendorsRes, itemsRes, packagesRes] = await Promise.all([
                    fetch('/api/orders'),
                    fetch('/api/vendors'),
                    fetch('/api/items'),
                    fetch('/api/packages'),
                ]);

                const ordersData = await ordersRes.json();
                const vendorsData = await vendorsRes.json();
                const itemsDataArray = await itemsRes.json();
                const packagesData = await packagesRes.json();

                const itemsDataById = itemsDataArray.reduce((acc: any, item: any) => {
                    acc[item.item_code] = item;
                    return acc;
                }, {});

                setOrders(ordersData);
                setAllVendors(vendorsData);
                const combinedSelectableItems = { ...itemsDataById };
                for (const pkgId in packagesData) {
                    combinedSelectableItems[pkgId] = { 
                        name: packagesData[pkgId].name, 
                        type: packagesData[pkgId].type, 
                        styles: []
                    };
                }
                setAllSelectableItems(combinedSelectableItems);
                setItemData(itemsDataById);
                setPackageData(packagesData);
            } catch (error) {
                console.error("Failed to fetch data:", error);
            } finally {
                setIsLoading(false);
            }
        };
        fetchData();
    }, []);

    const saveOrder = async (orderToSave: OrderFormData) => {
        try {
            const response = await fetch('/api/orders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(orderToSave)
            });
            const result = await response.json();
            if (result.status === 'success' && result.order) {
                const savedOrderFromServer = result.order;
                setOrders(prevOrders => {
                    const index = prevOrders.findIndex(o => o.id === savedOrderFromServer.id);
                    if (index !== -1) {
                        const updatedOrders = [...prevOrders];
                        updatedOrders[index] = savedOrderFromServer;
                        return updatedOrders;
                    } else {
                        return [savedOrderFromServer, ...prevOrders.filter(o => o.id !== savedOrderFromServer.id)];
                    }
                });
            } else {
                console.error("Failed to save order - server response:", result.message || "Unknown server error");
                throw new Error(result.message || "Failed to save order on server.");
            }
        } catch (error) {
            console.error("Failed to save order - network/fetch error:", error);
            throw error;
        }
    };

    const deleteOrder = async (orderId: string) => {
        try {
            const response = await fetch(`/api/orders/${orderId}`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (result.status === 'success') {
                setOrders(prevOrders => prevOrders.filter(o => o.id !== orderId));
            } else {
                console.error("Failed to delete order - server response:", result.message || "Unknown server error");
                throw new Error(result.message || "Failed to delete order on server.");
            }
        } catch (error) {
            console.error("Failed to delete order - network/fetch error:", error);
            throw error;
        }
    };

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
      <Layout
        appSettings={appSettings}
        orderForEmailModal={orderForEmailModal}
        allSelectableItems={allSelectableItems}
        handleOrderSent={handleOrderSent}
        setOrderForEmailModal={setOrderForEmailModal}
      >
        <Component {...pageProps}
            appSettings={appSettings}
            orders={orders}
            allVendors={allVendors}
            allSelectableItems={allSelectableItems}
            itemData={itemData}
            packageData={packageData}
            setOrderForEmailModal={setOrderForEmailModal}
            saveOrder={saveOrder}
            deleteOrder={deleteOrder}
            fetchAndUpdateVendors={fetchAndUpdateVendors}
            isLoading={isLoading}
        />
      </Layout>
    </>
  );
}

MyApp.getInitialProps = async (
    context: AppContext
): Promise<AppInitialProps & { appSettings: any }> => {
    const ctx = await App.getInitialProps(context);

    let appSettings = { company_name: "Your Company", default_email_body: "" };
    try {
        const isServer = typeof window === 'undefined';
        const baseUrl = isServer ? (process.env.NEXT_PUBLIC_VERCEL_URL ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}` : 'http://localhost:3000') : '';
        const settingsRes = await fetch(`${baseUrl}/api/settings`);
        if (settingsRes.ok) {
            appSettings = await settingsRes.json();
        }
    } catch (error) {
        console.error("Failed to fetch settings in getInitialProps:", error);
    }

    return { ...ctx, appSettings };
};

export default MyApp;
