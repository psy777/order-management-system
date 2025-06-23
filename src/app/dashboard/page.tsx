'use client';

import React, { useState, useEffect } from 'react';
import { Dashboard } from '../../components/views';
import { OrderFormData } from '../../components/views';
import { useAuth } from '@clerk/nextjs';

export default function DashboardPage() {
    const [orders, setOrders] = useState<OrderFormData[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [allVendors, setAllVendors] = useState<any[]>([]);
    const [allSelectableItems, setAllSelectableItems] = useState<any>({});
    const [itemData, setItemData] = useState<any>({});
    const [packageData, setPackageData] = useState<any>({});
    const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
    const { isSignedIn } = useAuth();

    useEffect(() => {
        const fetchData = async () => {
            setIsLoading(true);
            try {
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

    if (isLoading) {
        return <div className="text-center p-8">Loading...</div>;
    }

    return (
        <Dashboard
            orders={orders}
            allVendors={allVendors}
            allSelectableItems={allSelectableItems}
            setOrderForEmailModal={setOrderForEmailModal}
        />
    );
}
