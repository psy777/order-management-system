'use client';

import React, { useState, useEffect } from 'react';
import Layout from './Layout';
import { Dashboard } from './views';
import { OrderFormData } from './views';
import { useAuth } from '@clerk/nextjs';

export default function AppPage() {
    const [orders, setOrders] = useState<OrderFormData[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [allVendors, setAllVendors] = useState<any[]>([]);
    const [allSelectableItems, setAllSelectableItems] = useState<any>({});
    const [itemData, setItemData] = useState<any>({});
    const [packageData, setPackageData] = useState<any>({});
    const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
    const [appSettings, setAppSettings] = useState<any>({ company_name: "", default_email_body: "" });
    const { isSignedIn } = useAuth();

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
        const fetchAppSettings = async () => {
            try {
                const settingsRes = await fetch('/api/settings');
                if (settingsRes.ok) {
                    const settings = await settingsRes.json();
                    setAppSettings(settings);
                }
            } catch (error) {
                console.error("Failed to fetch settings:", error);
            }
        };

        const fetchData = async () => {
            setIsLoading(true);
            try {
                await fetchAppSettings();
                const [ordersRes, vendorsRes, itemsRes, packagesRes] = await Promise.all([
                    fetch(`/api/orders`),
                    fetch(`/api/vendors`),
                    fetch(`/api/items`),
                    fetch(`/api/packages`),
                ]);

                if (!itemsRes.ok) {
                    console.error("Failed to fetch items, status:", itemsRes.status);
                    setOrders([]);
                    setAllVendors([]);
                    setAllSelectableItems({});
                    setItemData({});
                    setPackageData({});
                    setIsLoading(false);
                    return;
                }

                const ordersData = await ordersRes.json();
                const vendorsData = await vendorsRes.json();
                const itemsDataArray = await itemsRes.json();
                const packagesData = await packagesRes.json();

                if (!Array.isArray(itemsDataArray)) {
                    console.error("Items data is not an array:", itemsDataArray);
                    setIsLoading(false);
                    return;
                }

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

        if (isSignedIn) {
            fetchData();
        }
    }, [isSignedIn]);

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

    if (isLoading) {
        return <div className="text-center p-8">Loading...</div>;
    }

    return (
        <Layout
            appSettings={appSettings}
            orderForEmailModal={orderForEmailModal}
            allSelectableItems={allSelectableItems}
            handleOrderSent={handleOrderSent}
            setOrderForEmailModal={setOrderForEmailModal}
        >
            <Dashboard
                orders={orders}
                allVendors={allVendors}
                allSelectableItems={allSelectableItems}
                setOrderForEmailModal={setOrderForEmailModal}
            />
        </Layout>
    );
}
