import React, { useState, useMemo, useEffect, useRef } from 'react';
import { Card, Input, Select, Textarea, TrashIcon, DollarSignIcon, DocumentTextIcon, ChartBarIcon, ViewIcon, PdfIcon, EmailIcon, TrendingUpIcon, CogIcon } from './ui';
import { PriceInput, ScentToggle, NameDropToggle, StatusBar } from './shared';
import { SalesChart, SignaturePad, EmailModal, ShippedStatusBadge } from './features';
import { generatePdf } from '../lib/pdf';

// --- INTERFACES ---
interface VendorInfo {
    id?: string;
    companyName: string;
    contactName: string;
    email: string;
    phone: string;
    billingAddress: string;
    billingCity: string;
    billingState: string;
    billingZipCode: string;
    shippingAddress: string;
    shippingCity: string;
    shippingState: string;
    shippingZipCode: string;
}

interface LineItem {
    id: number;
    item: string;
    style: string;
    type: string;
    quantity: number;
    price: number;
    packageCode: string | null;
}

export interface StatusHistory {
    status: string;
    date: string;
}

export interface OrderFormData {
    id?: string;
    date?: string;
    vendorInfo: VendorInfo;
    lineItems: LineItem[];
    notes: string;
    estimatedShippingDate: string;
    estimatedShipping: string;
    scentOption: string;
    nameDrop: boolean;
    signatureDataUrl: string | null;
    statusHistory: StatusHistory[];
    status: string;
    total?: number;
    deleteConfirmation?: string;
}

// --- COMPONENTS ---
export const Dashboard = ({ orders, navigateTo, viewOrder, allVendors, allSelectableItems, setOrderForEmailModal }: { orders: any[], navigateTo: (page: string) => void, viewOrder: (order: any) => void, allVendors: any[], allSelectableItems: any, setOrderForEmailModal: (order: any) => void }) => {
    const [filteredOrders, setFilteredOrders] = useState(orders);
    const [dashboardStats, setDashboardStats] = useState({ totalRevenue: 0, averageOrderRevenue: 0, totalOrders: 0 });

    useEffect(() => {
        setFilteredOrders(orders);
        const fetchDashboardStats = async () => {
            try {
                const response = await fetch('/api/dashboard-stats');
                const data = await response.json();
                setDashboardStats(data);
            } catch (error) {
                console.error("Failed to fetch dashboard stats:", error);
                setDashboardStats({ totalRevenue: 0, averageOrderRevenue: 0, totalOrders: orders.length });
            }
        };
        fetchDashboardStats();
    }, [orders]);

    const formatCurrency = (amountInDollars: number) => { 
        const numericAmount = typeof amountInDollars === 'number' ? amountInDollars : parseFloat(amountInDollars as any) || 0;
        return numericAmount.toLocaleString('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    };

    return (
        <React.Fragment>
            <div className="flex justify-between items-center mb-6">
                <h1 className="text-3xl font-bold text-slate-800">Dashboard</h1>
                <div className="flex space-x-2">
                    <button onClick={() => navigateTo('createOrder')} className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow">+ Create New Order</button>
                </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
                <Card title="Total Revenue" value={formatCurrency(dashboardStats.totalRevenue)} icon={<TrendingUpIcon />} />
                <Card title="Total Orders" value={dashboardStats.totalOrders} icon={<DocumentTextIcon />} />
                <Card title="Avg. Order Value" value={formatCurrency(dashboardStats.averageOrderRevenue)} icon={<ChartBarIcon />} />
            </div>
            <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                <h2 className="text-xl font-semibold text-slate-700 mb-4">All Orders</h2>
                <div className="overflow-x-auto"><table className="w-full text-sm text-left text-slate-500"><thead className="text-xs text-slate-700 uppercase bg-slate-100"><tr><th className="px-4 py-3">Order ID</th><th className="px-4 py-3">Customer</th><th className="px-4 py-3">Date</th><th className="px-4 py-3">Total</th><th className="px-4 py-3">Status</th><th className="px-4 py-3 text-center">Actions</th></tr></thead>
                    <tbody>{filteredOrders.map(order => (<tr key={order.id} className="bg-white border-b hover:bg-slate-50">
                        <td className="px-4 py-3 font-medium text-slate-800">{order.id}</td><td className="px-4 py-3">{order.vendorInfo.companyName}</td>
                        <td className="px-4 py-3">{new Date(order.date).toLocaleDateString()}</td><td className="px-4 py-3">${parseFloat(order.total || 0).toFixed(2)}</td>
                        <td className="px-4 py-3">
                            {order.status === 'Shipped' ? (
                                <ShippedStatusBadge statusText={order.status} />
                            ) : (
                                <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                                    order.status === 'Paid' ? 'bg-green-100 text-green-800' :
                                    order.status === 'Sent' ? 'bg-blue-100 text-blue-800' :
                                    'bg-slate-100 text-slate-800'
                                }`}>{order.status}</span>
                            )}
                        </td>
                        <td className="px-4 py-3 text-center"><div className="flex items-center justify-center space-x-2"><button onClick={() => viewOrder(order)} className="p-1 text-slate-500 hover:text-orange-600"><ViewIcon /></button><button onClick={() => generatePdf(order, allSelectableItems, 'preview')} className="p-1 text-slate-500 hover:text-orange-600"><PdfIcon /></button><button onClick={() => setOrderForEmailModal(order)} className="p-1 text-slate-500 hover:text-orange-600"><EmailIcon /></button></div></td>
                    </tr>))}</tbody>
                </table></div>
            </div>
        </React.Fragment>
    );
};

export const UnsavedChangesModal = ({ onCancel, onDelete, onSaveAndClose }: { onCancel: () => void, onDelete: () => void, onSaveAndClose: () => void }) => {
    return (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex justify-center items-center p-4">
            <div className="bg-white rounded-lg shadow-xl p-8 w-full max-w-md">
                <h2 className="text-xl font-bold text-slate-800 mb-4">You have unsaved changes.</h2>
                <p className="text-slate-600 mb-6">What would you like to do?</p>
                <div className="flex justify-end space-x-4">
                    <button onClick={onCancel} className="px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors">Cancel</button>
                    <button onClick={onDelete} className="px-4 py-2 bg-red-600 text-white font-semibold rounded-md hover:bg-red-700 transition-colors">Delete</button>
                    <button onClick={onSaveAndClose} className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors">Save and Close</button>
                </div>
            </div>
        </div>
    );
};

export const OrderForm = ({ order, navigateTo, saveOrder, deleteOrder, allVendors, allSelectableItems, itemData, packageData, fetchAndUpdateVendors, setOrderForEmailModal }: { order?: OrderFormData, navigateTo: (page: string) => void, saveOrder: (order: any) => Promise<void>, deleteOrder: (orderId: string, deletePayload: any) => Promise<void>, allVendors: any[], allSelectableItems: any, itemData: any, packageData: any, fetchAndUpdateVendors: () => Promise<void>, setOrderForEmailModal: (order: any) => void }) => {
    const [isEditing, setIsEditing] = useState(!order || order.status === 'Draft');
    const [formData, setFormData] = useState<OrderFormData>(order ? {...order, estimatedShipping: order.estimatedShipping || '', scentOption: order.scentOption || 'Scented'} : { vendorInfo: { companyName: '', contactName: '', email: '', phone: '', billingAddress: '', billingCity: '', billingState: '', billingZipCode: '', shippingAddress: '', shippingCity: '', shippingState: '', shippingZipCode: '' }, lineItems: [], notes: "", estimatedShippingDate: '', estimatedShipping: '', scentOption: 'Scented', nameDrop: false, signatureDataUrl: null, statusHistory: [{ status: 'Draft', date: new Date().toISOString() }], status: 'Draft' });
    const [vendorSuggestions, setVendorSuggestions] = useState<any[]>([]);
    const [showUnsavedChangesModal, setShowUnsavedChangesModal] = useState(false);
    const scanInputRef = useRef<HTMLInputElement>(null);
    const [scanInput, setScanInput] = useState('');
    const [sameAsShipping, setSameAsShipping] = useState(true);
    const [isAutofilledVendorActive, setIsAutofilledVendorActive] = useState(false);
    const [initialVendorInfoAfterAutofill, setInitialVendorInfoAfterAutofill] = useState<any | null>(null);
    const [isEditingAutofilledVendor, setIsEditingAutofilledVendor] = useState(false);
    const [isVendorInputActive, setIsVendorInputActive] = useState(false);
    const hideSuggestionsTimeoutRef = useRef<any>(null);

    const handleReturnToDashboard = () => {
        const isNewUnsavedDraft = !order && (formData.lineItems.length > 0 || (formData.vendorInfo && formData.vendorInfo.companyName));

        if (isNewUnsavedDraft) {
            setShowUnsavedChangesModal(true);
        } else {
            navigateTo('dashboard');
        }
    };

    useEffect(() => {
        const debounce = (func: Function, delay: number) => {
            let timeoutId: any;
            return (...args: any) => {
                clearTimeout(timeoutId);
                timeoutId = setTimeout(() => {
                    func.apply(this, args);
                }, delay);
            };
        };

        const debouncedFetchShipping = debounce(fetchEstimatedShipping, 750);

        if (formData.vendorInfo.shippingZipCode && formData.lineItems) {
            if (formData.vendorInfo.shippingZipCode.length === 5 && /^\d{5}$/.test(formData.vendorInfo.shippingZipCode) && formData.lineItems.length > 0) {
                debouncedFetchShipping(formData.vendorInfo.shippingZipCode, formData.lineItems);
            } else if (formData.vendorInfo.shippingZipCode.length === 5 && /^\d{5}$/.test(formData.vendorInfo.shippingZipCode) && formData.lineItems.length === 0) {
                setFormData(prev => ({ ...prev, estimatedShipping: '0.00' }));
            }
        }
    }, [formData.vendorInfo.shippingZipCode, formData.lineItems]);

    const fetchEstimatedShipping = async (zipCode: string, items: LineItem[]) => {
        if (!zipCode || zipCode.length !== 5 || !/^\d{5}$/.test(zipCode)) {
            return;
        }
        if (!items || items.length === 0) {
            setFormData(prev => ({ ...prev, estimatedShipping: '0.00' }));
            return;
        }

        try {
            const response = await fetch('/api/calculate-shipping-estimate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shippingZipCode: zipCode, lineItems: items })
            });
            const data = await response.json();
            if (response.ok) {
                setFormData(prev => ({ ...prev, estimatedShipping: data.estimatedShipping.toString() }));
            } else {
                console.error("Failed to fetch shipping estimate:", data.message);
            }
        } catch (error) {
            console.error("Error fetching shipping estimate:", error);
        }
    };
    
    const formatPhoneNumber = (value: string) => {
        if (!value) return value;
        const phoneNumber = value.replace(/[^\d]/g, '');
        const phoneNumberLength = phoneNumber.length;
        if (phoneNumberLength < 4) return phoneNumber;
        if (phoneNumberLength < 7) {
            return `(${phoneNumber.slice(0, 3)}) ${phoneNumber.slice(3)}`;
        }
        return `(${phoneNumber.slice(0, 3)}) ${phoneNumber.slice(3, 6)}-${phoneNumber.slice(6, 10)}`;
    };

    let nextId = useMemo(() => formData.lineItems.length > 0 ? Math.max(...formData.lineItems.map(i => i.id)) + 1 : 1, [formData.lineItems]);

    const handleVendorInputFocus = () => {
        clearTimeout(hideSuggestionsTimeoutRef.current);
        setIsVendorInputActive(true);
    };

    const handleVendorInputBlur = () => {
        hideSuggestionsTimeoutRef.current = setTimeout(() => {
            setIsVendorInputActive(false);
        }, 150); 
    };
    
    const unpackPackage = (id: number, pkgCode: string) => {
        const pkg = packageData[pkgCode] || { contents: [] };

        const newItems = pkg.contents.map(({ itemCode, quantity }: { itemCode: string, quantity: number }) => {
            const info = itemData[itemCode] || {};
            const selectableInfo = allSelectableItems[itemCode] || {};
            return {
                id:       nextId++,
                item:     itemCode,
                style:    (info.styles && info.styles[0]) || '',
                type:     selectableInfo.type || 'cross',
                quantity: quantity,
                price:    (info.price || 0),
                packageCode: pkgCode,
            };
        });

        setFormData(prev => ({
            ...prev,
            lineItems: [
                ...prev.lineItems.filter(item => item.id !== id),
                ...newItems
            ]
        }));
    };

    const handleLineItemChange = (id: number, field: string, value: any) => {
        if (field === 'item' && allSelectableItems[value]?.type === 'package') {
            unpackPackage(id, value);
        } else {
             setFormData(prev => ({...prev, lineItems: prev.lineItems.map(item => {
                 if (item.id !== id) return item;
                 const updatedItem = {...item, [field]: value};
                 if (field === 'item' && itemData[value]) {
                     updatedItem.style = itemData[value].styles?.[0] || '';
                     updatedItem.price = (itemData[value].price || 0);
                     updatedItem.type = allSelectableItems[value]?.type || 'cross';
                 }
                 return updatedItem;
             })}));
        }
    };
    
    const handleScanAddItem = (event?: React.FormEvent) => {
        if (event) event.preventDefault();

        const itemCode = scanInput.trim().toUpperCase();
        if (allSelectableItems[itemCode]) {
            if (allSelectableItems[itemCode].type === 'package') {
                unpackPackage(nextId++, itemCode);
            } else {
                setFormData(prev => ({ ...prev, lineItems: [...prev.lineItems, { id: nextId++, item: itemCode, style: itemData[itemCode]?.styles[0] || '', type: allSelectableItems[itemCode]?.type || 'cross', quantity: 1, price: (itemData[itemCode]?.price || 0), packageCode: null }] })); 
            }
            setScanInput('');
        } else { 
            console.log("Item code not found:", itemCode); 
        }
        if (scanInputRef.current) {
            scanInputRef.current.focus();
        }
    };
    
    const addEmptyLineItem = () => { setFormData(prev => ({ ...prev, lineItems: [...prev.lineItems, { id: nextId++, item: '-- Select Item --', style: '', type: '', quantity: 1, price: 0, packageCode: null }] })); };
    const removeLineItem = (id: number) => { setFormData(prev => ({ ...prev, lineItems: prev.lineItems.filter(item => item.id !== id) })); };

    const handleVendorInfoChange = (field: string, value: string) => {
        let processedValue = value;
        if (field === 'phone') {
            processedValue = formatPhoneNumber(value);
        }
        const newVendorInfo = {...formData.vendorInfo, [field]: processedValue};
        if (field === 'billingAddress' && sameAsShipping) {
            newVendorInfo.shippingAddress = processedValue;
        }
        if (field === 'billingAddress' && sameAsShipping) {
            newVendorInfo.shippingAddress = newVendorInfo.billingAddress;
        }

        setFormData(prev => ({ ...prev, vendorInfo: newVendorInfo }));

        if (isAutofilledVendorActive) {
            const currentVendorString = JSON.stringify(newVendorInfo);
            const initialVendorString = JSON.stringify(initialVendorInfoAfterAutofill);
            if (currentVendorString !== initialVendorString) {
                setIsEditingAutofilledVendor(true);
            } else {
                setIsEditingAutofilledVendor(false);
            }
        } else {
            setIsEditingAutofilledVendor(false);
        }

        if (value.length > 1 && (field === 'companyName' || field === 'contactName' || field === 'email')) {
            const searchResults = allVendors.filter(vendor => {
                const searchLower = value.toLowerCase();
                return (vendor.companyName && vendor.companyName.toLowerCase().includes(searchLower)) ||
                       (vendor.contactName && vendor.contactName.toLowerCase().includes(searchLower)) ||
                       (vendor.email && vendor.email.toLowerCase().includes(searchLower));
            });
            setVendorSuggestions(searchResults);
        } else { setVendorSuggestions([]); }
    };

    const handleSameAsShippingChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const isChecked = e.target.checked;
        setSameAsShipping(isChecked);
        setFormData(prev => ({
            ...prev,
            vendorInfo: {
                ...prev.vendorInfo,
                billingAddress: isChecked ? prev.vendorInfo.shippingAddress : '',
                billingCity: isChecked ? prev.vendorInfo.shippingCity : '',
                billingState: isChecked ? prev.vendorInfo.shippingState : '',
                billingZipCode: isChecked ? prev.vendorInfo.shippingZipCode : ''
            }
        }));
    };
    
    useEffect(() => {
        if (order && order.vendorInfo) {
            if (order.vendorInfo.shippingAddress && order.vendorInfo.shippingAddress === order.vendorInfo.billingAddress) {
                setSameAsShipping(true);
            } else if (order.vendorInfo.shippingAddress && !order.vendorInfo.billingAddress) {
                setSameAsShipping(true);
                setFormData(prev => ({
                    ...prev,
                    vendorInfo: {
                        ...prev.vendorInfo,
                        billingAddress: prev.vendorInfo.shippingAddress,
                        billingCity: prev.vendorInfo.shippingCity,
                        billingState: prev.vendorInfo.shippingState,
                        billingZipCode: prev.vendorInfo.shippingZipCode
                    }
                }));
            }
            else {
                setSameAsShipping(false);
            }
        } else {
            setSameAsShipping(true);
             setFormData(prev => ({
                ...prev,
                vendorInfo: {
                    ...prev.vendorInfo,
                    billingAddress: prev.vendorInfo.shippingAddress,
                    billingCity: prev.vendorInfo.shippingCity,
                    billingState: prev.vendorInfo.shippingState,
                    billingZipCode: prev.vendorInfo.shippingZipCode
                }
            }));
        }
    }, [order]);


    const selectVendor = (vendor: any) => {
        clearTimeout(hideSuggestionsTimeoutRef.current);
        setFormData(prev => {
            const newVendorInfo = {...vendor};
            if (sameAsShipping) {
                newVendorInfo.billingAddress = newVendorInfo.shippingAddress;
                newVendorInfo.billingCity = newVendorInfo.shippingCity;
                newVendorInfo.billingState = newVendorInfo.shippingState;
                newVendorInfo.billingZipCode = newVendorInfo.shippingZipCode;
            } else {
                newVendorInfo.billingAddress = prev.vendorInfo.billingAddress || vendor.billingAddress || '';
                newVendorInfo.billingCity = prev.vendorInfo.billingCity || vendor.billingCity || '';
                newVendorInfo.billingState = prev.vendorInfo.billingState || vendor.billingState || '';
                newVendorInfo.billingZipCode = prev.vendorInfo.billingZipCode || vendor.billingZipCode || '';
            }
            return {...prev, vendorInfo: newVendorInfo};
        }); 
        setVendorSuggestions([]);
        setInitialVendorInfoAfterAutofill(JSON.parse(JSON.stringify(vendor)));
        setIsAutofilledVendorActive(true);
        setIsEditingAutofilledVendor(false);
        setIsVendorInputActive(false);
    }
    
    const orderTotals = useMemo(() => {
        const subtotal = formData.lineItems.reduce((acc, item) => acc + (item.quantity * item.price), 0);
        let nameDropSurcharge = 0;
        let crossItemCount = 0;
        if (formData.nameDrop) {
            formData.lineItems.forEach(item => {
                if (item.type === 'cross') {
                    nameDropSurcharge += item.quantity * 100;
                    crossItemCount += item.quantity;
                }
            });
        }
        const estimatedShipping = parseFloat(formData.estimatedShipping) || 0;
        const estimatedShippingInCents = Math.round(estimatedShipping * 100);

        const liveCalculatedTotal = subtotal + nameDropSurcharge + estimatedShippingInCents;

        return { 
            subtotal,
            nameDropSurcharge,
            estimatedShipping,
            total: liveCalculatedTotal,
            crossItemCount 
        };
    }, [formData.lineItems, formData.nameDrop, formData.estimatedShipping]);

    const isDraft = formData.status === 'Draft';
    const canEdit = isEditing || isDraft;
    
    const handleStatusChange = (newStatus: string) => {
        const newStatusHistory = [...formData.statusHistory, { status: newStatus, date: new Date().toISOString() }];
        const updatedOrder = { ...formData, status: newStatus, statusHistory: newStatusHistory };
        setFormData(updatedOrder);
        saveOrder(updatedOrder);
    };
    
    const createOrderObject = (status?: string) => {
        const finalStatus = status || formData.status;
        const newStatusHistory = formData.statusHistory.find(h => h.status === finalStatus) ? formData.statusHistory : [...formData.statusHistory, { status: finalStatus, date: new Date().toISOString() }];
        return { ...formData, id: formData.id || `PO-${Date.now()}`, date: formData.date || new Date().toISOString(), status: finalStatus, statusHistory: newStatusHistory, total: orderTotals.total, signatureDataUrl: formData.signatureDataUrl };
    };

    const handleSaveDraft = async () => {
        if (!formData.vendorInfo.companyName) {
            console.log("Order save failed: Please select a vendor.");
            return;
        }
        const newOrder = createOrderObject('Draft');
        try {
            await saveOrder(newOrder);
            navigateTo('dashboard');
        } catch (error: any) {
            console.log(`Failed to save draft: ${error.message}`);
        }
    };
    
    const handleSaveAndSend = async () => {
        if (!formData.vendorInfo.companyName || !formData.vendorInfo.email) { console.log("Order save failed: Please select a vendor with a valid email address."); return; }
        const newOrder = createOrderObject('Sent');
        await saveOrder(newOrder);
        setOrderForEmailModal(newOrder); 
    };

    const handlePreviewPdf = () => { const tempOrder = createOrderObject(); generatePdf(tempOrder, allSelectableItems, 'preview'); };
    
    const handleDelete = async () => {
        if (!formData || !formData.id) {
            console.log("Cannot delete: Order data is missing.");
            return;
        }

        const orderId = formData.id;
        const orderStatus = formData.status;
        const companyName = formData.vendorInfo?.companyName || "";
        const cleanedOrderId = orderId.replace("PO-", "");
        let orderIdDigitsForConfirmation = "";

        if (cleanedOrderId.length >= 4) {
            orderIdDigitsForConfirmation = cleanedOrderId.slice(-4);
        } else if (cleanedOrderId.length > 0) {
            orderIdDigitsForConfirmation = cleanedOrderId;
        }

        let confirmationMessage = "Are you sure you want to delete this order? This action cannot be undone.";
        let requiresSpecialConfirmation = false;
        let expectedConfirmationPhrase = "";

        if (orderStatus !== 'Draft') {
            requiresSpecialConfirmation = true;
            if (!companyName || !orderIdDigitsForConfirmation) {
                 console.log("Cannot proceed with deletion: Company name or Order ID is missing/invalid for confirmation string generation.");
                 return;
            }
            expectedConfirmationPhrase = `delete ${companyName} order ${orderIdDigitsForConfirmation}`;
            confirmationMessage = `To delete this order, please type the following exactly:\n\n"${expectedConfirmationPhrase}"`;
        }

        const userInput = window.prompt(confirmationMessage);

        if (userInput === null) {
            return;
        }

        let deletePayload: OrderFormData = { ...formData, status: "Deleted" };

        if (requiresSpecialConfirmation) {
            if (userInput === expectedConfirmationPhrase) {
                deletePayload.deleteConfirmation = userInput;
            } else {
                console.log("Deletion cancelled: The confirmation phrase was incorrect.");
                return;
            }
        }
        
        try {
            await deleteOrder(orderId, deletePayload);
            navigateTo('dashboard');
        } catch (error: any) {
            console.error("Error during delete operation:", error);
            console.log(`Failed to delete order: ${error.message || 'Unknown error'}`);
        }
    };

    const handleCancelVendorEdit = () => {
        if (initialVendorInfoAfterAutofill) {
            setFormData(prev => ({ ...prev, vendorInfo: JSON.parse(JSON.stringify(initialVendorInfoAfterAutofill)) }));
        }
        setIsEditingAutofilledVendor(false);
    };

    const handleSaveVendorEdits = async () => {
        if (!initialVendorInfoAfterAutofill || !initialVendorInfoAfterAutofill.id) {
            console.log("Cannot save edits: Original vendor ID is missing.");
            return;
        }
        const vendorIdToUpdate = initialVendorInfoAfterAutofill.id;
        const payload = { ...formData.vendorInfo };
        payload.id = vendorIdToUpdate; 

        try {
            const response = await fetch(`/api/vendors/${vendorIdToUpdate}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (response.ok && result.status === 'success') {
                setInitialVendorInfoAfterAutofill(JSON.parse(JSON.stringify(result.vendor)));
                setFormData(prev => ({ ...prev, vendorInfo: JSON.parse(JSON.stringify(result.vendor)) }));
                setIsEditingAutofilledVendor(false);
                if (fetchAndUpdateVendors) fetchAndUpdateVendors();
                console.log("Vendor edits saved successfully.");
            } else {
                console.log(`Failed to save vendor edits: ${result.message || 'Unknown error'}`);
            }
        } catch (error: any) {
            console.error("Error saving vendor edits:", error);
            console.log(`Error saving vendor edits: ${error.message}`);
        }
    };

    const handleCreateNewVendorRecord = async () => {
        const currentVendorData = { ...formData.vendorInfo };
        delete currentVendorData.id; 

        if (!currentVendorData.companyName) {
            console.log("Company name is required to create a new vendor record.");
            return;
        }

        try {
            const response = await fetch('/api/vendors', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentVendorData)
            });
            const result = await response.json();
            if (response.ok && result.status === 'success' && result.vendor) {
                const newVendorFromServer = result.vendor;
                setFormData(prev => ({ ...prev, vendorInfo: JSON.parse(JSON.stringify(newVendorFromServer)) }));
                setInitialVendorInfoAfterAutofill(JSON.parse(JSON.stringify(newVendorFromServer)));
                setIsAutofilledVendorActive(true);
                setIsEditingAutofilledVendor(false);
                if (fetchAndUpdateVendors) fetchAndUpdateVendors();
                console.log(`New vendor "${newVendorFromServer.companyName}" created successfully with ID: ${newVendorFromServer.id}`);
            } else {
                console.log(`Failed to create new vendor record: ${result.message || 'Unknown error'}`);
            }
        } catch (error: any) {
            console.error("Error creating new vendor record:", error);
            console.log(`Error creating new vendor record: ${error.message}`);
        }
    };

    const handleClearAllVendorInputs = () => {
        setFormData(prev => ({
            ...prev,
            vendorInfo: { companyName: '', contactName: '', email: '', phone: '', billingAddress: '', shippingAddress: '', billingCity: '', billingState: '', billingZipCode: '', shippingCity: '', shippingState: '', shippingZipCode: '' }
        }));
        setInitialVendorInfoAfterAutofill(null);
        setIsAutofilledVendorActive(false);
        setIsEditingAutofilledVendor(false);
    };

    return (
        <>
            {showUnsavedChangesModal && (
                <UnsavedChangesModal
                    onCancel={() => setShowUnsavedChangesModal(false)}
                    onDelete={() => {
                        setShowUnsavedChangesModal(false);
                        navigateTo('dashboard');
                    }}
                    onSaveAndClose={() => {
                        setShowUnsavedChangesModal(false);
                        handleSaveDraft();
                    }}
                />
            )}
            <div className="mb-6"><button onClick={handleReturnToDashboard} className="text-orange-600 hover:text-orange-800 font-semibold">&larr; Back to Dashboard</button><h1 className="text-3xl font-bold text-slate-800 mt-2">{order ? `Order Details: ${order.id}` : 'New Purchase Order'}</h1>{order && <StatusBar status={formData.status} statusHistory={formData.statusHistory} />}</div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <div className="lg:col-span-2 space-y-6">
                    <div className="relative">
                        <div className={`bg-white p-6 rounded-lg shadow-sm border ${isEditingAutofilledVendor ? 'border-red-500 shadow-red-300/50 ring-2 ring-red-500' : 'border-slate-200'}`}>
                            <h2 className="text-xl font-semibold text-slate-700 border-b pb-3 mb-4">Vendor Information</h2>
                            {isEditingAutofilledVendor && (
                                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-center">
                                    <p className="text-sm font-medium text-red-700">You are editing a vendor record.</p>
                                    <div className="mt-3 flex flex-wrap justify-center gap-2">
                                        <button onClick={handleCancelVendorEdit} className="px-3 py-1.5 text-xs bg-gray-500 text-white font-semibold rounded-md hover:bg-gray-600">Cancel</button>
                                        <button onClick={handleSaveVendorEdits} className="px-3 py-1.5 text-xs bg-green-600 text-white font-semibold rounded-md hover:bg-green-700">Save Edits</button>
                                        <button onClick={handleClearAllVendorInputs} className="px-3 py-1.5 text-xs bg-slate-500 text-white font-semibold rounded-md hover:bg-slate-600">Clear All Inputs</button>
                                        <button onClick={handleCreateNewVendorRecord} className="px-3 py-1.5 text-xs bg-blue-600 text-white font-semibold rounded-md hover:bg-blue-700">Create New Record</button>
                                    </div>
                                </div>
                            )}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div className="space-y-4">
                                    <Input label="Company Name" value={formData.vendorInfo.companyName} onChange={e => handleVendorInfoChange('companyName', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                    <Input label="Contact Name" value={formData.vendorInfo.contactName} onChange={e => handleVendorInfoChange('contactName', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                    <Input label="Email" value={formData.vendorInfo.email} onChange={e => handleVendorInfoChange('email', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                    <Input label="Phone" value={formData.vendorInfo.phone} onChange={e => handleVendorInfoChange('phone', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                </div>
                                <div className="space-y-4">
                                    <Input label="Shipping Address" value={formData.vendorInfo.shippingAddress} onChange={e => handleVendorInfoChange('shippingAddress', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Input label="City" value={formData.vendorInfo.shippingCity} onChange={e => handleVendorInfoChange('shippingCity', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                        <Input label="State" value={formData.vendorInfo.shippingState} onChange={e => handleVendorInfoChange('shippingState', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                        <Input label="Zip Code" value={formData.vendorInfo.shippingZipCode} onChange={e => handleVendorInfoChange('shippingZipCode', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                    </div>
                                    <label className="block text-sm font-medium text-slate-600 pt-4">Billing Address</label>
                                    <div className="flex items-center">
                                        <input id="sameAsShipping" type="checkbox" checked={sameAsShipping} onChange={handleSameAsShippingChange} disabled={!canEdit} className="h-4 w-4 text-orange-600 border-slate-300 rounded focus:ring-orange-500 disabled:opacity-50" />
                                        <label htmlFor="sameAsShipping" className="ml-2 block text-sm text-slate-700">Same as Shipping Address</label>
                                    </div>
                                    {!sameAsShipping && (
                                        <div className="space-y-4">
                                            <Input label="Billing Address" value={formData.vendorInfo.billingAddress} onChange={e => handleVendorInfoChange('billingAddress', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <Input label="City" value={formData.vendorInfo.billingCity} onChange={e => handleVendorInfoChange('billingCity', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                                <Input label="State" value={formData.vendorInfo.billingState} onChange={e => handleVendorInfoChange('billingState', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                                <Input label="Zip Code" value={formData.vendorInfo.billingZipCode} onChange={e => handleVendorInfoChange('billingZipCode', e.target.value)} disabled={!canEdit} onFocus={handleVendorInputFocus} onBlur={handleVendorInputBlur} />
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                        {vendorSuggestions.length > 0 && canEdit && isVendorInputActive && (
                            <div className="absolute z-10 w-full mt-1 bg-white border border-slate-300 rounded-lg shadow-lg">
                                <ul className="py-1 max-h-60 overflow-y-auto">
                                    {vendorSuggestions.map(vendor => (
                                        <li key={vendor.id} onClick={() => selectVendor(vendor)} className="px-4 py-2 hover:bg-orange-100 cursor-pointer">
                                            <p className="font-semibold text-slate-800">{vendor.companyName}</p>
                                            <p className="text-sm text-slate-500">{vendor.contactName} - {vendor.email}</p>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                    <div className="bg-white rounded-lg shadow-sm border border-slate-200"><div className="p-6 border-b"><h2 className="text-xl font-semibold text-slate-700 mb-4">Line Items</h2>{canEdit && (<div className="flex gap-2"><input ref={scanInputRef} type="text" value={scanInput} onChange={e => setScanInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { handleScanAddItem(e as any); } }} placeholder="Scan or Enter Item Code" className="flex-grow block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" /><button onClick={handleScanAddItem} className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors">Add</button></div>)}</div><div className="overflow-x-auto"><table className="w-full text-sm text-left text-slate-500"><thead className="text-xs text-slate-700 uppercase bg-slate-100"><tr><th scope="col" className="px-4 py-3">Item</th><th scope="col" className="px-4 py-3">Style</th><th scope="col" className="px-4 py-3 text-center">Qty</th><th scope="col" className="px-4 py-3 text-right">Unit Price</th><th scope="col" className="px-4 py-3 text-right">Total</th>{canEdit && <th scope="col" className="px-2 py-3"></th>}</tr></thead><tbody>{formData.lineItems.map((item, index) => (<tr key={`${item.id}-${index}`} className="border-b align-middle"><td className="px-4 py-2"><Select value={item.item} onChange={e => handleLineItemChange(item.id, 'item', e.target.value)} disabled={!canEdit}><option value="-- Select Item --">-- Select Item --</option>{Object.entries(allSelectableItems).map(([id, details]: [string, any]) => (<option key={id} value={id}>{details.name}</option>))}</Select></td><td className="px-4 py-2"><Select value={item.style} onChange={e => handleLineItemChange(item.id, 'style', e.target.value)} disabled={!canEdit}>{(allSelectableItems[item.item]?.styles || []).map((name: string) => (<option key={name} value={name}>{name}</option>))}</Select></td><td className="px-4 py-2"><input type="number" min="0" value={item.quantity} onChange={e => handleLineItemChange(item.id, 'quantity', Number(e.target.value))} className="w-16 sm:w-20 text-center bg-white border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm p-2" disabled={!canEdit} /></td><td className="px-4 py-2 text-right"><PriceInput value={item.price} onChange={newPrice => handleLineItemChange(item.id, 'price', newPrice)} disabled={!canEdit}/></td><td className="px-4 py-2 text-right font-medium text-slate-800">${((item.quantity * item.price) / 100).toFixed(2)}</td>{canEdit && <td className="px-2 py-2 text-center"><button onClick={() => removeLineItem(item.id)} className="text-slate-400 hover:text-red-600 p-1 rounded-full hover:bg-red-100 transition-colors"><TrashIcon /></button></td>}</tr>))}</tbody></table></div>{canEdit && <div className="p-6 border-t"><button onClick={addEmptyLineItem} className="w-full text-center px-4 py-2 bg-orange-100 text-orange-700 font-semibold rounded-md hover:bg-orange-200 transition-colors">+ Add Line Item</button></div>}</div>
                </div>
                <div className="lg:col-span-1 space-y-6">
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200"><h2 className="text-xl font-semibold text-slate-700 border-b pb-3 mb-4">Order Metadata</h2><div className="space-y-4"><Input label="Estimated Shipping Date" type="date" value={formData.estimatedShippingDate} onChange={e => setFormData(p=>({...p, estimatedShippingDate: e.target.value}))} disabled={!canEdit} /><ScentToggle value={formData.scentOption} onChange={val => setFormData(p=>({...p, scentOption: val}))} disabled={!canEdit} /><NameDropToggle value={formData.nameDrop} onChange={val => setFormData(p=>({...p, nameDrop: val}))} disabled={!canEdit} /></div></div>
                    {!isDraft && (<div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200"><h2 className="text-xl font-semibold text-slate-700 mb-4">Update Status</h2><div className="space-y-2"><button onClick={() => handleStatusChange('Paid')} disabled={formData.status !== 'Sent'} className="w-full text-center px-4 py-2 bg-blue-500 text-white font-semibold rounded-md hover:bg-blue-600 disabled:bg-slate-300 disabled:cursor-not-allowed">Mark as Paid</button><button onClick={() => handleStatusChange('Shipped')} disabled={formData.status !== 'Paid'} className="w-full text-center px-4 py-2 bg-green-500 text-white font-semibold rounded-md hover:bg-green-600 disabled:bg-slate-300 disabled:cursor-not-allowed">Mark as Shipped</button></div></div>)}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                        <h2 className="text-xl font-semibold text-slate-700 mb-3 border-b pb-3">Signature</h2>
                        <SignaturePad 
                            initialDataUrl={formData.signatureDataUrl}
                            onSave={(dataUrl) => setFormData(prev => ({...prev, signatureDataUrl: dataUrl}))}
                            disabled={!canEdit}
                        />
                    </div>
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200"><h2 className="text-xl font-semibold text-slate-700 mb-3">Notes</h2><Textarea label="" value={formData.notes} onChange={e => setFormData(p => ({...p, notes: e.target.value}))} rows={4} disabled={!canEdit} /></div>
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                        <h2 className="text-xl font-semibold text-slate-700 mb-4">Order Summary</h2>
                        <div className="space-y-3">
                            <div className="flex justify-between items-center text-slate-600"><span>Subtotal</span><span className="font-medium">${(orderTotals.subtotal / 100).toFixed(2)}</span></div>
                            {orderTotals.nameDropSurcharge > 0 && (
                                <div className="flex justify-between items-center text-slate-600">
                                    <span>Name Drop Surcharge ({orderTotals.crossItemCount} crosses)</span>
                                    <span className="font-medium">${(orderTotals.nameDropSurcharge / 100).toFixed(2)}</span>
                                </div>
                            )}
                            {orderTotals.estimatedShipping > 0 && (
                                <div className="flex justify-between items-center text-slate-600">
                                    <span>Est. Shipping</span>
                                    <span className="font-medium">${orderTotals.estimatedShipping.toFixed(2)}</span>
                                </div>
                            )}
                            <div className="flex justify-between items-center font-bold text-xl text-slate-800"><span>Total</span><span>${(orderTotals.total / 100).toFixed(2)}</span></div>
                        </div>
                    </div>
                    <div className="space-y-3">
                        {isDraft && <button onClick={handleSaveAndSend} className="w-full text-center px-6 py-3 bg-orange-600 text-white font-bold rounded-md hover:bg-orange-700">Save and Send</button>}
                        {!isDraft && !isEditing && <button onClick={() => setOrderForEmailModal(formData)} className="w-full text-center px-6 py-3 bg-orange-600 text-white font-bold rounded-md hover:bg-orange-700">Resend Email</button>}
                        {isDraft && <button onClick={handleSaveDraft} className="w-full text-center px-6 py-3 bg-slate-600 text-white font-bold rounded-md hover:bg-slate-700">Save as Draft</button>}
                        {isEditing && !isDraft && <button onClick={() => {saveOrder(createOrderObject()); setIsEditing(false);}} className="w-full text-center px-6 py-3 bg-slate-600 text-white font-bold rounded-md hover:bg-slate-700">Save Changes</button>}
                        <button onClick={handlePreviewPdf} className="w-full text-center px-6 py-3 bg-white text-slate-700 font-bold rounded-md hover:bg-slate-100 border border-slate-300">Preview PDF</button>
                        {!isDraft && !isEditing && <button onClick={() => setIsEditing(true)} className="w-full text-center px-6 py-3 bg-blue-100 text-blue-700 font-bold rounded-md hover:bg-blue-200">Edit Order</button>}
                        {formData.id && (
                            <button 
                                onClick={handleDelete} 
                                className="w-full text-center px-6 py-3 bg-red-100 text-red-700 font-bold rounded-md hover:bg-red-200"
                            >
                                {isDraft ? "Delete Draft" : "Delete Order"}
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </>
    );
};
