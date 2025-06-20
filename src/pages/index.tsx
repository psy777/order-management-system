import React, { useState, useEffect } from 'react';
import { Dashboard, OrderForm } from '../components/views';
import { EmailModal } from '../components/features';
import { CogIcon } from '../components/ui';
import { OrderFormData } from '../components/views';

const App = () => {
    const [page, setPage] = useState('dashboard');
    const [orders, setOrders] = useState<OrderFormData[]>([]);
    const [activeOrder, setActiveOrder] = useState<OrderFormData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [allVendors, setAllVendors] = useState<any[]>([]);
    const [allSelectableItems, setAllSelectableItems] = useState<any>({});
    const [itemData, setItemData] = useState<any>({});
    const [packageData, setPackageData] = useState<any>({});
    const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
    const [showSettingsMenu, setShowSettingsMenu] = useState(false);
    const [appSettings, setAppSettings] = useState({ company_name: "Your Company", default_email_body: "" });

    const handleOrderUpdate = (updatedOrderFromServer: OrderFormData) => {
        setOrders(prevOrders => {
            const index = prevOrders.findIndex(o => o.id === updatedOrderFromServer.id);
            if (index !== -1) {
                const newOrders = [...prevOrders];
                newOrders[index] = updatedOrderFromServer;
                return newOrders;
            }
            return [...prevOrders.filter(o => o.id !== updatedOrderFromServer.id), updatedOrderFromServer];
        });
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
                const [ordersRes, vendorsRes, itemsRes, packagesRes, settingsRes] = await Promise.all([
                    fetch('/api/orders'),
                    fetch('/api/vendors'),
                    fetch('/api/items'),
                    fetch('/api/packages'),
                    fetch('/api/settings')
                ]);

                const ordersData = await ordersRes.json();
                const settingsData = await settingsRes.json();
                setAppSettings(settingsData);
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

    
    const navigateTo = (pageName: string) => { setPage(pageName); };
    
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
    
    const deleteOrder = async (orderId: string, deletePayload: any) => {
        const orderToDelete = orders.find(o => o.id === orderId);
        if (!orderToDelete) {
            console.warn("Order not found for deletion:", orderId);
            throw new Error("Order not found locally. Cannot proceed with deletion.");
        }

        const finalPayload = {
            ...deletePayload,
            status: "Deleted",
            statusHistory: [
                ...(deletePayload.statusHistory || orderToDelete.statusHistory || []),
                { status: "Deleted", date: new Date().toISOString() }
            ].filter((item, index, self) =>
                index === self.findIndex((t) => (
                    t.status === item.status && new Date(t.date).getTime() === new Date(item.date).getTime()
                )) || item.status !== "Deleted"
            )
        };
        const deletedEntries = finalPayload.statusHistory.filter((h: any) => h.status === "Deleted");
        if (deletedEntries.length > 1) {
            const latestDeletedEntry = deletedEntries.reduce((latest: any, current: any) => 
                new Date(current.date) > new Date(latest.date) ? current : latest
            );
            finalPayload.statusHistory = [
                ...finalPayload.statusHistory.filter((h: any) => h.status !== "Deleted"),
                latestDeletedEntry
            ];
        }

        await saveOrder(finalPayload); 
    };
    
    const viewOrder = (order: OrderFormData) => { setActiveOrder(order); navigateTo('viewOrder'); };
    
    if (isLoading) {
        return <div className="text-center p-8">Loading...</div>;
    }

    const renderPage = () => {
        switch(page) {
            case 'createOrder': return <OrderForm navigateTo={navigateTo} saveOrder={saveOrder} deleteOrder={deleteOrder} allVendors={allVendors} allSelectableItems={allSelectableItems} itemData={itemData} packageData={packageData} fetchAndUpdateVendors={fetchAndUpdateVendors} setOrderForEmailModal={setOrderForEmailModal} />;
            case 'viewOrder': return <OrderForm order={activeOrder as OrderFormData} navigateTo={navigateTo} saveOrder={saveOrder} deleteOrder={deleteOrder} allVendors={allVendors} allSelectableItems={allSelectableItems} itemData={itemData} packageData={packageData} fetchAndUpdateVendors={fetchAndUpdateVendors} setOrderForEmailModal={setOrderForEmailModal} />;
            case 'settings': return <iframe src="/settings.html" style={{ width: '100%', height: 'calc(100vh - 100px)', border: 'none' }}></iframe>;
            case 'manage-customers': return <iframe src="/manage_customers.html" style={{ width: '100%', height: 'calc(100vh - 100px)', border: 'none' }}></iframe>;
            case 'manage-items': return <iframe src="/manage_items.html" style={{ width: '100%', height: 'calc(100vh - 100px)', border: 'none' }}></iframe>;
            case 'manage-packages': return <iframe src="/manage_packages.html" style={{ width: '100%', height: 'calc(100vh - 100px)', border: 'none' }}></iframe>;
            case 'dashboard': default: return <Dashboard orders={orders} navigateTo={navigateTo} viewOrder={viewOrder} allVendors={allVendors} allSelectableItems={allSelectableItems} setOrderForEmailModal={setOrderForEmailModal} />;
        }
    }

    const handleLogout = async () => {
        try {
            const response = await fetch('/shutdown', { method: 'POST' });
            if (response.ok) {
                document.body.innerHTML = '<div style="text-align: center; padding: 50px; font-family: sans-serif; font-size: 1.2em; color: #333;">Application has been shut down. You can now close this tab.</div>';
            } else {
                alert('Failed to send shutdown signal to the server (server responded with an error). Please close the tab manually.');
            }
        } catch (error) {
            console.error('Error during shutdown attempt:', error);
            alert('Error attempting to shut down the server. Please use Task Manager to stop the task.');
        }
    };

    return (
        <div className="bg-slate-50 min-h-screen font-sans">
            {orderForEmailModal && (
                <EmailModal
                    order={orderForEmailModal}
                    allItems={allSelectableItems}
                    appSettings={appSettings}
                    saveOrder={saveOrder}
                    onClose={() => setOrderForEmailModal(null)}
                    onOrderUpdatedAfterEmail={handleOrderUpdate}
                    onEmailClientOpened={() => {
                        setOrderForEmailModal(null);
                        navigateTo('dashboard'); 
                    }}
                />
            )}
            <div className="max-w-7xl mx-auto p-4 sm:p-6 lg:p-8">
                {renderPage()}
            </div>
            {page !== 'settings' && (
            <div className="fixed bottom-4 left-4 z-50">
                <button
                    onClick={() => setShowSettingsMenu(!showSettingsMenu)}
                    className="p-3 bg-slate-600 text-white rounded-full shadow-lg hover:bg-slate-700 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-opacity-75"
                    aria-label="Settings"
                >
                    <CogIcon />
                </button>
                {showSettingsMenu && (
                    <div 
                        className="absolute bottom-full left-0 mb-2 w-48 bg-white rounded-md shadow-lg py-1 ring-1 ring-black ring-opacity-5 focus:outline-none"
                        role="menu"
                        aria-orientation="vertical"
                        aria-labelledby="options-menu"
                    >
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                navigateTo('settings');
                                setShowSettingsMenu(false);
                            }}
                            className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                            role="menuitem"
                        >
                            Settings
                        </a>
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                navigateTo('manage-customers');
                                setShowSettingsMenu(false);
                            }}
                            className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                            role="menuitem"
                        >
                            Manage Customers
                        </a>
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                navigateTo('manage-items');
                                setShowSettingsMenu(false);
                            }}
                            className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                            role="menuitem"
                        >
                            Manage Items
                        </a>
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                navigateTo('manage-packages');
                                setShowSettingsMenu(false);
                            }}
                            className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                            role="menuitem"
                        >
                            Manage Packages
                        </a>
                        <button
                            onClick={() => {
                                handleLogout();
                                setShowSettingsMenu(false);
                            }}
                            className="block w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                            role="menuitem"
                        >
                            Log Out
                        </button>
                    </div>
                )}
            </div>
            )}
        </div>
    );
};

export default App;
