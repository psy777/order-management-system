import React, { useState, useEffect } from 'react';

interface Item {
    id: string;
    item_code: string;
    name: string;
    price: number;
    type: string;
    weight_oz: number | null;
    styles: string[];
}

interface ItemFormState {
    id?: string;
    item_code?: string;
    name?: string;
    price?: string;
    type?: string;
    weight_oz?: string;
    styles?: string;
}

const ManageItemsPage = () => {
    const [items, setItems] = useState<Item[]>([]);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [modalTitle, setModalTitle] = useState('');
    const [currentItem, setCurrentItem] = useState<ItemFormState>({});

    useEffect(() => {
        loadItems();
    }, []);

    const loadItems = async () => {
        try {
            const response = await fetch('/api/items');
            const data = await response.json();
            setItems(data);
        } catch (error) {
            console.error('Error loading items:', error);
        }
    };

    const prepareAddItemModal = () => {
        setModalTitle('Add New Item');
        setCurrentItem({});
        setIsModalOpen(true);
    };

    const prepareEditItemModal = (itemId: string) => {
        const item = items.find(i => i.id === itemId);
        if (item) {
            setModalTitle('Edit Item');
            setCurrentItem({
                ...item,
                price: (item.price / 100).toFixed(2),
                weight_oz: item.weight_oz?.toString() ?? '',
                styles: item.styles.join(', ')
            });
            setIsModalOpen(true);
        }
    };

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
        const { id, value } = e.target;
        setCurrentItem(prev => ({ ...prev, [id]: value }));
    };

    const saveItem = async () => {
        const { id, item_code, name, price, type, weight_oz, styles } = currentItem;

        if (!item_code || !name || price === undefined) {
            alert('Item Code, Name, and a valid Price are required.');
            return;
        }

        const price_in_cents = Math.round(parseFloat(price) * 100);

        const itemData = {
            id,
            item_code,
            name,
            price: price_in_cents,
            type,
            weight_oz: weight_oz ? parseFloat(weight_oz) : null,
            styles: styles ? styles.split(',').map(s => s.trim()).filter(s => s) : [],
        };

        const url = id ? `/api/items/${currentItem.item_code}` : '/api/items';
        const method = id ? 'PUT' : 'POST';

        try {
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(itemData),
            });
            const result = await response.json();
            if (response.ok) {
                setIsModalOpen(false);
                loadItems();
                alert(result.message || 'Item saved successfully!');
            } else {
                alert('Error saving item: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error saving item:', error);
            alert('Error saving item. See console for details.');
        }
    };

    const deleteItem = async (item_code: string) => {
        if (!confirm('Are you sure you want to delete this item?')) return;

        try {
            const response = await fetch(`/api/items/${item_code}`, { method: 'DELETE' });
            const result = await response.json();
            if (response.ok) {
                loadItems();
                alert(result.message || 'Item deleted successfully!');
            } else {
                alert('Error deleting item: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error deleting item:', error);
            alert('Error deleting item. See console for details.');
        }
    };

    return (
        <>
            <h1 className="text-3xl font-bold text-slate-800">Manage Items</h1>
            <div className="mt-6 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4">
                    <button type="button" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow" onClick={prepareAddItemModal}>
                        + Add New Item
                    </button>
                    <form action="/api/import-items-csv" method="post" encType="multipart/form-data" className="flex items-center gap-3">
                        <label htmlFor="csv_file" className="text-sm font-medium text-slate-600">Import from CSV:</label>
                        <input type="file" name="csv_file" id="csv_file" className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-orange-50 file:text-orange-700 hover:file:bg-orange-100" accept=".csv" required />
                        <button type="submit" className="px-4 py-2 bg-slate-600 text-white font-semibold rounded-md hover:bg-slate-700 transition-colors text-sm">Import</button>
                    </form>
                </div>

                <div className="mt-6 overflow-x-auto">
                    <table className="w-full text-sm text-left text-slate-500">
                        <thead className="text-xs text-slate-700 uppercase bg-slate-100">
                            <tr>
                                <th className="px-4 py-3">Item Code</th>
                                <th className="px-4 py-3">Name</th>
                                <th className="px-4 py-3">Type</th>
                                <th className="px-4 py-3">Price</th>
                                <th className="px-4 py-3">Styles</th>
                                <th className="px-4 py-3">Weight (oz)</th>
                                <th className="px-4 py-3 text-center">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(item => (
                                <tr key={item.id} className="bg-white border-b hover:bg-slate-50">
                                    <td className="px-4 py-3 font-medium text-slate-800">{item.item_code || 'N/A'}</td>
                                    <td className="px-4 py-3">{item.name || 'N/A'}</td>
                                    <td className="px-4 py-3">{item.type || 'N/A'}</td>
                                    <td className="px-4 py-3">${(item.price / 100).toFixed(2)}</td>
                                    <td className="px-4 py-3">{item.styles.join(', ')}</td>
                                    <td className="px-4 py-3">{item.weight_oz}</td>
                                    <td className="px-4 py-3 text-center">
                                        <div className="flex items-center justify-center space-x-2">
                                            <button className="p-1 text-slate-500 hover:text-orange-600" onClick={() => prepareEditItemModal(item.id)}>
                                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" /><path fillRule="evenodd" d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clipRule="evenodd" /></svg>
                                            </button>
                                            <button className="p-1 text-slate-500 hover:text-red-600" onClick={() => deleteItem(item.item_code)}>
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
                    <div className="bg-white rounded-lg shadow-xl p-6 sm:p-8 w-full max-w-xl max-h-[90vh] overflow-y-auto" role="document">
                        <div className="flex justify-between items-center pb-4 border-b border-slate-200">
                            <h5 className="text-xl font-bold text-slate-800">{modalTitle}</h5>
                            <button type="button" className="p-1 text-slate-400 hover:text-slate-600" onClick={() => setIsModalOpen(false)} aria-label="Close">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
                            </button>
                        </div>
                        <div className="mt-6">
                            <form className="space-y-4">
                                <input type="hidden" id="id" value={currentItem.id || ''} />
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                    <div>
                                        <label htmlFor="item_code" className="block text-sm font-medium text-slate-600">Item Code</label>
                                        <input type="text" className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="item_code" value={currentItem.item_code || ''} onChange={handleInputChange} required />
                                    </div>
                                    <div>
                                        <label htmlFor="name" className="block text-sm font-medium text-slate-600">Name</label>
                                        <input type="text" className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="name" value={currentItem.name || ''} onChange={handleInputChange} required />
                                    </div>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                    <div>
                                        <label htmlFor="price" className="block text-sm font-medium text-slate-600">Price ($)</label>
                                        <input type="number" className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="price" value={currentItem.price || ''} onChange={handleInputChange} required min="0" step="0.01" />
                                    </div>
                                    <div>
                                        <label htmlFor="weight_oz" className="block text-sm font-medium text-slate-600">Weight (oz)</label>
                                        <input type="number" className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="weight_oz" value={currentItem.weight_oz || ''} onChange={handleInputChange} min="0" step="0.1" />
                                    </div>
                                </div>
                                <div>
                                    <label htmlFor="type" className="block text-sm font-medium text-slate-600">Type</label>
                                    <select className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="type" value={currentItem.type || 'cross'} onChange={handleInputChange}>
                                        <option value="cross">Cross</option>
                                        <option value="display">Display</option>
                                        <option value="other">Other</option>
                                    </select>
                                </div>
                                <div>
                                    <label htmlFor="styles" className="block text-sm font-medium text-slate-600">Styles (comma-separated)</label>
                                    <input type="text" className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" id="styles" value={currentItem.styles || ''} onChange={handleInputChange} />
                                </div>
                            </form>
                        </div>
                        <div className="mt-6 pt-4 border-t border-slate-200 flex justify-end space-x-3">
                            <button type="button" className="px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors" onClick={() => setIsModalOpen(false)}>Close</button>
                            <button type="button" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors" onClick={saveItem}>Save Item</button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default ManageItemsPage;
