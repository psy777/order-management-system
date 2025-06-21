import React, { useState, useEffect, FormEvent } from 'react';

interface Customer {
    id: string;
    companyName: string;
    contactName?: string;
    email?: string;
    phone?: string;
    shippingAddress?: string;
    shippingCity?: string;
    shippingState?: string;
    shippingZipCode?: string;
    billingAddress?: string;
    billingCity?: string;
    billingState?: string;
    billingZipCode?: string;
}

const ManageCustomersPage = () => {
    const [customers, setCustomers] = useState<Customer[]>([]);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [modalTitle, setModalTitle] = useState('');
    const [currentCustomer, setCurrentCustomer] = useState<Partial<Customer>>({});
    const [isSameAsShipping, setIsSameAsShipping] = useState(true);

    useEffect(() => {
        loadCustomers();
    }, []);

    const loadCustomers = async () => {
        try {
            const response = await fetch('/api/vendors');
            const data = await response.json();
            setCustomers(data);
        } catch (error) {
            console.error('Error loading customers:', error);
        }
    };

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const { id, value } = e.target;
        setCurrentCustomer(prev => ({ ...prev, [id]: value }));
    };

    const prepareAddCustomerModal = () => {
        setModalTitle('Add New Customer');
        setCurrentCustomer({});
        setIsSameAsShipping(true);
        setIsModalOpen(true);
    };

    const prepareEditCustomerModal = (customer: Customer) => {
        setModalTitle('Edit Customer');
        const sameAsShipping = customer.shippingAddress === customer.billingAddress &&
                             customer.shippingCity === customer.billingCity &&
                             customer.shippingState === customer.billingState &&
                             customer.shippingZipCode === customer.billingZipCode;
        setIsSameAsShipping(sameAsShipping);
        setCurrentCustomer(customer);
        setIsModalOpen(true);
    };

    const saveCustomer = async () => {
        if (!currentCustomer.companyName) {
            alert('Company Name is required.');
            return;
        }

        const customerData = { ...currentCustomer };
        if (isSameAsShipping) {
            customerData.billingAddress = customerData.shippingAddress;
            customerData.billingCity = customerData.shippingCity;
            customerData.billingState = customerData.shippingState;
            customerData.billingZipCode = customerData.shippingZipCode;
        }

        const url = customerData.id ? `/api/vendors/${customerData.id}` : '/api/vendors';
        const method = customerData.id ? 'PUT' : 'POST';

        try {
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(customerData),
            });
            const result = await response.json();
            if (response.ok) {
                setIsModalOpen(false);
                loadCustomers();
                alert(result.message || 'Customer saved successfully!');
            } else {
                alert('Error saving customer: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error saving customer:', error);
            alert('Error saving customer. See console for details.');
        }
    };

    const deleteCustomer = async (customerId: string) => {
        if (!confirm('Are you sure you want to delete this customer? This action cannot be undone.')) {
            return;
        }

        try {
            const response = await fetch(`/api/vendors/${customerId}`, { method: 'DELETE' });
            const result = await response.json();
            if (response.ok) {
                loadCustomers();
                alert(result.message || 'Customer deleted successfully!');
            } else {
                alert('Error deleting customer: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error deleting customer:', error);
            alert('Error deleting customer. See console for details.');
        }
    };

    const handleImportCsv = async (e: FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        try {
            const response = await fetch('/api/import-customers-csv', {
                method: 'POST',
                body: formData,
            });
            if (response.ok) {
                alert('CSV imported successfully!');
                loadCustomers();
            } else {
                const result = await response.json();
                alert(`Error importing CSV: ${result.message || 'Unknown error'}`);
            }
        } catch (error) {
            console.error('Error importing CSV:', error);
            alert('Error importing CSV. See console for details.');
        }
    };

    return (
        <div>
            <h1 className="text-3xl font-bold text-slate-800">Manage Customers</h1>
            <div className="mt-6 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4">
                    <button type="button" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow" onClick={prepareAddCustomerModal}>
                        + Add New Customer
                    </button>
                    <form onSubmit={handleImportCsv} className="flex items-center gap-3">
                        <label htmlFor="csv_file" className="text-sm font-medium text-slate-600">Import from CSV:</label>
                        <input type="file" className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-orange-50 file:text-orange-700 hover:file:bg-orange-100" name="csv_file" id="csv_file" accept=".csv" required />
                        <button type="submit" className="px-4 py-2 bg-slate-600 text-white font-semibold rounded-md hover:bg-slate-700 transition-colors text-sm">Import</button>
                    </form>
                </div>
                <div className="mt-6 overflow-x-auto">
                    <table className="w-full text-sm text-left text-slate-500">
                        <thead className="text-xs text-slate-700 uppercase bg-slate-100">
                            <tr>
                                <th className="px-4 py-3">Company Name</th>
                                <th className="px-4 py-3">Contact Name</th>
                                <th className="px-4 py-3">Email</th>
                                <th className="px-4 py-3">Phone</th>
                                <th className="px-4 py-3 text-center">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {customers.map(customer => (
                                <tr key={customer.id} className="bg-white border-b hover:bg-slate-50">
                                    <td className="px-4 py-3 font-medium text-slate-800">{customer.companyName || 'N/A'}</td>
                                    <td className="px-4 py-3">{customer.contactName || 'N/A'}</td>
                                    <td className="px-4 py-3">{customer.email || 'N/A'}</td>
                                    <td className="px-4 py-3">{customer.phone || 'N/A'}</td>
                                    <td className="px-4 py-3 text-center">
                                        <div className="flex items-center justify-center space-x-2">
                                            <button className="p-1 text-slate-500 hover:text-orange-600" onClick={() => prepareEditCustomerModal(customer)}>
                                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" /><path fillRule="evenodd" d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clipRule="evenodd" /></svg>
                                            </button>
                                            <button className="p-1 text-slate-500 hover:text-red-600" onClick={() => deleteCustomer(customer.id)}>
                                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm4 0a1 1 0 012 0v6a1 1 0 11-2 0V8z" clipRule="evenodd" /></svg>
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {isModalOpen && (
                <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex justify-center items-center p-4">
                    <div className="bg-white rounded-lg shadow-xl p-6 sm:p-8 w-full max-w-3xl max-h-[90vh] overflow-y-auto">
                        <div className="flex justify-between items-center pb-4 border-b border-slate-200">
                            <h5 className="text-xl font-bold text-slate-800">{modalTitle}</h5>
                            <button type="button" className="p-1 text-slate-400 hover:text-slate-600" onClick={() => setIsModalOpen(false)}>
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
                            </button>
                        </div>
                        <div className="mt-6">
                            <form className="space-y-4">
                                <div>
                                    <label htmlFor="companyName" className="block text-sm font-medium text-slate-600">Company Name</label>
                                    <input type="text" id="companyName" value={currentCustomer.companyName || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" required />
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                    <div>
                                        <label htmlFor="contactName" className="block text-sm font-medium text-slate-600">Contact Name</label>
                                        <input type="text" id="contactName" value={currentCustomer.contactName || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                    </div>
                                    <div>
                                        <label htmlFor="phone" className="block text-sm font-medium text-slate-600">Phone</label>
                                        <input type="tel" id="phone" value={currentCustomer.phone || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                    </div>
                                </div>
                                <div>
                                    <label htmlFor="email" className="block text-sm font-medium text-slate-600">Email</label>
                                    <input type="email" id="email" value={currentCustomer.email || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                </div>
                                
                                <h3 className="text-lg font-semibold text-slate-700 pt-4 border-t border-slate-200">Shipping Address</h3>
                                <div>
                                    <label htmlFor="shippingAddress" className="block text-sm font-medium text-slate-600">Address</label>
                                    <input type="text" id="shippingAddress" value={currentCustomer.shippingAddress || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                    <div>
                                        <label htmlFor="shippingCity" className="block text-sm font-medium text-slate-600">City</label>
                                        <input type="text" id="shippingCity" value={currentCustomer.shippingCity || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                    </div>
                                    <div>
                                        <label htmlFor="shippingState" className="block text-sm font-medium text-slate-600">State</label>
                                        <input type="text" id="shippingState" value={currentCustomer.shippingState || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                    </div>
                                    <div>
                                        <label htmlFor="shippingZipCode" className="block text-sm font-medium text-slate-600">Zip Code</label>
                                        <input type="text" id="shippingZipCode" value={currentCustomer.shippingZipCode || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                    </div>
                                </div>

                                <h3 className="text-lg font-semibold text-slate-700 pt-4 border-t border-slate-200">Billing Address</h3>
                                <div className="flex items-center">
                                    <input type="checkbox" id="sameAsShipping" checked={isSameAsShipping} onChange={(e) => setIsSameAsShipping(e.target.checked)} className="h-4 w-4 text-orange-600 border-slate-300 rounded focus:ring-orange-500" />
                                    <label htmlFor="sameAsShipping" className="ml-2 block text-sm text-slate-700">Billing address is the same as shipping</label>
                                </div>

                                {!isSameAsShipping && (
                                    <div className="space-y-4">
                                        <div>
                                            <label htmlFor="billingAddress" className="block text-sm font-medium text-slate-600">Address</label>
                                            <input type="text" id="billingAddress" value={currentCustomer.billingAddress || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                        </div>
                                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                            <div>
                                                <label htmlFor="billingCity" className="block text-sm font-medium text-slate-600">City</label>
                                                <input type="text" id="billingCity" value={currentCustomer.billingCity || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                            </div>
                                            <div>
                                                <label htmlFor="billingState" className="block text-sm font-medium text-slate-600">State</label>
                                                <input type="text" id="billingState" value={currentCustomer.billingState || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                            </div>
                                            <div>
                                                <label htmlFor="billingZipCode" className="block text-sm font-medium text-slate-600">Zip Code</label>
                                                <input type="text" id="billingZipCode" value={currentCustomer.billingZipCode || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </form>
                        </div>
                        <div className="mt-6 pt-4 border-t border-slate-200 flex justify-end space-x-3">
                            <button type="button" className="px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors" onClick={() => setIsModalOpen(false)}>Close</button>
                            <button type="button" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors" onClick={saveCustomer}>Save Customer</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ManageCustomersPage;
