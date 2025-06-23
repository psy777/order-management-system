'use client';

import React, { useState, useEffect } from 'react';
import { OrderForm } from '../../components/views';
import { OrderFormData } from '../../components/views';
import { useAuth } from '@clerk/nextjs';

export default function CreateOrderPage() {
    const [isLoading, setIsLoading] = useState(true);
    const [allVendors, setAllVendors] = useState<any[]>([]);
    const [allSelectableItems, setAllSelectableItems] = useState<any>({});
    const [itemData, setItemData] = useState<any>({});
    const [packageData, setPackageData] = useState<any>({});
    const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
    const { isSignedIn } = useAuth();

    const saveOrder = async (orderToSave: OrderFormData) => {
        try {
            const response = await fetch('/api/orders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(orderToSave)
            });
            const result = await response.json();
            if (result.status !== 'success') {
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
            if (result.status !== 'success') {
                console.error("Failed to delete order - server response:", result.message || "Unknown server error");
                throw new Error(result.message || "Failed to delete order on server.");
            }
        } catch (error) {
            console.error("Failed to delete order - network/fetch error:", error);
            throw error;
        }
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
                const [vendorsRes, itemsRes, packagesRes] = await Promise.all([
                    fetch(`/api/vendors`),
                    fetch(`/api/items`),
                    fetch(`/api/packages`),
                ]);

                if (!itemsRes.ok) {
                    console.error("Failed to fetch items, status:", itemsRes.status);
                    setAllVendors([]);
                    setAllSelectableItems({});
                    setItemData({});
                    setPackageData({});
                    setIsLoading(false);
                    return;
                }

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

    if (isLoading) {
        return <div className="text-center p-8">Loading...</div>;
    }

    return (
        <OrderForm
            saveOrder={saveOrder}
            deleteOrder={deleteOrder}
            allVendors={allVendors}
            allSelectableItems={allSelectableItems}
            itemData={itemData}
            packageData={packageData}
            fetchAndUpdateVendors={fetchAndUpdateVendors}
            setOrderForEmailModal={setOrderForEmailModal}
        />
    );
}
